import logging
import pathlib

import pkg_resources
from mopidy import config, ext

__version__ = pkg_resources.get_distribution("Mopidy-Scrobbler").version

logger = logging.getLogger(__name__)


class Extension(ext.Extension):
    dist_name = "Mopidy-Scrobbler"
    ext_name = "scrobbler"
    version = __version__

    def get_default_config(self):
        return config.read(pathlib.Path(__file__).parent / "ext.conf")

    def get_config_schema(self):
        schema = super().get_config_schema()
        schema["username"] = config.String()
        schema["password"] = config.Secret()
        schema["scrobbler_users"] = config.List()
        return schema

    def setup(self, registry):
        from .frontend import ScrobblerFrontend

        registry.add("frontend", ScrobblerFrontend)

        from .backend import ScrobblerBackend

        registry.add("backend", ScrobblerBackend)
