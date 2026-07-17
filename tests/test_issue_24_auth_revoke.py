import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / 'resources' / 'lib' / 'twitch_addon'
DEVICE_AUTH = ADDON / 'addon' / 'device_auth.py'
REVOKE_TOKEN = ADDON / 'routes' / 'revoke_token.py'


class TwitchException(Exception):
    pass


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


def load_device_auth():
    _package('twitch_addon')
    addon = _package('twitch_addon.addon')
    common = _package('twitch_addon.addon.common')

    kodi = types.ModuleType('twitch_addon.addon.common.kodi')
    kodi.set_setting = MagicMock()
    common.kodi = kodi
    sys.modules[kodi.__name__] = kodi

    log_utils = types.ModuleType('twitch_addon.addon.common.log_utils')
    log_utils.LOGDEBUG = 0
    log_utils.LOGINFO = 1
    log_utils.LOGWARNING = 2
    log_utils.LOGERROR = 3
    log_utils.log = MagicMock()
    common.log_utils = log_utils
    sys.modules[log_utils.__name__] = log_utils

    utils = types.ModuleType('twitch_addon.addon.utils')
    utils.get_proxy_dict = MagicMock(return_value={})
    utils.i18n = MagicMock(side_effect=lambda key: key)
    addon.utils = utils
    sys.modules[utils.__name__] = utils

    constants = types.ModuleType('twitch_addon.addon.constants')
    constants.SCOPES = ['user:read:follows']
    sys.modules[constants.__name__] = constants

    requests = types.ModuleType('requests')
    requests.post = MagicMock()
    requests.get = MagicMock()
    requests.exceptions = types.SimpleNamespace(RequestException=Exception)
    sys.modules[requests.__name__] = requests

    module = _load('twitch_addon.addon.device_auth', DEVICE_AUTH)
    return module, log_utils


def load_revoke_token(settings):
    _package('twitch_addon')
    addon = _package('twitch_addon.addon')
    _package('twitch_addon.routes')

    kodi = types.ModuleType('twitch_addon.addon.common.kodi')
    kodi.get_setting = MagicMock(side_effect=lambda key: settings.get(key, ''))
    kodi.set_setting = MagicMock(
        side_effect=lambda key, value: settings.__setitem__(key, value)
    )
    dialog = MagicMock()
    dialog.yesno.return_value = True
    kodi.Dialog = MagicMock(return_value=dialog)
    kodi.notify = MagicMock()
    common = types.ModuleType('twitch_addon.addon.common')
    common.kodi = kodi
    sys.modules[common.__name__] = common
    sys.modules[kodi.__name__] = kodi

    utils = types.ModuleType('twitch_addon.addon.utils')
    utils.get_oauth_token = MagicMock(return_value=settings['oauth_token_helix'])
    utils.i18n = MagicMock(side_effect=lambda key: key)
    addon.utils = utils
    sys.modules[utils.__name__] = utils

    cache = types.ModuleType('twitch_addon.addon.cache')
    cache.reset_cache = MagicMock()
    addon.cache = cache
    sys.modules[cache.__name__] = cache

    device_auth = types.ModuleType('twitch_addon.addon.device_auth')

    def clear_device_tokens():
        for key, value in (
            ('oauth_token_helix', ''),
            ('device_refresh_token', ''),
            ('device_token_expires_at', ''),
            ('is_device_authenticated', 'false'),
        ):
            kodi.set_setting(key, value)

    device_auth.clear_device_tokens = MagicMock(side_effect=clear_device_tokens)
    addon.device_auth = device_auth
    sys.modules[device_auth.__name__] = device_auth

    exceptions = types.ModuleType('twitch_addon.addon.twitch_exceptions')
    exceptions.TwitchException = TwitchException
    sys.modules[exceptions.__name__] = exceptions

    route = _load('twitch_addon.routes.revoke_token', REVOKE_TOKEN)
    return route, kodi, cache, device_auth


def response(payload, status=200, retry_after=None):
    result = MagicMock()
    result.status_code = status
    result.headers = {} if retry_after is None else {'Retry-After': retry_after}
    result.json.return_value = payload
    return result


class RevokeTokenTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            'oauth_token_helix': 'access-token-sentinel',
            'device_refresh_token': 'refresh-token-sentinel',
            'device_token_expires_at': '123456',
            'is_device_authenticated': 'true',
        }

    def test_success_clears_all_reusable_auth_state_after_remote_revoke(self):
        route, kodi, cache, device_auth = load_revoke_token(self.settings)
        api = MagicMock()
        api.access_token = 'access-token-sentinel'
        api.queries.OAUTH_TOKEN = 'access-token-sentinel'

        def successful_revoke(token):
            self.assertEqual('access-token-sentinel', token)
            self.assertEqual('refresh-token-sentinel', self.settings['device_refresh_token'])
            self.assertEqual('access-token-sentinel', api.access_token)
            return {}

        api.client.revoke_token.side_effect = successful_revoke

        route.route(api)

        device_auth.clear_device_tokens.assert_called_once_with()
        self.assertEqual('', self.settings['oauth_token_helix'])
        self.assertEqual('', self.settings['device_refresh_token'])
        self.assertEqual('', self.settings['device_token_expires_at'])
        self.assertEqual('false', self.settings['is_device_authenticated'])
        self.assertEqual('', api.access_token)
        self.assertEqual('', api.queries.OAUTH_TOKEN)
        kodi.notify.assert_called_once_with(msg='token_revoked')
        cache.reset_cache.assert_called_once_with()

    def test_failed_revoke_preserves_persisted_and_in_memory_state(self):
        route, kodi, cache, device_auth = load_revoke_token(self.settings)
        original_settings = dict(self.settings)
        api = MagicMock()
        api.access_token = 'access-token-sentinel'
        api.queries.OAUTH_TOKEN = 'access-token-sentinel'
        api.client.revoke_token.return_value = {'error': 'revoke failed'}

        with self.assertRaises(TwitchException):
            route.route(api)

        self.assertEqual(original_settings, self.settings)
        self.assertEqual('access-token-sentinel', api.access_token)
        self.assertEqual('access-token-sentinel', api.queries.OAUTH_TOKEN)
        device_auth.clear_device_tokens.assert_not_called()
        kodi.notify.assert_not_called()
        cache.reset_cache.assert_not_called()


class DeviceAuthThrottleTests(unittest.TestCase):
    def test_http_429_respects_retry_after_seconds(self):
        device_auth, _ = load_device_auth()
        sleep = MagicMock()
        auth = device_auth.DeviceAuth('client-id-sentinel', sleep=sleep)
        device_auth.requests.post = MagicMock(side_effect=[
            response({'message': 'rate limited'}, status=429, retry_after='7'),
            response({'access_token': 'access-token-sentinel'}),
        ])

        result = auth.poll_for_token('device-code-sentinel', interval=2)

        self.assertEqual('access-token-sentinel', result['access_token'])
        sleep.assert_called_once_with(7)
        self.assertEqual(2, device_auth.requests.post.call_count)

    def test_malformed_retry_after_uses_poll_interval(self):
        device_auth, _ = load_device_auth()
        sleep = MagicMock()
        auth = device_auth.DeviceAuth('client-id-sentinel', sleep=sleep)
        device_auth.requests.post = MagicMock(side_effect=[
            response({}, status=429, retry_after='not-seconds'),
            response({'access_token': 'access-token-sentinel'}),
        ])

        auth.poll_for_token('device-code-sentinel', interval=4)

        sleep.assert_called_once_with(4)

    def test_retry_after_is_bounded_and_exhaustion_is_controlled(self):
        device_auth, _ = load_device_auth()
        sleep = MagicMock()
        auth = device_auth.DeviceAuth('client-id-sentinel', sleep=sleep)
        device_auth.requests.post = MagicMock(
            return_value=response({}, status=429, retry_after='999')
        )

        with self.assertRaisesRegex(device_auth.DeviceAuthError, 'rate limit'):
            auth.poll_for_token('device-code-sentinel', interval=2)

        self.assertEqual(3, device_auth.MAX_RATE_LIMIT_RETRIES)
        self.assertEqual(4, device_auth.requests.post.call_count)
        self.assertEqual([unittest.mock.call(30)] * 3, sleep.call_args_list)

    def test_zero_retry_after_cannot_create_tight_polling_loop(self):
        device_auth, _ = load_device_auth()
        sleep = MagicMock()
        auth = device_auth.DeviceAuth('client-id-sentinel', sleep=sleep)
        device_auth.requests.post = MagicMock(side_effect=[
            response({}, status=429, retry_after='0'),
            response({'access_token': 'access-token-sentinel'}),
        ])

        auth.poll_for_token('device-code-sentinel', interval=0)

        sleep.assert_called_once_with(1)

    def test_throttle_logging_never_contains_authentication_secrets(self):
        device_auth, log_utils = load_device_auth()
        sleep = MagicMock()
        auth = device_auth.DeviceAuth('client-id-sentinel', sleep=sleep)
        device_auth.requests.post = MagicMock(side_effect=[
            response({}, status=429, retry_after='2'),
            response({'access_token': 'access-token-sentinel'}),
        ])

        auth.poll_for_token('device-code-sentinel', interval=1)

        logged = ' '.join(str(call) for call in log_utils.log.call_args_list)
        for secret in (
            'client-id-sentinel', 'device-code-sentinel',
            'access-token-sentinel', 'refresh-token-sentinel',
            'client-secret-sentinel',
        ):
            self.assertNotIn(secret, logged)

    def test_authorization_pending_and_slow_down_keep_existing_delays(self):
        device_auth, _ = load_device_auth()
        sleep = MagicMock()
        auth = device_auth.DeviceAuth('client-id-sentinel', sleep=sleep)
        auth._make_request = MagicMock(side_effect=[
            {'status': 400, 'message': 'authorization_pending'},
            {'status': 400, 'message': 'slow_down'},
            {'access_token': 'access-token-sentinel'},
        ])

        auth.poll_for_token('device-code-sentinel', interval=3)

        self.assertEqual(
            [unittest.mock.call(3), unittest.mock.call(8)],
            sleep.call_args_list,
        )


if __name__ == '__main__':
    unittest.main()
