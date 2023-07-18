import json
import re

import pykka
import pylast
from mopidy import backend, httpclient
from mopidy.models import Ref
from mopidy_tubeify.yt_matcher import search_and_get_best_match
from pylast import PlayedTrack, Track
from ytmusicapi import YTMusic

from mopidy_scrobbler import Extension, logger
from mopidy_scrobbler.frontend import API_KEY, API_SECRET, PYLAST_ERRORS


class ScrobblerBackend(pykka.ThreadingActor, backend.Backend):
    def __init__(self, config, audio):
        super().__init__()
        self.config = config
        self.username = self.config["scrobbler"]["username"]
        self.library = ScrobblerLibraryProvider(backend=self)
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
                password_hash=pylast.md5(self.config["scrobbler"]["password"]),
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

    # add cache?

    def browse(self, uri):
        if uri == "scrobbler:browse":
            directoryrefs = [
                Ref.directory(
                    uri="scrobbler:self:lastplayed",
                    name="Last 20 tracks scrobbled",
                ),
                Ref.directory(
                    uri="scrobbler:self:loved", name="Last 20 loved tracks"
                ),
            ]
            return directoryrefs

        match = re.match(r"scrobbler:(?P<service>.+):(?P<kind>.+)$", uri)
        if match:
            if match["service"] == "self":
                if match["kind"] == "lastplayed":
                    recent_tracks = self.backend.lastfm.get_user(
                        self.backend.username
                    ).get_recent_tracks(limit=20)

                    tracks = [
                        {
                            "song_name": recent_track.track.title,
                            "song_artists": [
                                recent_track.track.artist.get_name()
                            ],
                            "song_duration": 0,
                            "isrc": None,
                        }
                        for recent_track in recent_tracks
                    ]

                    logger.debug(
                        f"total tracks for {match['service']}, {match['kind']}: {len(tracks)}"
                    )

                    matched_tracks = search_and_get_best_match(
                        tracks, self.backend.ytmusic
                    )

                    good_tracks = []
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
                            if f"yt:video:{track['videoId']}"
                            == trackrefs[0].uri
                        ][0]

                        trackrefs[0] = Ref.track(
                            uri=(
                                f"yt:video:{first_track['videoId']}"
                                f":preload:"
                                f"{json.dumps([track for track in good_tracks if track is not None])}"
                            ),
                            name=first_track["title"],
                        )

                    return trackrefs

                elif match["kind"] == "loved":
                    pass
