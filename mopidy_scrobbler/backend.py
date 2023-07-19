import json
import re

import pykka
import pylast
from mopidy import backend, httpclient
from mopidy.models import Ref
from mopidy_tubeify.data import flatten
from mopidy_tubeify.yt_matcher import (
    search_and_get_best_albums,
    search_and_get_best_match,
)
from pylast import Album, LovedTrack, PlayedTrack, TopItem, Track
from ytmusicapi import YTMusic

from mopidy_scrobbler import Extension, logger
from mopidy_scrobbler.frontend import API_KEY, API_SECRET, PYLAST_ERRORS


class ScrobblerBackend(pykka.ThreadingActor, backend.Backend):
    def __init__(self, config, audio):
        super().__init__()
        self.config = config
        self.library = ScrobblerLibraryProvider(backend=self)
        self.username = self.config["scrobbler"]["username"]
        self.password = self.config["scrobbler"]["password"]
        self.scrobbler_users = [self.config["scrobbler"]["username"]] + list(
            self.config["scrobbler"]["scrobbler_users"]
        )

        self.uri_schemes = ["scrobbler"]
        self.user_agent = "{}/{}".format(Extension.dist_name, Extension.version)

    def on_start(self):
        proxy = httpclient.format_proxy(self.config["proxy"])

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 6.1) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/80.0.3987.149 Safari/537.36 "
                f"{httpclient.format_user_agent(self.user_agent)}"
            )
        }

        self.ytmusic = YTMusic()

        try:
            self.lastfm = pylast.LastFMNetwork(
                api_key=API_KEY,
                api_secret=API_SECRET,
                username=self.username,
                password_hash=pylast.md5(self.password),
            )
            logger.info("Scrobbler connected to Last.fm")
        except PYLAST_ERRORS as exc:
            logger.error(f"Error during Last.fm setup: {exc}")
            self.stop()


class ScrobblerLibraryProvider(backend.LibraryProvider):

    """
    Called when root_directory is set to [insert description]
    When enabled makes possible to browse the users listed in
    config and to browse their public playlists and the
    separate tracks those playlists.
    """

    root_directory = Ref.directory(uri="scrobbler:browse", name="last.fm")

    self_kinds = []

    user_kinds = {
        "get_loved_tracks": {"limit": 20},
        # "get_now_playing":{},
        "get_recent_tracks": {"limit": 20},
        # "get_tagged_albums":{"limit":20},
        # "get_tagged_artists":{"limit":20},
        # "get_tagged_tracks":{"limit":20},
        "get_top_albums": {"limit": 20, "period": "PERIOD_1MONTH"},
        "get_top_artists": {"limit": 20, "period": "PERIOD_1MONTH"},
        "get_top_tags": {"limit": 20},
        "get_top_tracks": {"limit": 20, "period": "PERIOD_1MONTH"},
    }

    # add cache?

    def browse(self, uri):
        if uri == "scrobbler:browse":
            userrefs = [
                Ref.directory(
                    uri=f"scrobbler:{user}:root",
                    name=user,
                )
                for user in self.backend.scrobbler_users
            ]

            return sorted(userrefs, key=lambda x: x.name.lower())

        match = re.match(r"scrobbler:(?P<user>.+):(?P<kind>.+)$", uri)

        if match and match["kind"] == "root":
            directoryrefs = []
            for kind in self.user_kinds.keys():
                directoryrefs.append(
                    Ref.directory(
                        uri=f"scrobbler:{match['user']}:{kind}",
                        name=f"{match['user']}, {kind}",
                    )
                )

            return directoryrefs

        else:
            directoryrefs = []
            user_object = self.backend.lastfm.get_user(match["user"])

            get_pylast_object_method = getattr(
                user_object,
                match["kind"],
                None,
            )

            pylast_object = get_pylast_object_method(
                **self.user_kinds[match["kind"]]
            )
            
            scrobbled_items = []
            for scrobbled_item in pylast_object:
                if type(scrobbled_item) == TopItem:
                    scrobbled_items.append(scrobbled_item.item)
                if type(scrobbled_item) in [PlayedTrack, LovedTrack]:
                    scrobbled_items.append(scrobbled_item.track)

            tracks = [
                {
                    "song_name": scrobbled_item.title,
                    "song_artists": [scrobbled_item.artist.get_name()],
                    "song_duration": 0,
                    "isrc": None,
                }
                for scrobbled_item in scrobbled_items
                if type(scrobbled_item) == Track
            ]

            logger.debug(
                f"total tracks for {match['user']}, {match['kind']}: {len(tracks)}"
            )

            matched_tracks = search_and_get_best_match(
                tracks, self.backend.ytmusic
            )

            albums = [
                (
                    [scrobbled_item.artist.get_name()],
                    scrobbled_item.title,
                )
                for scrobbled_item in scrobbled_items
                if type(scrobbled_item) == Album
            ]

            logger.debug(
                f"total albums for {match['user']}, {match['kind']}: {len(albums)}"
            )

            albums_to_return = search_and_get_best_albums(
                [album for album in albums if album[1]], self.backend.ytmusic
            )

            matched_albums = list(flatten(albums_to_return))

            good_tracks = []
            good_albums = []
            trackrefs = []

            if matched_tracks:
                good_tracks = [
                    track
                    for track in matched_tracks
                    if "videoId" in track
                    and track["videoId"]
                    and "title" in track
                    and track["title"]
                ]

            if matched_albums:
                good_albums = [
                    album
                    for album in matched_albums
                    if "type" in album
                    and album["type"] == "Album"
                    and album["browseId"]
                    and "title" in album
                    and album["title"]
                    and "artists" in album
                    and album["artists"]
                ]

            if good_tracks:
                trackrefs.extend(
                    [
                        Ref.track(
                            uri=f"yt:video:{track['videoId']}",
                            name=track["title"],
                        )
                        for track in good_tracks
                        # if "videoId" in track and track["videoId"]
                    ]
                )

                # include ytmusic data for all tracks as preload data in the uri
                # for the first track.  There is surely a better way to do this.
                # It breaks the first track in the musicbox_webclient
                first_track = [
                    track
                    for track in good_tracks
                    if f"yt:video:{track['videoId']}" == trackrefs[0].uri
                ][0]

                trackrefs[0] = Ref.track(
                    uri=(
                        f"yt:video:{first_track['videoId']}"
                        f":preload:"
                        f"{json.dumps([track for track in good_tracks if track is not None])}"
                    ),
                    name=first_track["title"],
                )

            if good_albums:
                trackrefs.extend(
                    [
                        Ref.album(
                            uri=f"yt:playlist:{album['browseId']}",
                            name=f"{', '.join([artist['name'] for artist in album['artists']])}, '{album['title']}'",
                        )
                        for album in good_albums
                    ]
                )

            return trackrefs
