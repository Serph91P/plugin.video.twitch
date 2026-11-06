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

    def test_automatic_best_ignores_resolution_markers_in_signed_urls(self):
        source = {
            'id': 'chunked', 'name': 'Source', 'resolution': '1664x936',
            'codecs': 'avc1.4D402A,mp4a.40.2', 'bandwidth': 9238982,
            'fps': 60, 'url': 'https://video.example/source/index.m3u8',
        }
        for name, resolution, marker in (
            ('360p', '640x360', '951p'),
            ('480p', '852x480', '2358p'),
            ('480p', '852x480', '3840x2160'),
        ):
            with self.subTest(marker=marker):
                lower = dict(
                    source, id=name, name=name, resolution=resolution,
                    bandwidth=1427999,
                    url='https://video.example/opaque-%s-value/index.m3u8' % marker,
                )
                with patch(__name__ + '.parse_master_playlist', return_value=[source, lower]):
                    selected = self.select('unused', token=False)
                self.assertEqual('chunked', selected['id'])

    def test_hevc_selection_ignores_resolution_markers_in_urls(self):
        videos = parse_master_playlist('enhanced_master.m3u8')
        lower = dict(
            videos[-1], id='720p60_hevc', name='720p60 (HEVC)',
            resolution='1280x720',
            url='https://video.example/opaque-9999p-value/index.m3u8',
        )
        selected = self.converter.select_best_hevc_video(videos + [lower])
        self.assertEqual('1440p60_hevc', selected['id'])

    def test_resolution_metadata_takes_precedence_over_labels(self):
        video = {
            'id': '2160p60', 'name': '2160p', 'resolution': '1280x720',
            'url': 'https://video.example/index.m3u8',
        }
        self.assertEqual(720, self.converter.video_sort_key(video)[0])

    def test_missing_or_malformed_resolution_falls_back_to_labels(self):
        for resolution in (None, '', 'invalid'):
            for labels in ({'id': '720p60'}, {'name': '1280x720'}):
                with self.subTest(resolution=resolution, labels=labels):
                    video = dict(labels, resolution=resolution,
                                 url='https://video.example/9999p/index.m3u8')
                    self.assertEqual(720, self.converter.video_sort_key(video)[0])

    def test_url_alone_is_not_resolution_metadata(self):
        self.assertEqual(0, self.converter.video_sort_key({
            'url': 'https://video.example/3840x2160/9999p/index.m3u8',
        })[0])

    def test_codec_markers_in_opaque_urls_do_not_exclude_h264_source(self):
        for marker in ('h265', 'hevc', 'hvc1', 'hev1'):
            with self.subTest(marker=marker):
                videos = parse_master_playlist('h264_only_master.m3u8')
                source = next(v for v in videos if v['id'] == 'chunked')
                source['url'] = 'https://video.example/opaque-%s-value/index.m3u8' % marker
                self.assertFalse(self.converter.is_hevc_video(source))
                with patch(__name__ + '.parse_master_playlist', return_value=videos):
                    selected = self.select('unused', token=False)
                self.assertEqual('chunked', selected['id'])

    def test_codec_metadata_takes_precedence_over_labels(self):
        self.assertFalse(self.converter.is_hevc_video({
            'id': '1080p60_hevc', 'name': '1080p HEVC',
            'codecs': 'avc1.64002A,mp4a.40.2',
        }))

    def test_missing_codec_metadata_falls_back_to_labels_not_urls(self):
        self.assertTrue(self.converter.is_hevc_video({'id': '1440p60_hevc'}))
        self.assertFalse(self.converter.is_hevc_video({
            'id': '1080p60', 'url': 'https://video.example/hevc/index.m3u8',
        }))


if __name__ == '__main__':
    unittest.main()
