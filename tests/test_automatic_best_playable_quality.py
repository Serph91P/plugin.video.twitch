import importlib.util
import re
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures'
CONVERTER = ROOT / 'resources' / 'lib' / 'twitch_addon' / 'addon' / 'converter.py'


class _Names(object):
    def __getattr__(self, name):
        return name.lower()


class _Dialog(object):
    def select(self, heading, choices):
        return 0


class _Kodi(object):
    settings = {}
    Dialog = _Dialog

    @classmethod
    def get_setting(cls, name):
        return cls.settings.get(name, '')


def _function(value=None):
    return value


def load_converter_module():
    package_names = ('twitch_addon', 'twitch_addon.addon')
    for name in package_names:
        module = types.ModuleType(name)
        module.__path__ = []
        sys.modules[name] = module

    common = types.ModuleType('twitch_addon.addon.common')
    common.kodi = _Kodi
    sys.modules[common.__name__] = common

    menu_items = types.ModuleType('twitch_addon.addon.menu_items')
    sys.modules[menu_items.__name__] = menu_items

    constants = types.ModuleType('twitch_addon.addon.constants')
    constants.Keys = _Names()
    constants.Images = _Names()
    constants.MODES = _Names()
    constants.ADAPTIVE_SOURCE_TEMPLATE = {
        'id': 'hls',
        'name': 'Adaptive (H.265/2K/4K)',
        'bandwidth': -1,
        'url': '',
    }
    sys.modules[constants.__name__] = constants

    utils = types.ModuleType('twitch_addon.addon.utils')
    for name in (
        'the_art', 'TitleBuilder', 'i18n', 'get_oauth_token',
        'get_vodcast_color', 'use_inputstream_adaptive', 'get_thumbnail_size',
        'get_refresh_stamp', 'to_string', 'get_private_oauth_token',
        'get_hevc_token', 'supports_hevc_decoding', 'convert_duration',
    ):
        setattr(utils, name, _function)
    sys.modules[utils.__name__] = utils

    spec = importlib.util.spec_from_file_location(
        'twitch_addon.addon.converter', CONVERTER
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _attributes(line):
    return {
        match.group(1): match.group(2).strip('"')
        for match in re.finditer(r'(?:^|,)([A-Z0-9-]+)=("[^"]*"|[^,]*)', line)
    }


def parse_master_playlist(name):
    """Parse fixture text into the rendition contract returned by Twitch usher."""
    lines = [line.strip() for line in (FIXTURES / name).read_text().splitlines()]
    renditions = []
    media = None
    for index, line in enumerate(lines):
        if line.startswith('#EXT-X-MEDIA:'):
            media = _attributes(line.split(':', 1)[1])
            continue
        if not line.startswith('#EXT-X-STREAM-INF:'):
            continue

        stream = _attributes(line.split(':', 1)[1])
        group_id = (media or {}).get('GROUP-ID', stream.get('VIDEO', ''))
        group_name = (media or {}).get('NAME', group_id)
        if group_name == 'audio_only':
            display_name = 'Audio Only'
        elif group_id == 'chunked':
            display_name = 'Source'
        else:
            display_name = group_name
        renditions.append({
            'id': group_id,
            'name': display_name,
            'url': lines[index + 1],
            'bandwidth': int(stream['BANDWIDTH']),
            'fps': float(stream['FRAME-RATE']) if stream.get('FRAME-RATE') else None,
            'resolution': stream.get('RESOLUTION'),
            'codecs': stream.get('CODECS'),
        })
        media = None
    return renditions


class AutomaticBestPlayableQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.converter_module = load_converter_module()

    def setUp(self):
        _Kodi.settings = {
            'video_quality': '4',
            'bandwidth': '2500000',
            'source_frame_rate_limit': '0',
        }
        self.converter = self.converter_module.JsonListItemConverter.__new__(
            self.converter_module.JsonListItemConverter
        )

    def select(
        self, fixture, inputstream=False, token=True, decoder_supported=True,
        quality=None
    ):
        videos = parse_master_playlist(fixture)
        with patch.object(
            self.converter_module, 'use_inputstream_adaptive', return_value=inputstream
        ), patch.object(
            self.converter_module, 'get_hevc_token', return_value='website-token' if token else ''
        ), patch.object(
            self.converter_module, 'supports_hevc_decoding',
            return_value=decoder_supported, create=True
        ):
            return self.converter.get_video_for_quality(
                videos, ask=False, quality=quality
            )

    def test_inputstream_automatic_best_uses_master_for_supported_hevc(self):
        selected = self.select('enhanced_master.m3u8', inputstream=True)

        self.assertEqual('hls', selected['id'])
        self.assertEqual('', selected['url'])

    def test_global_adaptive_uses_master_for_supported_hevc(self):
        _Kodi.settings['video_quality'] = '3'

        selected = self.select(
            'enhanced_master.m3u8', inputstream=True, token=True,
            decoder_supported=True
        )

        self.assertEqual('hls', selected['id'])
        self.assertEqual('', selected['url'])

    def test_non_inputstream_automatic_best_selects_hevc_1440p(self):
        selected = self.select('enhanced_master.m3u8')

        self.assertEqual('1440p60_hevc', selected['id'])
        self.assertEqual(
            'https://video.example/hevc/1440p60/index.m3u8', selected['url']
        )

    def test_only_h264_selects_highest_video_rendition(self):
        selected = self.select('h264_only_master.m3u8')

        self.assertEqual('chunked', selected['id'])
        self.assertEqual('Source', selected['name'])

    def test_missing_hevc_token_falls_back_to_highest_h264(self):
        selected = self.select('enhanced_master.m3u8', token=False)

        self.assertEqual('1080p60', selected['id'])

    def test_hevc_token_without_decoder_falls_back_to_highest_h264(self):
        selected = self.select(
            'enhanced_master.m3u8', token=True, decoder_supported=False
        )

        self.assertEqual('1080p60', selected['id'])

    def test_missing_hevc_variant_falls_back_to_highest_h264(self):
        selected = self.select('h264_only_master.m3u8', token=True)

        self.assertEqual('chunked', selected['id'])

    def test_malformed_metadata_and_duplicates_are_deterministic(self):
        first = self.select('irregular_master.m3u8', token=False)
        second = self.select('irregular_master.m3u8', token=False)

        self.assertEqual(first, second)
        self.assertEqual(
            'https://video.example/h264/1080p60-missing-codecs/index.m3u8',
            first['url'],
        )

    def test_audio_only_rendition_is_never_selected(self):
        selected = self.select('h264_only_master.m3u8', token=False)

        self.assertNotEqual('audio_only', selected['id'])
        self.assertNotEqual('Audio Only', selected['name'])

    def test_explicit_manual_quality_remains_authoritative(self):
        selected = self.select(
            'enhanced_master.m3u8', inputstream=True, quality='720p60'
        )

        self.assertEqual('720p60', selected['id'])
        self.assertEqual(
            'https://video.example/h264/720p60/index.m3u8', selected['url']
        )


if __name__ == '__main__':
    unittest.main()
