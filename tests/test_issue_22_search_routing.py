import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / 'resources' / 'lib' / 'twitch_addon'
SEARCH_RESULTS = ADDON / 'routes' / 'search_results.py'
ROUTER = ADDON / 'router.py'
ERROR_HANDLING = ADDON / 'addon' / 'error_handling.py'
URL_DISPATCHER = ADDON / 'addon' / 'common' / 'url_dispatcher.py'


class TwitchException(Exception):
    pass


class _Modes(object):
    SEARCHRESULTS = 'search_results'
    GAMES = 'games'
    REFRESH = 'refresh'

    def __getattr__(self, name):
        return name.lower()


def _package(name, path=None):
    module = types.ModuleType(name)
    module.__path__ = [] if path is None else [str(path)]
    sys.modules[name] = module
    return module


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_search_results():
    _package('twitch_addon')
    _package('twitch_addon.routes')
    _package('twitch_addon.addon')

    kodi = types.ModuleType('twitch_addon.addon.common.kodi')
    for name in ('set_view', 'create_item', 'end_of_directory'):
        setattr(kodi, name, MagicMock())
    kodi.get_plugin_url = MagicMock(side_effect=lambda queries: queries)
    kodi.get_id = MagicMock(return_value='plugin.video.twitch')
    window = MagicMock()
    kodi.Window = MagicMock(return_value=window)
    common = types.ModuleType('twitch_addon.addon.common')
    common.kodi = kodi
    sys.modules[common.__name__] = common
    sys.modules[kodi.__name__] = kodi

    history = MagicMock()
    utils = types.ModuleType('twitch_addon.addon.utils')
    utils.get_search_history_size = MagicMock(return_value=10)
    utils.get_search_history = MagicMock(return_value=history)
    utils.get_items_per_page = MagicMock(return_value=25)
    utils.refresh_previews = MagicMock()
    utils.link_to_next_page = MagicMock(side_effect=lambda queries: queries)
    utils.extract_video = MagicMock(return_value=('video-id', 42))
    utils.i18n = lambda key: key
    sys.modules[utils.__name__] = utils

    constants = types.ModuleType('twitch_addon.addon.constants')
    constants.Keys = types.SimpleNamespace(DATA='data')
    constants.LINE_LENGTH = 60
    constants.MODES = _Modes()
    sys.modules[constants.__name__] = constants

    converter = MagicMock()
    converter.game_to_listitem.side_effect = lambda item: ('game', item)
    converter.video_list_to_listitem.side_effect = lambda item: ('video', item)
    converter_module = types.ModuleType('twitch_addon.addon.converter')
    converter_module.JsonListItemConverter = MagicMock(return_value=converter)
    sys.modules[converter_module.__name__] = converter_module

    exceptions = types.ModuleType('twitch_addon.addon.twitch_exceptions')
    exceptions.TwitchException = TwitchException
    sys.modules[exceptions.__name__] = exceptions

    route = _load('twitch_addon.routes.search_results', SEARCH_RESULTS)
    return route, kodi, utils, history, converter, window


def load_router():
    twitch_addon = _package('twitch_addon', ADDON)
    addon = _package('twitch_addon.addon', ADDON / 'addon')
    _package('twitch_addon.routes', ADDON / 'routes')
    common = _package('twitch_addon.addon.common', ADDON / 'addon' / 'common')

    kodi = types.ModuleType('twitch_addon.addon.common.kodi')
    kodi.parse_query = MagicMock()
    kodi.get_version = MagicMock(return_value='test-version')
    kodi.get_kodi_version = MagicMock(return_value='test-kodi')
    kodi.get_id = MagicMock(return_value='plugin.video.twitch')
    kodi.get_setting = MagicMock(return_value='')
    kodi.set_setting = MagicMock()
    kodi.notify = MagicMock()
    kodi.end_of_directory = MagicMock()
    common.kodi = kodi
    sys.modules[kodi.__name__] = kodi

    log_utils = types.ModuleType('twitch_addon.addon.common.log_utils')
    log_utils.LOGDEBUG = 0
    log_utils.LOGNOTICE = 1
    log_utils.LOGWARNING = 2
    log_utils.LOGERROR = 3
    log_utils.log = MagicMock()
    common.log_utils = log_utils
    sys.modules[log_utils.__name__] = log_utils

    _load('twitch_addon.addon.common.url_dispatcher', URL_DISPATCHER)

    utils = types.ModuleType('twitch_addon.addon.utils')
    utils.i18n = MagicMock(side_effect=lambda key: 'localized:' + key)
    sys.modules[utils.__name__] = utils

    exceptions = types.ModuleType('twitch_addon.addon.twitch_exceptions')
    for name in (
        'TwitchException', 'SubRequired', 'ResourceUnavailableException',
        'NotFound', 'PlaybackFailed', 'StreamOffline', 'TokenExpired',
        'RateLimited', 'NetworkError', 'QualityUnavailable',
    ):
        setattr(exceptions, name, type(name, (Exception,), {}))
    sys.modules[exceptions.__name__] = exceptions
    _load('twitch_addon.addon.error_handling', ERROR_HANDLING)

    constants = types.ModuleType('twitch_addon.addon.constants')
    constants.MODES = _Modes()
    sys.modules[constants.__name__] = constants

    api = types.ModuleType('twitch_addon.addon.api')
    api.Twitch = MagicMock(return_value=MagicMock())
    addon.api = api
    sys.modules[api.__name__] = api

    twitch = _package('twitch')
    twitch_api = _package('twitch.api')
    parameters = types.ModuleType('twitch.api.parameters')
    parameters.StreamType = object()
    parameters.Platform = object()
    twitch.api = twitch_api
    sys.modules[parameters.__name__] = parameters

    router = _load('twitch_addon.router', ROUTER)
    twitch_addon.router = router
    return router, kodi, utils


class Issue22SearchRoutingTests(unittest.TestCase):
    def test_game_next_page_preserves_search_route_and_parameters(self):
        route, kodi, utils, history, converter, _ = load_search_results()
        api = MagicMock()
        game = {'id': '1', 'name': 'Game'}
        api.get_game_search.return_value = {
            'data': [game],
            'pagination': {'cursor': 'NEXT'},
        }

        route.route(api, 'games', 'needle', after='CURRENT')

        api.get_game_search.assert_called_once_with(
            search_query='needle', after='CURRENT', first=25
        )
        converter.game_to_listitem.assert_called_once_with(game)
        utils.link_to_next_page.assert_called_once_with({
            'mode': 'search_results',
            'content': 'games',
            'query': 'needle',
            'after': 'NEXT',
        })
        history.update.assert_called_once_with('needle')
        kodi.end_of_directory.assert_called_once_with()

    def test_empty_game_search_ends_directory_once(self):
        route, kodi, _, history, _, _ = load_search_results()
        api = MagicMock()
        api.get_game_search.return_value = {'data': [], 'pagination': {}}

        route.route(api, 'games', 'needle')

        self.assertEqual(1, kodi.create_item.call_count)
        self.assertEqual('refresh', kodi.create_item.call_args.args[0]['label'])
        history.update.assert_not_called()
        kodi.end_of_directory.assert_called_once_with()

    def test_id_url_lookup_failure_shapes_end_directory_once(self):
        failures = (
            ('twitch exception', TwitchException('failed')),
            ('empty response', None),
            ('missing data', {}),
            ('malformed response', []),
            ('malformed data', {'data': None}),
        )
        for label, failure in failures:
            with self.subTest(label=label):
                route, kodi, _, history, converter, window = load_search_results()
                api = MagicMock()
                if isinstance(failure, Exception):
                    api.get_video_by_id.side_effect = failure
                else:
                    api.get_video_by_id.return_value = failure

                route.route(api, 'id_url', 'video-url')

                api.get_video_by_id.assert_called_once_with('video-id')
                kodi.create_item.assert_not_called()
                history.update.assert_not_called()
                converter.video_list_to_listitem.assert_not_called()
                window.setProperty.assert_not_called()
                kodi.end_of_directory.assert_called_once_with()

    def test_valid_id_url_lookup_preserves_history_and_seek(self):
        route, kodi, _, history, converter, window = load_search_results()
        api = MagicMock()
        video = {'id': 'video-id'}
        api.get_video_by_id.return_value = {'data': [video]}

        route.route(api, 'id_url', 'video-url')

        history.update.assert_called_once_with('video-url')
        window.setProperty.assert_called_once_with(
            'plugin.video.twitch-_seek', 'video-id,42'
        )
        converter.video_list_to_listitem.assert_called_once_with(video)
        kodi.create_item.assert_called_once_with(('video', video))
        kodi.end_of_directory.assert_called_once_with()

    def test_unknown_mode_uses_localized_directory_failure_path(self):
        router, kodi, _ = load_router()
        kodi.parse_query.return_value = {'mode': 'not_registered'}

        router.run(['plugin://plugin.video.twitch/', '1', '?mode=not_registered'])

        kodi.notify.assert_called_once_with(
            'localized:error', 'localized:error_unexpected',
            duration=7000, sound=False,
        )
        kodi.end_of_directory.assert_called_once_with(succeeded=False)

    def test_missing_registered_directory_module_uses_same_failure_path(self):
        router, kodi, _ = load_router()
        kodi.parse_query.return_value = {
            'mode': 'collections',
            'channel_id': 'channel-id',
        }

        router.run(['plugin://plugin.video.twitch/', '1', '?mode=collections'])

        kodi.notify.assert_called_once_with(
            'localized:error', 'localized:error_unexpected',
            duration=7000, sound=False,
        )
        kodi.end_of_directory.assert_called_once_with(succeeded=False)


if __name__ == '__main__':
    unittest.main()
