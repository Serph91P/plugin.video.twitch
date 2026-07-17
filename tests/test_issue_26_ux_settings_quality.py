import copy
import importlib.util
import re
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / 'resources' / 'lib' / 'twitch_addon'
CONSTANTS = ADDON / 'addon' / 'constants.py'
UTILS = ADDON / 'addon' / 'utils.py'
EDIT_QUALITIES = ADDON / 'routes' / 'edit_qualities.py'
SETTINGS = ROOT / 'resources' / 'settings.xml'
ENGLISH = ROOT / 'resources' / 'language' / 'resource.language.en_gb' / 'strings.po'
GERMAN = ROOT / 'resources' / 'language' / 'resource.language.de_de' / 'strings.po'


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


def _stored_qualities(**qualities):
    return {
        'qualities': {
            'stream': qualities.get('stream', []),
            'video': qualities.get('video', []),
            'clip': qualities.get('clip', []),
        },
        'sorting': {},
        'languages': 'all',
    }


class _Store(object):
    def __init__(self, data):
        self.data = copy.deepcopy(data)
        self.saved = []

    def load(self):
        return self.data

    def save(self, data):
        self.data = copy.deepcopy(data)
        self.saved.append(copy.deepcopy(data))


class _Dialog(object):
    selection = -1
    calls = []

    def select(self, heading, choices):
        self.calls.append((heading, list(choices)))
        return self.selection


class _Converter(object):
    selected_name = None
    seen_videos = []

    def __init__(self, line_length):
        self.line_length = line_length

    def select_video_for_quality(self, videos):
        self.seen_videos = list(videos)
        type(self).seen_videos = self.seen_videos
        return next(
            (video for video in videos if video['name'] == self.selected_name),
            None,
        )


def load_issue_modules(data=None):
    for name in list(sys.modules):
        if name == 'xbmcvfs' or name == 'twitch' or name.startswith('twitch.') \
                or name == 'twitch_addon' or name.startswith('twitch_addon.'):
            del sys.modules[name]

    _package('twitch_addon')
    _package('twitch_addon.addon')
    _package('twitch_addon.routes')
    common = _package('twitch_addon.addon.common')

    kodi = types.ModuleType('twitch_addon.addon.common.kodi')
    kodi.setting_value = ''
    kodi.get_setting = MagicMock(side_effect=lambda setting: kodi.setting_value)
    kodi.decode_utf8 = MagicMock(side_effect=lambda value: value)
    kodi.translate_path = lambda value: value
    kodi.get_profile = lambda: '/tmp/plugin.video.twitch/'
    kodi.get_icon = lambda: 'icon.png'
    kodi.get_fanart = lambda: 'fanart.jpg'
    kodi.Dialog = _Dialog
    kodi.notify = MagicMock()

    translations = {
        'remove_default_quality': 'Remove %s quality',
        'default_quality_set': "Default '%s' quality '%s' set for %s",
        'removed_default_quality': "Removed default '%s' quality for %s",
    }

    class Translations(object):
        def __init__(self, strings):
            self.i18n = lambda key: translations.get(key, key)

    kodi.Translations = Translations
    common.kodi = kodi
    sys.modules[kodi.__name__] = kodi

    store = _Store(data if data is not None else _stored_qualities())
    json_store = types.ModuleType('twitch_addon.addon.common.json_store')
    json_store.JSONStore = lambda filename: store
    common.json_store = json_store
    sys.modules[json_store.__name__] = json_store

    strings = types.ModuleType('twitch_addon.addon.strings')
    strings.STRINGS = {}
    sys.modules[strings.__name__] = strings

    history = types.ModuleType('twitch_addon.addon.search_history')
    for name in (
        'StreamsSearchHistory', 'ChannelsSearchHistory', 'GamesSearchHistory',
        'IdUrlSearchHistory',
    ):
        setattr(history, name, object)
    sys.modules[history.__name__] = history

    twitch = _package('twitch')
    twitch.oauth = _package('twitch.oauth')
    helix = _package('twitch.oauth.helix')
    helix.scopes = types.SimpleNamespace(
        user_read_follows='user_read_follows',
        user_edit_follows='user_edit_follows',
        user_read_subscriptions='user_read_subscriptions',
        chat_read='chat_read',
        chat_edit='chat_edit',
    )
    twitch.api = _package('twitch.api')
    parameters = types.ModuleType('twitch.api.parameters')
    parameters.Boolean = type('Boolean', (), {'TRUE': True})
    parameters.Period = type('Period', (), {'WEEK': 'week'})
    parameters.ClipPeriod = type('ClipPeriod', (), {'WEEK': 'week'})
    parameters.Direction = type('Direction', (), {'DESC': 'desc'})
    parameters.Language = type('Language', (), {
        'ALL': 'all',
        'validate': staticmethod(lambda value: value),
    })
    parameters.SortBy = type('SortBy', (), {'LAST_BROADCAST': 'last_broadcast'})
    parameters.VideoSort = type('VideoSort', (), {'TIME': 'time'})
    sys.modules[parameters.__name__] = parameters

    xbmcvfs = types.ModuleType('xbmcvfs')
    xbmcvfs.exists = lambda path: True
    xbmcvfs.mkdir = MagicMock()
    sys.modules[xbmcvfs.__name__] = xbmcvfs

    constants = _load('twitch_addon.addon.constants', CONSTANTS)
    utils = _load('twitch_addon.addon.utils', UTILS)

    converter = types.ModuleType('twitch_addon.addon.converter')
    converter.JsonListItemConverter = _Converter
    sys.modules[converter.__name__] = converter
    route = _load('twitch_addon.routes.edit_qualities', EDIT_QUALITIES)
    return constants, utils, route, kodi, store


def _po_messages(path):
    text = path.read_text(encoding='utf-8')
    messages = {}
    pattern = re.compile(
        r'msgctxt "#(?P<id>\d+)"\n'
        r'msgid "(?P<msgid>[^"]*)"\n'
        r'msgstr "(?P<msgstr>[^"]*)"'
    )
    for match in pattern.finditer(text):
        messages[match.group('id')] = (
            match.group('msgid'), match.group('msgstr')
        )
    return messages


class Issue26ColorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = ET.parse(SETTINGS).getroot()
        cls.setting = root.find(".//setting[@id='vodcast_highlight']")
        cls.declared_default = cls.setting.findtext('default')
        cls.declared_colors = [
            option.text for option in cls.setting.findall('./constraints/options/option')
        ]

    def setUp(self):
        self.constants, self.utils, _, self.kodi, _ = load_issue_modules()

    def test_declared_default_and_every_declared_color_are_valid(self):
        self.assertEqual('red', self.declared_default)
        for color in self.declared_colors:
            with self.subTest(color=color):
                self.kodi.setting_value = color
                self.assertEqual(color, self.utils.get_vodcast_color())

    def test_known_legacy_numeric_indexes_are_still_supported(self):
        colors = self.constants.COLORS.split('|')
        for index in (0, 9, colors.index('red'), len(colors) - 1):
            with self.subTest(index=index):
                self.kodi.setting_value = str(index)
                self.assertEqual(colors[index], self.utils.get_vodcast_color())

    def test_invalid_values_safely_use_the_declared_default(self):
        colors = self.constants.COLORS.split('|')
        invalid_values = ('', 'not-a-color', True, False, '-1', str(len(colors)), None)
        for value in invalid_values:
            with self.subTest(value=value):
                self.kodi.setting_value = value
                self.assertEqual(self.declared_default, self.utils.get_vodcast_color())


class Issue26SavedQualityTests(unittest.TestCase):
    def setUp(self):
        _Dialog.selection = -1
        _Dialog.calls = []
        _Converter.selected_name = None
        _Converter.seen_videos = []

    def test_removal_renders_in_stored_order_and_persists_selected_item(self):
        data = _stored_qualities(video=[
            {'first': {'name': 'First channel', 'quality': '720p60'}},
            {'second': {'name': 'Second channel', 'quality': 'Source'}},
        ])
        _, utils, _, _, store = load_issue_modules(data)
        _Dialog.selection = 1

        removed = utils.remove_default_quality('video')

        self.assertEqual(
            {'second': {'name': 'Second channel', 'quality': 'Source'}},
            removed,
        )
        self.assertEqual([(
            'Remove video quality',
            ['First channel [720p60]', 'Second channel [Source]'],
        )], _Dialog.calls)
        self.assertEqual([data['qualities']['video'][0]], store.data['qualities']['video'])
        self.assertEqual(1, len(store.saved))

    def test_cancel_does_not_save_or_mutate(self):
        data = _stored_qualities(stream=[
            {'one': {'name': 'Channel', 'quality': '720p60'}},
        ])
        _, utils, _, _, store = load_issue_modules(data)

        self.assertIsNone(utils.remove_default_quality('stream'))

        self.assertEqual(data, store.data)
        self.assertEqual([], store.saved)

    def test_empty_missing_and_malformed_lists_do_not_crash_or_save(self):
        cases = (
            ('empty', _stored_qualities()),
            ('missing type', {
                'qualities': {}, 'sorting': {}, 'languages': 'all',
            }),
            ('malformed qualities', {
                'qualities': None, 'sorting': {}, 'languages': 'all',
            }),
            ('malformed type', {
                'qualities': {'video': 'bad'}, 'sorting': {}, 'languages': 'all',
            }),
            ('malformed entries', _stored_qualities(video=[None, {}, {'id': None}])),
        )
        for label, data in cases:
            with self.subTest(label=label):
                _Dialog.calls = []
                _, utils, _, _, store = load_issue_modules(data)
                self.assertIsNone(utils.remove_default_quality('video'))
                self.assertEqual([], store.saved)
                self.assertEqual([], _Dialog.calls)

    def test_standard_quality_can_be_saved_then_removed_through_route(self):
        _, _, route, kodi, store = load_issue_modules()
        api = MagicMock()
        api.get_vod.return_value = [
            {'id': '720', 'name': '720p60', 'bandwidth': 1, 'url': 'https://720'},
            {'id': 'source', 'name': 'Source', 'bandwidth': 2, 'url': 'https://source'},
        ]
        route.utils.use_inputstream_adaptive = lambda: False
        _Converter.selected_name = '720p60'

        route.route(api, 'video', target_id='channel-id', name='Channel', video_id='vod-id')

        self.assertEqual([{
            'channel-id': {'name': 'Channel', 'quality': '720p60'},
        }], store.data['qualities']['video'])
        _Dialog.selection = 0
        route.route(api, 'video', remove=True)
        self.assertEqual([], store.data['qualities']['video'])
        self.assertEqual(2, len(store.saved))
        self.assertEqual([
            "Default 'video' quality '720p60' set for Channel",
            "Removed default 'video' quality for Channel",
        ], [call.kwargs['msg'] for call in kodi.notify.call_args_list])

    def test_generated_adaptive_quality_can_be_saved_then_removed_through_route(self):
        constants, _, route, kodi, store = load_issue_modules()
        api = MagicMock()
        api.get_live.return_value = [
            {'id': 'source', 'name': 'Source', 'bandwidth': 2, 'url': 'https://source'},
        ]
        route.utils.use_inputstream_adaptive = lambda: True
        _Converter.selected_name = constants.ADAPTIVE_SOURCE_TEMPLATE['name']

        route.route(api, 'stream', target_id='channel-id', name='Channel')

        self.assertEqual([{
            'channel-id': {
                'name': 'Channel',
                'quality': constants.ADAPTIVE_SOURCE_TEMPLATE['name'],
            },
        }], store.data['qualities']['stream'])
        self.assertIn(constants.ADAPTIVE_SOURCE_TEMPLATE, _Converter.seen_videos)
        _Dialog.selection = 0
        route.route(api, 'stream', remove=True)
        self.assertEqual([], store.data['qualities']['stream'])
        self.assertEqual(2, len(store.saved))
        self.assertEqual(
            "Removed default 'stream' quality for Channel",
            kodi.notify.call_args.kwargs['msg'],
        )

    def test_adaptive_quality_is_not_generated_for_clips(self):
        constants, _, route, _, store = load_issue_modules()
        api = MagicMock()
        clip_quality = {
            'id': 'source', 'name': 'Source', 'bandwidth': 2, 'url': 'https://clip',
        }
        api.get_clip.return_value = [clip_quality]
        route.utils.use_inputstream_adaptive = lambda: True
        _Converter.selected_name = 'Source'

        route.route(
            api, 'clip', target_id='channel-id', name='Channel', clip_id='clip-id'
        )

        self.assertNotIn(constants.ADAPTIVE_SOURCE_TEMPLATE, _Converter.seen_videos)
        self.assertEqual([{
            'channel-id': {'name': 'Channel', 'quality': 'Source'},
        }], store.data['qualities']['clip'])


class Issue26SettingsTextTests(unittest.TestCase):
    def test_affected_settings_have_clear_english_and_german_text(self):
        expected = {
            '30167': ('Saved default qualities', 'Gespeicherte Standardqualitäten'),
            '30191': ('Remove a saved stream quality', 'Gespeicherte Streamqualität entfernen'),
            '30192': ('Remove a saved video quality', 'Gespeicherte Videoqualität entfernen'),
            '30193': ('Remove a saved clip quality', 'Gespeicherte Clipqualität entfernen'),
            '30194': ('Remove all saved stream qualities', 'Alle gespeicherten Streamqualitäten entfernen'),
            '30195': ('Remove all saved video qualities', 'Alle gespeicherten Videoqualitäten entfernen'),
            '30196': ('Remove all saved clip qualities', 'Alle gespeicherten Clipqualitäten entfernen'),
            '30222': ('Rerun highlight color', 'Hervorhebungsfarbe für Wiederholungen'),
            '30336': (
                'Sets the title color used to distinguish reruns.',
                'Legt die Titelfarbe zur Kennzeichnung von Wiederholungen fest.',
            ),
            '30337': (
                'Select one saved quality preference to remove.',
                'Eine gespeicherte Qualitätseinstellung zum Entfernen auswählen.',
            ),
            '30338': (
                'Removes every saved quality preference for this content type after confirmation.',
                'Entfernt nach Bestätigung alle gespeicherten Qualitätseinstellungen für diesen Inhaltstyp.',
            ),
        }
        english = _po_messages(ENGLISH)
        german = _po_messages(GERMAN)
        for string_id, (english_text, german_text) in expected.items():
            with self.subTest(string_id=string_id):
                self.assertEqual((english_text, ''), english.get(string_id))
                self.assertEqual((english_text, german_text), german.get(string_id))

        root = ET.parse(SETTINGS).getroot()
        color = root.find(".//setting[@id='vodcast_highlight']")
        self.assertEqual('30222', color.get('label'))
        self.assertEqual('30336', color.get('help'))
        for setting_id in (
            'remove_default_stream_quality', 'remove_default_video_quality',
            'remove_default_clip_quality',
        ):
            self.assertEqual(
                '30337', root.find(".//setting[@id='%s']" % setting_id).get('help')
            )
        for setting_id in (
            'clear_default_stream_qualities', 'clear_default_video_qualities',
            'clear_default_clip_qualities',
        ):
            self.assertEqual(
                '30338', root.find(".//setting[@id='%s']" % setting_id).get('help')
            )


if __name__ == '__main__':
    unittest.main()
