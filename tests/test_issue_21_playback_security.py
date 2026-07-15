import ast
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from urllib.parse import urlencode, urlparse
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]
PLAYER = ROOT / 'resources' / 'lib' / 'twitch_addon' / 'addon' / 'player.py'
PLAY = ROOT / 'resources' / 'lib' / 'twitch_addon' / 'routes' / 'play.py'
UTILS = ROOT / 'resources' / 'lib' / 'twitch_addon' / 'addon' / 'utils.py'


class _Names(object):
    def __getattr__(self, name):
        return name.lower()


class _Item(object):
    def __init__(self, item_dict=None):
        self.item_dict = item_dict or {}
        self.properties = {}

    def setProperty(self, key, value):
        self.properties[key] = value

    def addStreamInfo(self, stream_type, info):
        pass

    def setContentLookup(self, enabled):
        pass

    def setMimeType(self, mime_type):
        pass


class _Window(object):
    def __init__(self):
        self.properties = {}

    def clearProperty(self, key):
        self.properties.pop(key, None)

    def getProperty(self, key):
        return self.properties.get(key, '')

    def setProperty(self, key, value):
        self.properties[key] = value


class _Dialog(object):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def is_canceled(self):
        return False

    def update(self, *args, **kwargs):
        pass


class _Monitor(object):
    def abortRequested(self):
        return False

    def waitForAbort(self, timeout):
        return False


class _XbmcPlayer(object):
    def play(self, path, item):
        self.played_path = path
        self.played_item = item

    def getPlayingFile(self):
        return ''


class _Converter(object):
    def __init__(self, line_length):
        pass

    def stream_to_playitem(self, result, id_only=False):
        return {'path': '', 'info': {}}

    def get_video_for_quality(self, videos, ask=False, quality=None, clip=False):
        return videos[0]


class _Api(object):
    access_token = ''

    def __init__(self, quality='Adaptive'):
        self.quality = quality

    def get_channel_stream(self, channel_id):
        return {'data': [{
            'user_name': 'Channel',
            'user_login': 'channel',
            'user_id': channel_id,
        }]}

    def get_live(self, name):
        return [{
            'id': 'hls' if self.quality == 'Adaptive' else 'chunked',
            'name': self.quality,
            'url': 'https://video.example/channel/index.m3u8',
        }]

    def live_request(self, name):
        return {
            'url': 'https://usher.example/channel/index.m3u8?token=token&sig=signature',
            'headers': {},
        }


def _install_property_builder(utils, kodi, log_utils):
    tree = ast.parse(UTILS.read_text())
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == 'set_inputstream_adaptive_properties'
    ]
    if not functions:
        return
    namespace = {
        '__name__': 'twitch_addon.addon.utils',
        '__package__': 'twitch_addon.addon',
        'kodi': kodi,
        'log_utils': log_utils,
        'get_proxy_dict': utils.get_proxy_dict,
        'urlparse': urlparse,
    }
    module = ast.Module(body=functions, type_ignores=[])
    exec(compile(module, str(UTILS), 'exec'), namespace)
    utils.set_inputstream_adaptive_properties = namespace[
        'set_inputstream_adaptive_properties'
    ]


def load_playback_modules(kodi_major=20):
    for name in (
        'twitch_addon', 'twitch_addon.addon', 'twitch_addon.addon.common',
        'twitch_addon.routes',
    ):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    window = _Window()
    settings = {'live_reconnect': 'true'}
    kodi = types.ModuleType('twitch_addon.addon.common.kodi')
    kodi.get_id = lambda: 'plugin.video.twitch'
    kodi.get_name = lambda: 'Twitch'
    kodi.get_setting = lambda name: settings.get(name, '')
    kodi.get_kodi_version = lambda: types.SimpleNamespace(major=kodi_major)
    kodi.get_current_window_dialog_id = lambda: 9999
    kodi.Window = lambda window_id: window
    kodi.ProgressDialog = lambda *args, **kwargs: _Dialog()
    kodi.create_item = lambda item_dict, add=False: _Item(item_dict)
    kodi.ListItem = _Item
    kodi.Player = _XbmcPlayer
    kodi.set_resolved_url = MagicMock()

    log_utils = types.ModuleType('twitch_addon.addon.common.log_utils')
    log_utils.LOGDEBUG = 0
    log_utils.LOGINFO = 1
    log_utils.LOGERROR = 2
    log_utils.LOGWARNING = 3
    log_utils.log = MagicMock()

    common = sys.modules['twitch_addon.addon.common']
    common.kodi = kodi
    common.log_utils = log_utils
    sys.modules[kodi.__name__] = kodi
    sys.modules[log_utils.__name__] = log_utils

    utils = types.ModuleType('twitch_addon.addon.utils')
    utils.use_inputstream_adaptive = lambda: True
    utils.append_headers = lambda headers: '|%s' % urlencode(headers) if headers else ''
    utils.get_proxy_dict = lambda: {
        'http': (
            'http://ISSUE21_PROXY_USER_SENTINEL:'
            'ISSUE21_PROXY_PASSWORD_SENTINEL@proxy.example:8181'
        ),
        'https': (
            'http://ISSUE21_PROXY_USER_SENTINEL:'
            'ISSUE21_PROXY_PASSWORD_SENTINEL@proxy.example:8181'
        ),
    }
    utils.get_default_quality = lambda content_type, target_id: None
    utils.get_watch_history_size = lambda: 0
    utils.irc_enabled = lambda: False
    utils.i18n = lambda value: '%s'
    _install_property_builder(utils, kodi, log_utils)
    sys.modules[utils.__name__] = utils

    constants = types.ModuleType('twitch_addon.addon.constants')
    constants.Keys = _Names()
    constants.LINE_LENGTH = 80
    sys.modules[constants.__name__] = constants

    converter = types.ModuleType('twitch_addon.addon.converter')
    converter.JsonListItemConverter = _Converter
    sys.modules[converter.__name__] = converter

    exceptions = types.ModuleType('twitch_addon.addon.twitch_exceptions')
    for name in ('PlaybackFailed', 'SubRequired', 'TwitchException'):
        setattr(exceptions, name, type(name, (Exception,), {}))
    sys.modules[exceptions.__name__] = exceptions

    history = types.ModuleType('twitch_addon.addon.watch_history')
    history.get_watch_history = MagicMock()
    sys.modules[history.__name__] = history

    cache = types.ModuleType('twitch_addon.addon.cache')
    cache.reset_cache = MagicMock()
    sys.modules[cache.__name__] = cache

    api_module = types.ModuleType('twitch_addon.addon.api')
    api_module.Twitch = _Api
    sys.modules[api_module.__name__] = api_module

    xbmc = types.ModuleType('xbmc')
    xbmc.Player = _XbmcPlayer
    xbmc.Monitor = _Monitor
    xbmc.executebuiltin = MagicMock()
    sys.modules[xbmc.__name__] = xbmc

    play_spec = importlib.util.spec_from_file_location(
        'twitch_addon.routes.play', PLAY
    )
    play = importlib.util.module_from_spec(play_spec)
    sys.modules[play_spec.name] = play
    play_spec.loader.exec_module(play)

    player_spec = importlib.util.spec_from_file_location(
        'twitch_addon.addon.player', PLAYER
    )
    player = importlib.util.module_from_spec(player_spec)
    sys.modules[player_spec.name] = player
    player_spec.loader.exec_module(player)
    return play, player, utils, kodi, window


def initial_playback(play, kodi, api):
    play.route(api, channel_id='123')
    return kodi.set_resolved_url.call_args.args[0]


def reconnect_playback(player_module, window):
    player = player_module.TwitchPlayer(window)
    window.setProperty(player.player_keys['twitch_playing'], 'True')
    window.setProperty(
        player.reconnect_keys['stream'], '123,channel,Channel,Adaptive'
    )
    player.onPlayBackEnded()
    return player.played_item, player.played_path


class Issue21PlaybackSecurityTests(unittest.TestCase):
    def test_initial_adaptive_url_keeps_tls_peer_verification_enabled(self):
        play, player, utils, kodi, window = load_playback_modules()

        item = initial_playback(play, kodi, _Api())

        self.assertFalse(
            'verifypeer=false' in item.item_dict['path'],
            'initial adaptive playback disables TLS peer verification',
        )

    def test_initial_fallback_url_keeps_tls_peer_verification_enabled(self):
        play, player, utils, kodi, window = load_playback_modules()
        utils.use_inputstream_adaptive = lambda: False

        item = initial_playback(play, kodi, _Api(quality='Source'))

        self.assertFalse(
            'verifypeer=false' in item.item_dict['path'],
            'initial fallback playback disables TLS peer verification',
        )

    def test_reconnect_url_keeps_tls_peer_verification_enabled(self):
        play, player, utils, kodi, window = load_playback_modules()

        item, path = reconnect_playback(player, window)

        self.assertFalse(
            'verifypeer=false' in path,
            'reconnect playback disables TLS peer verification',
        )

    def test_reconnect_has_initial_adaptive_property_map(self):
        play, player, utils, kodi, window = load_playback_modules(kodi_major=20)
        initial_item = initial_playback(play, kodi, _Api())
        reconnect_item, path = reconnect_playback(player, window)

        initial_properties = set(initial_item.properties.items())
        reconnect_properties = set(reconnect_item.properties.items())
        missing = initial_properties - reconnect_properties
        extra = reconnect_properties - initial_properties

        self.assertFalse(
            bool(missing),
            'reconnect is missing %d initial adaptive properties' % len(missing),
        )
        self.assertFalse(
            bool(extra),
            'reconnect has %d unexpected adaptive properties' % len(extra),
        )

    def test_kodi_21_reconnect_omits_deprecated_manifest_property(self):
        play, player, utils, kodi, window = load_playback_modules(kodi_major=21)
        initial_item = initial_playback(play, kodi, _Api())
        reconnect_item, path = reconnect_playback(player, window)

        key = 'inputstream.adaptive.manifest_type'
        self.assertFalse(
            key in reconnect_item.properties,
            'reconnect sets the deprecated Kodi 21 manifest property',
        )
        self.assertEqual(
            key in initial_item.properties,
            key in reconnect_item.properties,
        )


if __name__ == '__main__':
    unittest.main()
