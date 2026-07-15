import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]
GQL_SEARCH = ROOT / 'resources' / 'lib' / 'twitch_addon' / 'addon' / 'gql_search.py'


class _Names(object):
    def __getattr__(self, name):
        return name.lower()


def load_gql_search():
    for name in ('twitch_addon', 'twitch_addon.addon'):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    constants = types.ModuleType('twitch_addon.addon.constants')
    constants.Keys = _Names()
    sys.modules[constants.__name__] = constants

    log_utils = types.ModuleType('twitch_addon.addon.common.log_utils')
    log_utils.LOGWARNING = 2
    log_utils.log = MagicMock()
    common = types.ModuleType('twitch_addon.addon.common')
    common.log_utils = log_utils
    sys.modules[common.__name__] = common
    sys.modules[log_utils.__name__] = log_utils

    utils = types.ModuleType('twitch_addon.addon.utils')
    utils.get_private_client_id = MagicMock(return_value='website-client-id')
    sys.modules[utils.__name__] = utils

    requests = types.ModuleType('requests')
    requests.RequestException = RequestException
    requests.post = MagicMock()
    sys.modules['requests'] = requests

    spec = importlib.util.spec_from_file_location(
        'twitch_addon.addon.gql_search', GQL_SEARCH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, requests, utils, log_utils


class RequestException(Exception):
    pass


class FakeResponse(object):
    def __init__(self, payload=None, status_error=None, json_error=None):
        self.payload = payload
        self.status_error = status_error
        self.json_error = json_error

    def raise_for_status(self):
        if self.status_error:
            raise self.status_error

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload


def channel_payload(edges):
    return [{
        'data': {
            'searchFor': {
                'channels': {'edges': edges},
            },
        },
    }]


def game_payload(edges):
    return {
        'data': {
            'searchFor': {
                'games': {'edges': edges},
            },
        },
    }


LIVE_CHANNEL = {
    'id': '1',
    'login': 'live_login',
    'displayName': 'Live Name',
    'broadcastSettings': {'language': 'en', 'title': 'Live title'},
    'profileImageURL': 'profile.jpg',
    'stream': {
        'id': 'stream-1',
        'viewersCount': 42,
        'previewImageURL': 'preview.jpg',
        'game': {'id': 'game-1', 'name': 'Game'},
    },
}
OFFLINE_CHANNEL = {
    'id': '2',
    'login': 'offline_login',
    'displayName': 'Offline Name',
    'stream': None,
}


class GqlSearchTests(unittest.TestCase):
    def setUp(self):
        self.module, self.requests, self.utils, self.log_utils = load_gql_search()

    def respond(self, payload):
        self.requests.post.return_value = FakeResponse(payload)

    def test_channel_search_maps_live_and_offline_converter_fields(self):
        self.respond(channel_payload([
            {'item': LIVE_CHANNEL},
            {'item': OFFLINE_CHANNEL},
        ]))

        result = self.module.search('query', 'channels')

        self.assertNotIn('pagination', result)
        self.assertEqual(2, len(result['data']))
        live, offline = result['data']
        self.assertEqual('1', live['id'])
        self.assertEqual('live_login', live['broadcaster_login'])
        self.assertEqual('Live Name', live['display_name'])
        self.assertEqual('en', live['broadcaster_language'])
        self.assertEqual('Live title', live['title'])
        self.assertEqual('profile.jpg', live['offline_image_url'])
        self.assertEqual('preview.jpg', live['thumbnail_url'])
        self.assertEqual(42, live['viewer_count'])
        self.assertEqual('Game', live['game_name'])
        self.assertEqual('game-1', live['game_id'])
        self.assertEqual('', offline['title'])
        self.assertEqual('', offline['broadcaster_language'])
        self.assertEqual('', offline['thumbnail_url'])
        self.assertEqual(0, offline['viewer_count'])
        self.assertEqual('', offline['game_name'])
        self.assertEqual('', offline['game_id'])

    def test_stream_search_filters_offline_channels(self):
        self.respond(channel_payload([
            {'item': OFFLINE_CHANNEL},
            {'item': dict(OFFLINE_CHANNEL, stream={})},
            {'item': LIVE_CHANNEL},
        ]))

        result = self.module.search('query', 'streams')

        self.assertEqual(['1'], [item['id'] for item in result['data']])

    def test_game_search_maps_name_fallback_and_box_art(self):
        self.respond(game_payload([
            {'item': {'id': '10', 'name': 'First', 'boxArtURL': 'first.jpg'}},
            {'item': {'id': '11', 'displayName': 'Second'}},
        ]))

        result = self.module.search('query', 'games')

        self.assertEqual([
            {'id': '10', 'name': 'First', 'box_art_url': 'first.jpg'},
            {'id': '11', 'name': 'Second', 'box_art_url': ''},
        ], result['data'])

    def test_malformed_items_are_skipped_but_valid_items_remain(self):
        self.respond(channel_payload([
            {},
            {'item': None},
            {'item': {'id': '1', 'login': 'missing-display'}},
            {'item': LIVE_CHANNEL},
        ]))

        result = self.module.search('query', 'channels')

        self.assertEqual(['1'], [item['id'] for item in result['data']])

    def test_request_is_anonymous_batched_and_bounded(self):
        self.respond(channel_payload([{'item': LIVE_CHANNEL}]))

        self.module.search('needle', 'channels')

        args, kwargs = self.requests.post.call_args
        self.assertEqual(('https://gql.twitch.tv/gql',), args)
        self.assertEqual(15, kwargs['timeout'])
        self.assertEqual({'Client-ID': 'website-client-id'}, kwargs['headers'])
        self.assertNotIn('Authorization', kwargs['headers'])
        self.assertIsInstance(kwargs['json'], list)
        self.assertEqual('needle', kwargs['json'][0]['variables']['q'])
        self.utils.get_private_client_id.assert_called_once_with()

    def test_failure_shapes_return_none_for_helix_fallback(self):
        failures = [
            [],
            'not-an-envelope',
            {'errors': [{'message': 'failed'}]},
            {'data': None},
            {'data': {'searchFor': []}},
            {'data': {'searchFor': {'channels': []}}},
            {'data': {'searchFor': {'channels': {'edges': 'bad'}}}},
            channel_payload([]),
            channel_payload([{'item': {'id': '1'}}]),
        ]
        for payload in failures:
            with self.subTest(payload=payload):
                self.respond(payload)
                self.assertIsNone(self.module.search('query', 'channels'))

    def test_request_http_and_json_errors_return_none(self):
        errors = [
            FakeResponse(status_error=RequestException('http')),
            FakeResponse(json_error=ValueError('json')),
        ]
        for response in errors:
            with self.subTest(response=response):
                self.requests.post.return_value = response
                self.assertIsNone(self.module.search('query', 'channels'))

        self.requests.post.side_effect = RequestException('timeout')
        self.assertIsNone(self.module.search('query', 'channels'))


if __name__ == '__main__':
    unittest.main()
