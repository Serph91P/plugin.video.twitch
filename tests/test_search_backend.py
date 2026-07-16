import ast
import importlib.util
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / 'resources' / 'lib' / 'twitch_addon' / 'addon' / 'api.py'
UTILS = ROOT / 'resources' / 'lib' / 'twitch_addon' / 'addon' / 'utils.py'
SETTINGS = ROOT / 'resources' / 'settings.xml'


class _Names(object):
    def __getattr__(self, name):
        return name.lower()


class _Parameters(object):
    FALSE = False
    TRUE = True
    ALL = ''
    TIME = ''


def identity_decorator(func=None, **kwargs):
    if func is not None:
        return func
    return lambda wrapped: wrapped


def load_api():
    for name in ('twitch_addon', 'twitch_addon.addon'):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    cache = types.ModuleType('twitch_addon.addon.cache')
    cache.limit = 1
    cache.cache_method = lambda cache_limit, persist=True: identity_decorator
    cache.reset_cache = MagicMock()
    sys.modules[cache.__name__] = cache

    utils = types.ModuleType('twitch_addon.addon.utils')
    utils.i18n = lambda value: value
    utils.get_client_id = MagicMock(return_value='client-id')
    utils.get_oauth_token = MagicMock(return_value='')
    utils.get_search_backend = MagicMock(return_value=0)
    sys.modules[utils.__name__] = utils

    gql_search = types.ModuleType('twitch_addon.addon.gql_search')
    gql_search.search = MagicMock()
    sys.modules[gql_search.__name__] = gql_search

    kodi = types.ModuleType('twitch_addon.addon.common.kodi')
    log_utils = types.ModuleType('twitch_addon.addon.common.log_utils')
    log_utils.LOGWARNING = 2
    log_utils.LOGINFO = 1
    log_utils.LOGDEBUG = 0
    log_utils.log = MagicMock()
    common = types.ModuleType('twitch_addon.addon.common')
    common.kodi = kodi
    common.log_utils = log_utils
    sys.modules[common.__name__] = common
    sys.modules[kodi.__name__] = kodi
    sys.modules[log_utils.__name__] = log_utils

    common_cache = types.ModuleType('twitch_addon.addon.common.cache')
    common_cache.invalidate_cache_for_function = MagicMock()
    sys.modules[common_cache.__name__] = common_cache

    constants = types.ModuleType('twitch_addon.addon.constants')
    constants.Keys = _Names()
    constants.SCOPES = []
    sys.modules[constants.__name__] = constants

    error_handling = types.ModuleType('twitch_addon.addon.error_handling')
    error_handling.api_error_handler = identity_decorator
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
    helix.search = types.SimpleNamespace(
        get_channels=MagicMock(return_value={'data': [{'id': 'helix'}]}),
        get_categories=MagicMock(return_value={'data': [{'id': 'helix'}]}),
    )
    twitch_api.usher = usher
    twitch_api.helix = helix
    sys.modules['twitch.api'] = twitch_api
    sys.modules['twitch.api.usher'] = usher
    sys.modules['twitch.api.helix'] = helix

    parameters = types.ModuleType('twitch.api.parameters')
    for name in (
        'Language', 'Boolean', 'VideoSort', 'PeriodHelix'
    ):
        setattr(parameters, name, _Parameters)
    sys.modules[parameters.__name__] = parameters

    spec = importlib.util.spec_from_file_location('twitch_addon.addon.api', API)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, utils, gql_search


def load_get_search_backend(get_setting):
    tree = ast.parse(UTILS.read_text())
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == 'get_search_backend'
    )
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {'kodi': types.SimpleNamespace(get_setting=get_setting)}
    exec(compile(module, str(UTILS), 'exec'), namespace)
    return namespace['get_search_backend']


class SearchBackendTests(unittest.TestCase):
    def test_setting_only_exact_one_selects_helix(self):
        for value, expected in (
            ('1', 1), (1, 1), ('0', 0), (0, 0), ('', 0),
            ('invalid', 0), ('2', 0), ('01', 0), (True, 0), (None, 0),
        ):
            with self.subTest(value=value):
                backend = load_get_search_backend(lambda name, value=value: value)
                self.assertEqual(expected, backend())

    def test_search_backend_setting_uses_current_integer_spinner_schema(self):
        setting = ET.parse(SETTINGS).find(".//setting[@id='search_backend']")

        if setting is None:
            self.fail('search_backend setting is missing')
        control = setting.find('control')
        if control is None:
            self.fail('search_backend control is missing')
        self.assertEqual('integer', setting.get('type'))
        self.assertEqual('30333', setting.get('label'))
        self.assertEqual('0', setting.findtext('default'))
        self.assertEqual('spinner', control.get('type'))
        self.assertEqual(
            [('30334', '0'), ('30335', '1')],
            [(option.get('label'), option.text)
             for option in setting.findall('./constraints/options/option')],
        )

    def test_gql_success_is_first_page_only_and_has_backend_cache_identity(self):
        module, utils, gql = load_api()
        twitch = module.Twitch.__new__(module.Twitch)
        gql.search.return_value = {'data': [{'id': 'gql'}]}

        with patch.object(twitch, '_get_channel_search', wraps=twitch._get_channel_search) as cached:
            first = twitch.get_channel_search('query')
            utils.get_search_backend.return_value = 1
            helix = twitch.get_channel_search('query')

        self.assertEqual({'data': [{'id': 'gql'}]}, first)
        self.assertEqual({'data': [{'id': 'helix'}]}, helix)
        self.assertEqual([0, 1], [call.args[-1] for call in cached.call_args_list])
        self.assertNotIn('pagination', first)

    def test_gql_failure_falls_back_to_existing_helix_call(self):
        module, utils, gql = load_api()
        twitch = module.Twitch.__new__(module.Twitch)
        gql.search.return_value = None

        result = twitch.get_stream_search('query', first=15)

        self.assertEqual({'data': [{'id': 'helix'}]}, result)
        gql.search.assert_called_once_with('query', 'streams')
        module.Twitch.api.search.get_channels.assert_called_once_with(
            search_query='query', after='MA==', first=15,
            live_only=_Parameters.TRUE,
        )

    def test_helix_setting_and_non_initial_cursor_bypass_gql(self):
        module, utils, gql = load_api()
        twitch = module.Twitch.__new__(module.Twitch)

        utils.get_search_backend.return_value = 1
        twitch.get_game_search('query')
        utils.get_search_backend.return_value = 0
        twitch.get_channel_search('query', after='next-cursor')

        gql.search.assert_not_called()
        module.Twitch.api.search.get_categories.assert_called_once()
        module.Twitch.api.search.get_channels.assert_called_once()


if __name__ == '__main__':
    unittest.main()
