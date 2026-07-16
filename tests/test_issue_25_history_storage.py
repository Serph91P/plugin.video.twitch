import importlib.util
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
SEARCH_HISTORY = (
    ROOT / 'resources' / 'lib' / 'twitch_addon' / 'addon' / 'common'
    / 'search_history.py'
)
WATCH_HISTORY = (
    ROOT / 'resources' / 'lib' / 'twitch_addon' / 'addon' / 'watch_history.py'
)
PLAY = ROOT / 'resources' / 'lib' / 'twitch_addon' / 'routes' / 'play.py'


def _package(name):
    module = types.ModuleType(name)
    module.__path__ = []
    sys.modules[name] = module
    return module


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_search_history(profile):
    _package('twitch_addon')
    _package('twitch_addon.addon')
    common = _package('twitch_addon.addon.common')

    def translate_path(path):
        directory = profile / 'search'
        if path.endswith('/'):
            return str(directory) + os.sep
        return str(directory / path.rsplit('/', 1)[-1])

    kodi = types.ModuleType('twitch_addon.addon.common.kodi')
    kodi.translate_path = translate_path
    kodi.decode_utf8 = lambda value: value
    common.kodi = kodi
    sys.modules[kodi.__name__] = kodi

    xbmcvfs = types.ModuleType('xbmcvfs')
    xbmcvfs.exists = os.path.exists
    xbmcvfs.mkdirs = lambda path: Path(path).mkdir(parents=True, exist_ok=True)
    sys.modules[xbmcvfs.__name__] = xbmcvfs

    return _load('twitch_addon.addon.common.search_history', SEARCH_HISTORY)


def load_watch_history(profile):
    _package('twitch_addon')
    _package('twitch_addon.addon')
    common = _package('twitch_addon.addon.common')

    def translate_path(path):
        directory = profile / 'history'
        if path.endswith('/'):
            return str(directory) + os.sep
        return str(directory / path.rsplit('/', 1)[-1])

    kodi = types.ModuleType('twitch_addon.addon.common.kodi')
    kodi.translate_path = translate_path
    common.kodi = kodi
    sys.modules[kodi.__name__] = kodi

    xbmcvfs = types.ModuleType('xbmcvfs')
    xbmcvfs.exists = os.path.exists
    xbmcvfs.mkdirs = lambda path: Path(path).mkdir(parents=True, exist_ok=True)
    sys.modules[xbmcvfs.__name__] = xbmcvfs

    return _load('twitch_addon.addon.watch_history', WATCH_HISTORY)


class _Names(object):
    def __getattr__(self, name):
        return name.lower()


class _Window(object):
    def __init__(self):
        self.properties = {}

    def clearProperty(self, key):
        self.properties.pop(key, None)

    def getProperty(self, key):
        return self.properties.get(key, '')

    def setProperty(self, key, value):
        self.properties[key] = value


class _Item(object):
    def __init__(self, item_dict=None):
        self.item_dict = item_dict or {}

    def addStreamInfo(self, stream_type, info):
        pass

    def setContentLookup(self, enabled):
        pass

    def setMimeType(self, mime_type):
        pass

    def setProperty(self, key, value):
        pass


class _Converter(object):
    def __init__(self, line_length):
        pass

    def stream_to_playitem(self, result, id_only=False):
        return {
            'path': '',
            'thumbnail': 'https://images.example/live.jpg',
            'info': {'title': 'Live title', 'genre': 'Live game'},
        }

    def get_video_for_quality(self, videos, ask=False, quality=None, clip=False):
        return videos[0]


class _Api(object):
    access_token = ''

    def get_channel_stream(self, channel_id):
        return {'data': [{
            'user_name': 'Channel Name',
            'user_login': 'channel_login',
            'user_id': channel_id,
        }]}

    def get_live(self, name):
        return [{
            'name': 'Source',
            'url': 'https://video.example/live.m3u8',
        }]


def load_play_route(use_player=False, dispatch_error=None):
    _package('twitch_addon')
    _package('twitch_addon.addon')
    common = _package('twitch_addon.addon.common')
    _package('twitch_addon.routes')

    window = _Window()
    player = MagicMock()
    kodi = types.ModuleType('twitch_addon.addon.common.kodi')
    kodi.get_id = lambda: 'plugin.video.twitch'
    kodi.Window = lambda window_id: window
    kodi.create_item = lambda item_dict, add=False: _Item(item_dict)
    kodi.ListItem = _Item
    kodi.Player = MagicMock(return_value=player)
    kodi.set_resolved_url = MagicMock()
    if dispatch_error:
        if use_player:
            player.play.side_effect = dispatch_error
        else:
            kodi.set_resolved_url.side_effect = dispatch_error

    log_utils = types.ModuleType('twitch_addon.addon.common.log_utils')
    log_utils.LOGDEBUG = 0
    log_utils.LOGINFO = 1
    log_utils.LOGWARNING = 2
    log_utils.log = MagicMock()
    common.kodi = kodi
    common.log_utils = log_utils
    sys.modules[kodi.__name__] = kodi
    sys.modules[log_utils.__name__] = log_utils

    utils = types.ModuleType('twitch_addon.addon.utils')
    utils.use_inputstream_adaptive = lambda: False
    utils.get_default_quality = lambda content_type, target_id: None
    utils.append_headers = lambda headers: ''
    utils.get_proxy_dict = lambda: None
    utils.get_watch_history_size = lambda: 10
    utils.irc_enabled = lambda: False
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

    stored_history = MagicMock()
    history = types.ModuleType('twitch_addon.addon.watch_history')
    history.get_watch_history = MagicMock(return_value=stored_history)
    sys.modules[history.__name__] = history

    play = _load('twitch_addon.routes.play', PLAY)
    return play, kodi, player, stored_history


class SearchHistoryRegressionTests(unittest.TestCase):
    def test_max_one_overflow_keeps_only_newest_without_locking(self):
        with tempfile.TemporaryDirectory() as directory:
            module = load_search_history(Path(directory))
            history = module.SearchHistory('streams_search', max_items=10)

            history.update('first')
            history.update('second')
            history.update('third')

            history = module.SearchHistory('streams_search', max_items=1)
            history.update('fourth')

            self.assertEqual(['fourth'], history.list())

    def test_retention_preserves_newest_first_at_zero_and_two(self):
        cases = (
            (0, ['first'], []),
            (2, ['first', 'second', 'third'], ['third', 'second']),
        )
        for max_items, values, expected in cases:
            with self.subTest(max_items=max_items):
                with tempfile.TemporaryDirectory() as directory:
                    module = load_search_history(Path(directory))
                    history = module.SearchHistory(
                        'streams_search', max_items=max_items
                    )

                    for value in values:
                        history.update(value)

                    self.assertEqual(expected, history.list())

    def test_update_uses_one_transaction_and_does_not_vacuum(self):
        with tempfile.TemporaryDirectory() as directory:
            module = load_search_history(Path(directory))
            history = module.SearchHistory('streams_search', max_items=2)
            real_connect = sqlite3.connect
            statements = []

            def traced_connect(*args, **kwargs):
                database = real_connect(*args, **kwargs)
                database.set_trace_callback(statements.append)
                return database

            with patch.object(
                module.sqlite3, 'connect', side_effect=traced_connect
            ) as connect:
                history.update('first')

            sql = [statement.upper() for statement in statements]
            self.assertEqual(1, connect.call_count)
            self.assertEqual(1, sql.count('BEGIN'))
            self.assertEqual(1, sql.count('COMMIT'))
            self.assertFalse(any('VACUUM' in statement for statement in sql))


class WatchHistoryRegressionTests(unittest.TestCase):
    @staticmethod
    def add(history, content_id):
        history.add(
            content_type='video',
            content_id=content_id,
            channel_id='channel-' + content_id,
            channel_name='Channel ' + content_id,
            title='Title ' + content_id,
        )

    def test_add_retains_configured_limits_and_newest_first(self):
        cases = (
            (0, ['one'], []),
            (1, ['one', 'two'], ['two']),
            (2, ['one', 'two', 'three'], ['three', 'two']),
        )
        for max_items, values, expected in cases:
            with self.subTest(max_items=max_items):
                with tempfile.TemporaryDirectory() as directory:
                    module = load_watch_history(Path(directory))
                    history = module.WatchHistory(max_items=max_items)

                    for value in values:
                        self.add(history, value)

                    self.assertEqual(
                        expected,
                        [item['content_id'] for item in history.list()],
                    )

    def test_add_and_cleanup_use_one_transaction_without_vacuum(self):
        with tempfile.TemporaryDirectory() as directory:
            module = load_watch_history(Path(directory))
            history = module.WatchHistory(max_items=1)
            self.add(history, 'first')
            real_connect = sqlite3.connect
            statements = []

            def traced_connect(*args, **kwargs):
                database = real_connect(*args, **kwargs)
                database.set_trace_callback(statements.append)
                return database

            with patch.object(
                module.sqlite3, 'connect', side_effect=traced_connect
            ) as connect:
                self.add(history, 'second')

            sql = [statement.upper() for statement in statements]
            self.assertEqual(1, connect.call_count)
            self.assertEqual(1, sql.count('BEGIN'))
            self.assertEqual(1, sql.count('COMMIT'))
            self.assertFalse(any('VACUUM' in statement for statement in sql))
            self.assertEqual(
                ['second'],
                [item['content_id'] for item in history.list()],
            )


class PlaybackHistoryRegressionTests(unittest.TestCase):
    def test_failed_dispatch_does_not_create_watched_entry(self):
        for use_player in (False, True):
            with self.subTest(use_player=use_player):
                play, kodi, player, history = load_play_route(
                    use_player=use_player,
                    dispatch_error=RuntimeError('dispatch failed'),
                )

                with self.assertRaisesRegex(RuntimeError, 'dispatch failed'):
                    play.route(_Api(), channel_id='123', use_player=use_player)

                history.add.assert_not_called()

    def test_confirmed_dispatch_creates_exactly_one_watched_entry(self):
        for use_player in (False, True):
            with self.subTest(use_player=use_player):
                play, kodi, player, history = load_play_route(
                    use_player=use_player
                )
                events = MagicMock()
                dispatch = player.play if use_player else kodi.set_resolved_url
                events.attach_mock(dispatch, 'dispatch')
                events.attach_mock(history.add, 'add')

                play.route(_Api(), channel_id='123', use_player=use_player)

                history.add.assert_called_once_with(
                    content_type='stream',
                    content_id='123',
                    channel_id='123',
                    channel_name='Channel Name',
                    title='Live title',
                    thumbnail='https://images.example/live.jpg',
                    game_name='Live game',
                )
                self.assertEqual('dispatch', events.mock_calls[0][0])
                self.assertEqual('add', events.mock_calls[1][0])


if __name__ == '__main__':
    unittest.main()
