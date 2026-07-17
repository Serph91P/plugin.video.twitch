import importlib.util
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'resources' / 'lib' / 'twitch_addon' / 'addon' / 'common' / 'cache.py'
API = ROOT / 'resources' / 'lib' / 'twitch_addon' / 'addon' / 'api.py'


def load_cache(cache_path):
    for name in (
        'twitch_addon',
        'twitch_addon.addon',
        'twitch_addon.addon.common',
    ):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    kodi = types.ModuleType('twitch_addon.addon.common.kodi')
    kodi.get_id = MagicMock(return_value='plugin.video.twitch')
    kodi.get_setting = MagicMock(return_value='true')
    kodi.translate_path = MagicMock(return_value=str(cache_path))

    log_utils = types.ModuleType('twitch_addon.addon.common.log_utils')
    log_utils.LOGDEBUG = 0
    log_utils.LOGWARNING = 2
    log_utils.log = MagicMock()

    common = sys.modules['twitch_addon.addon.common']
    common.kodi = kodi
    common.log_utils = log_utils
    sys.modules[kodi.__name__] = kodi
    sys.modules[log_utils.__name__] = log_utils

    spec = importlib.util.spec_from_file_location(
        'twitch_addon.addon.common.cache', CACHE
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, log_utils


def load_api(cache_module):
    addon = sys.modules['twitch_addon.addon']

    cache = types.ModuleType('twitch_addon.addon.cache')
    cache.limit = 1
    cache.cache_method = cache_module.cache_method
    cache.reset_cache = cache_module.reset_cache
    addon.cache = cache
    sys.modules[cache.__name__] = cache

    utils = types.ModuleType('twitch_addon.addon.utils')
    utils.i18n = lambda value: value
    utils.get_client_id = MagicMock(return_value='client-id')
    utils.get_oauth_token = MagicMock(return_value='')
    utils.get_low_latency = MagicMock(return_value=False)
    addon.utils = utils
    sys.modules[utils.__name__] = utils

    gql_search = types.ModuleType('twitch_addon.addon.gql_search')
    addon.gql_search = gql_search
    sys.modules[gql_search.__name__] = gql_search

    constants = types.ModuleType('twitch_addon.addon.constants')
    constants.Keys = types.SimpleNamespace(ID='id', LOGIN='login', DATA='data')
    constants.SCOPES = []
    sys.modules[constants.__name__] = constants

    error_handling = types.ModuleType('twitch_addon.addon.error_handling')
    error_handling.api_error_handler = lambda func: func
    sys.modules[error_handling.__name__] = error_handling

    exceptions = types.ModuleType('twitch_addon.addon.twitch_exceptions')
    exceptions.PlaybackFailed = type('PlaybackFailed', (Exception,), {})
    exceptions.TwitchException = type('TwitchException', (Exception,), {})
    sys.modules[exceptions.__name__] = exceptions

    twitch_package = types.ModuleType('twitch')
    twitch_package.__path__ = []
    queries = types.ModuleType('twitch.queries')
    oauth = types.ModuleType('twitch.oauth')
    oauth.clients = types.SimpleNamespace(MobileClient=MagicMock())
    twitch_package.queries = queries
    twitch_package.oauth = oauth
    sys.modules['twitch'] = twitch_package
    sys.modules['twitch.queries'] = queries
    sys.modules['twitch.oauth'] = oauth

    twitch_api = types.ModuleType('twitch.api')
    twitch_api.__path__ = []
    usher = types.ModuleType('twitch.api.usher')
    helix = types.ModuleType('twitch.api.helix')
    twitch_api.usher = usher
    twitch_api.helix = helix
    sys.modules['twitch.api'] = twitch_api
    sys.modules['twitch.api.usher'] = usher
    sys.modules['twitch.api.helix'] = helix

    parameter = types.SimpleNamespace(ALL='', TIME='', FALSE=False, TRUE=True)
    parameters = types.ModuleType('twitch.api.parameters')
    for name in ('Language', 'Boolean', 'VideoSort', 'PeriodHelix'):
        setattr(parameters, name, parameter)
    sys.modules[parameters.__name__] = parameters

    spec = importlib.util.spec_from_file_location('twitch_addon.addon.api', API)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CacheContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.cache_path = Path(self.temporary_directory.name) / 'cache'
        self.cache, self.log_utils = load_cache(self.cache_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def cache_files(self):
        return list(self.cache_path.iterdir())

    def test_equivalent_calls_and_nested_mapping_orders_share_one_entry(self):
        calls = []

        @self.cache.cache_function(1)
        def channel(login, options=None):
            calls.append((login, options))
            return {'call': len(calls)}

        first = channel('twitch', {
            'headers': {'Client-ID': 'client', 'Accept': 'json'},
            'quality': ['1080p', '720p'],
        })
        second = channel(
            options={
                'quality': ['1080p', '720p'],
                'headers': {'Accept': 'json', 'Client-ID': 'client'},
            },
            login='twitch',
        )

        self.assertEqual(first, second)
        self.assertEqual(1, len(calls))
        self.assertEqual(1, len(self.cache_files()))

    def test_invalidation_removes_fresh_matching_entries_only(self):
        calls = {'valid_token': 0, 'channel_list': 0}

        @self.cache.cache_function(1)
        def valid_token():
            calls['valid_token'] += 1
            return calls['valid_token']

        @self.cache.cache_function(1)
        def channel_list():
            calls['channel_list'] += 1
            return calls['channel_list']

        self.assertEqual(1, valid_token())
        self.assertEqual(1, channel_list())
        self.assertTrue(self.cache.invalidate_cache_for_function('valid'))

        self.assertEqual(2, valid_token())
        self.assertEqual(1, channel_list())
        self.assertEqual({'valid_token': 2, 'channel_list': 1}, calls)
        self.assertEqual(2, len(self.cache_files()))

    def test_corrupt_entry_is_removed_and_treated_as_safe_miss(self):
        calls = []

        @self.cache.cache_function(1)
        def followed_channels():
            calls.append(True)
            return {'call': len(calls)}

        self.assertEqual({'call': 1}, followed_channels())
        cache_file = self.cache_files()[0]
        sentinel = 'DO-NOT-LOG-CORRUPT-PAYLOAD'
        cache_file.write_bytes(('not-a-pickle-' + sentinel).encode('utf-8'))

        with patch.object(self.cache, '_save_func') as save_func:
            self.assertEqual({'call': 2}, followed_channels())

        save_func.assert_called_once()
        self.assertFalse(cache_file.exists())
        logs = ' '.join(str(call) for call in self.log_utils.log.call_args_list)
        self.assertNotIn(sentinel, logs)

    def test_sensitive_boundary_can_explicitly_disable_persistence(self):
        sentinel = 'Bearer DO-NOT-PERSIST-THIS-TOKEN'
        calls = []

        @self.cache.cache_function(1, persist=False)
        def playback_manifest(authorization):
            calls.append(authorization)
            return {'authorization': authorization, 'manifest': '#EXTM3U'}

        self.assertEqual(
            '#EXTM3U', playback_manifest(sentinel)['manifest']
        )
        self.assertEqual(
            '#EXTM3U', playback_manifest(sentinel)['manifest']
        )

        self.assertEqual([sentinel, sentinel], calls)
        persisted = b''.join(path.read_bytes() for path in self.cache_files())
        self.assertNotIn(sentinel.encode('utf-8'), persisted)
        logs = ' '.join(str(call) for call in self.log_utils.log.call_args_list)
        self.assertNotIn(sentinel, logs)

    def test_playback_api_boundaries_do_not_persist_responses(self):
        module = load_api(self.cache)
        twitch = module.Twitch.__new__(module.Twitch)
        twitch.get_private_credential_headers = MagicMock(return_value={})
        twitch.usher = types.SimpleNamespace(
            vod_token=MagicMock(return_value={
                'token': '{"playback":"sensitive-response"}',
            }),
            video=MagicMock(return_value={'playback': 'sensitive-response'}),
            clip=MagicMock(return_value={'playback': 'sensitive-response'}),
            live=MagicMock(return_value={'playback': 'sensitive-response'}),
            live_request=MagicMock(return_value={
                'playback': 'sensitive-response',
            }),
            video_request=MagicMock(return_value={
                'playback': 'sensitive-response',
            }),
        )

        for method, argument in (
            (twitch._get_video_token, 'video-id'),
            (twitch.get_vod, 'video-id'),
            (twitch.get_clip, 'clip-slug'),
            (twitch.get_live, 'channel-name'),
            (twitch.live_request, 'channel-name'),
            (twitch.video_request, 'video-id'),
        ):
            with self.subTest(method=method.__name__):
                self.cache.reset_cache()
                method(argument)
                self.assertEqual([], self.cache_files())

    def test_expired_entry_is_refreshed_while_fresh_entry_is_a_hit(self):
        calls = []

        @self.cache.cache_function(1)
        def games():
            calls.append(True)
            return len(calls)

        self.assertEqual(1, games())
        self.assertEqual(1, games())

        cache_file = self.cache_files()[0]
        expired = time.time() - (2 * 60 * 60)
        os.utime(str(cache_file), (expired, expired))

        self.assertEqual(2, games())
        self.assertEqual(2, len(calls))


if __name__ == '__main__':
    unittest.main()
