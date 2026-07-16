import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'resources' / 'lib' / 'twitch_addon' / 'service.py'


class _Keys(object):
    DATA = 'data'
    DISPLAY_NAME = 'display_name'
    GAME_NAME = 'game_name'
    ID = 'id'
    LOGIN = 'login'
    PROFILE_IMAGE_URL = 'profile_image_url'
    STREAM = 'stream'
    STREAMS = 'streams'
    TYPE = 'type'
    USER_ID = 'user_id'


def _function(value=None):
    return value


def load_service_module():
    for name in ('twitch_addon', 'twitch_addon.addon'):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    common = types.ModuleType('twitch_addon.addon.common')
    common.kodi = types.SimpleNamespace()
    common.log_utils = types.SimpleNamespace()
    sys.modules[common.__name__] = common

    utils = types.ModuleType('twitch_addon.addon.utils')
    utils.i18n = _function
    utils.get_stamp_diff = _function
    utils.get_vodcast_color = lambda: 'vodcast'
    sys.modules[utils.__name__] = utils

    constants = types.ModuleType('twitch_addon.addon.constants')
    constants.Keys = _Keys
    sys.modules[constants.__name__] = constants

    player = types.ModuleType('twitch_addon.addon.player')
    player.TwitchPlayer = object
    sys.modules[player.__name__] = player

    for name in ('twitch_addon.addon.api', 'twitch_addon.addon.cache'):
        sys.modules[name] = types.ModuleType(name)

    xbmc = types.ModuleType('xbmc')
    sys.modules[xbmc.__name__] = xbmc

    spec = importlib.util.spec_from_file_location('twitch_addon.service', SERVICE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def stream(user_id, game_name):
    return {
        _Keys.USER_ID: user_id,
        _Keys.TYPE: 'live',
        _Keys.GAME_NAME: game_name,
    }


def user(user_id):
    return {
        _Keys.ID: user_id,
        _Keys.LOGIN: 'login-{0}'.format(user_id),
        _Keys.DISPLAY_NAME: 'User {0}'.format(user_id),
        _Keys.PROFILE_IMAGE_URL: 'https://img/{0}.png'.format(user_id),
    }


class _Monitor(object):
    def __init__(self):
        self.aborted = False

    def waitForAbort(self, timeout):
        return self.aborted


class _TwitchApi(object):
    def __init__(self, pages, users, reverse_users=False):
        self.pages = list(pages)
        self.users = users
        self.reverse_users = reverse_users
        self.followed_calls = []
        self.user_calls = []

    def get_user_id(self):
        return 'viewer'

    def get_followed_streams(self, **kwargs):
        self.followed_calls.append(kwargs)
        return self.pages.pop(0)

    def get_users(self, user_ids):
        self.user_calls.append(list(user_ids))
        users = [dict(self.users[user_id]) for user_id in user_ids
                 if user_id in self.users]
        if self.reverse_users:
            users.reverse()
        return {_Keys.DATA: users}


class _AbortDuringStreamApi(_TwitchApi):
    def __init__(self, monitor, pages, users):
        super(_AbortDuringStreamApi, self).__init__(pages, users)
        self.monitor = monitor

    def get_followed_streams(self, **kwargs):
        result = super(_AbortDuringStreamApi, self).get_followed_streams(**kwargs)
        self.monitor.aborted = True
        return result


class _UsersResponseApi(_TwitchApi):
    def __init__(self, pages, response):
        super(_UsersResponseApi, self).__init__(pages, {})
        self.response = response

    def get_users(self, user_ids):
        self.user_calls.append(list(user_ids))
        return self.response


class ServicePaginationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = load_service_module()

    def make_thread(self):
        thread = self.service.LiveNotificationsThread.__new__(
            self.service.LiveNotificationsThread
        )
        thread.logos = {}
        return thread

    def test_two_pages_use_page_local_ids_and_skip_a_missing_user(self):
        twitch_api = _TwitchApi(
            pages=[
                {
                    _Keys.DATA: [stream('one', 'Game 1'), stream('gone', 'Old')],
                    'pagination': {'cursor': 'next'},
                },
                {
                    _Keys.DATA: [stream('two', 'Game 2')],
                    'pagination': {},
                },
            ],
            users={'one': user('one'), 'two': user('two')},
        )

        result = self.make_thread().get_followed_streams(twitch_api, _Monitor())

        self.assertEqual(result, [
            ('one', 'login-one', 'User one', 'Game 1'),
            ('two', 'login-two', 'User two', 'Game 2'),
        ])
        self.assertEqual(twitch_api.followed_calls, [
            {'user_id': 'viewer', 'first': 100, 'after': 'MA=='},
            {'user_id': 'viewer', 'first': 100, 'after': 'next'},
        ])
        self.assertEqual(twitch_api.user_calls, [['one', 'gone'], ['two']])

    def test_repeated_ids_are_processed_once_in_first_seen_order(self):
        twitch_api = _TwitchApi(
            pages=[
                {
                    _Keys.DATA: [
                        stream('one', 'Game 1'),
                        stream('one', 'Duplicate 1'),
                        stream('two', 'Game 2'),
                    ],
                    'pagination': {'cursor': 'next'},
                },
                {
                    _Keys.DATA: [
                        stream('two', 'Duplicate 2'),
                        stream('three', 'Game 3'),
                    ],
                    'pagination': {},
                },
            ],
            users={name: user(name) for name in ('one', 'two', 'three')},
        )

        result = self.make_thread().get_followed_streams(twitch_api, _Monitor())

        self.assertEqual(result, [
            ('one', 'login-one', 'User one', 'Game 1'),
            ('two', 'login-two', 'User two', 'Game 2'),
            ('three', 'login-three', 'User three', 'Game 3'),
        ])
        self.assertEqual(twitch_api.followed_calls, [
            {'user_id': 'viewer', 'first': 100, 'after': 'MA=='},
            {'user_id': 'viewer', 'first': 100, 'after': 'next'},
        ])
        self.assertEqual(twitch_api.user_calls, [['one', 'two'], ['three']])

    def test_partial_user_results_skip_missing_users_and_optional_fields(self):
        partial_user = user('one')
        del partial_user[_Keys.PROFILE_IMAGE_URL]
        twitch_api = _TwitchApi(
            pages=[{
                _Keys.DATA: [stream('gone', 'Old'), stream('one', 'Game 1')],
                'pagination': {},
            }],
            users={'one': partial_user},
        )
        thread = self.make_thread()

        result = thread.get_followed_streams(twitch_api, _Monitor())

        self.assertEqual(result, [
            ('one', 'login-one', 'User one', 'Game 1'),
        ])
        self.assertEqual(thread.logos, {})
        self.assertEqual(twitch_api.followed_calls, [
            {'user_id': 'viewer', 'first': 100, 'after': 'MA=='},
        ])
        self.assertEqual(twitch_api.user_calls, [['gone', 'one']])

    def test_none_user_data_skips_unresolved_users(self):
        twitch_api = _UsersResponseApi(
            pages=[{
                _Keys.DATA: [stream('one', 'Game 1')],
                'pagination': {},
            }],
            response={_Keys.DATA: None},
        )

        result = self.make_thread().get_followed_streams(twitch_api, _Monitor())

        self.assertEqual(result, [])
        self.assertEqual(twitch_api.user_calls, [['one']])

    def test_empty_page_does_not_request_users(self):
        twitch_api = _TwitchApi(
            pages=[{_Keys.DATA: [], 'pagination': {}}],
            users={},
        )

        result = self.make_thread().get_followed_streams(twitch_api, _Monitor())

        self.assertEqual(result, [])
        self.assertEqual(twitch_api.followed_calls, [
            {'user_id': 'viewer', 'first': 100, 'after': 'MA=='},
        ])
        self.assertEqual(twitch_api.user_calls, [])

    def test_abort_during_pagination_returns_none_without_more_api_calls(self):
        monitor = _Monitor()
        twitch_api = _AbortDuringStreamApi(
            monitor=monitor,
            pages=[{
                _Keys.DATA: [stream('one', 'Game 1')],
                'pagination': {'cursor': 'next'},
            }],
            users={'one': user('one')},
        )

        result = self.make_thread().get_followed_streams(twitch_api, monitor)

        self.assertIsNone(result)
        self.assertEqual(twitch_api.followed_calls, [
            {'user_id': 'viewer', 'first': 100, 'after': 'MA=='},
        ])
        self.assertEqual(twitch_api.user_calls, [])

    def test_user_response_order_does_not_change_stream_order(self):
        twitch_api = _TwitchApi(
            pages=[{
                _Keys.DATA: [stream('one', 'Game 1'), stream('two', 'Game 2')],
                'pagination': {},
            }],
            users={'one': user('one'), 'two': user('two')},
            reverse_users=True,
        )

        result = self.make_thread().get_followed_streams(twitch_api, _Monitor())

        self.assertEqual(result, [
            ('one', 'login-one', 'User one', 'Game 1'),
            ('two', 'login-two', 'User two', 'Game 2'),
        ])
        self.assertEqual(twitch_api.followed_calls, [
            {'user_id': 'viewer', 'first': 100, 'after': 'MA=='},
        ])
        self.assertEqual(twitch_api.user_calls, [['one', 'two']])


if __name__ == '__main__':
    unittest.main()
