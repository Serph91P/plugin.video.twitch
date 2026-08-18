"""Regression contracts for issue #57 Usher audio-only policy."""

import ast
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / 'resources' / 'lib' / 'twitch_addon' / 'addon' / 'api.py'


def _attribute_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _attribute_name(node.value)
        return '%s.%s' % (parent, node.attr) if parent else node.attr
    return None


def _functions_by_name():
    module = ast.parse(API.read_text(encoding='utf-8'))
    return {
        node.name: node for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef)
    }


def _usher_call(function):
    calls = [
        node for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and _attribute_name(node.func).startswith('self.usher.')
    ]
    if len(calls) != 1:
        raise AssertionError('%s should contain exactly one Usher call' % function.name)
    return calls[0]


class Issue57AudioOnlyPolicyTests(unittest.TestCase):
    def test_video_seams_preserve_existing_contract_and_disable_audio_only(self):
        functions = _functions_by_name()
        expected = {
            'get_live': {
                'usher_method': 'self.usher.live',
                'identifier': 'name',
                'keywords': ['headers', 'low_latency', 'allow_audio_only'],
                'decorators': ['api_error_handler', 'cache.cache_method'],
            },
            'live_request': {
                'usher_method': 'self.usher.live_request',
                'identifier': 'name',
                'keywords': [
                    'supported_codecs', 'headers', 'low_latency',
                    'allow_audio_only',
                ],
                'decorators': [
                    'api_error_handler', 'api_error_handler',
                    'cache.cache_method',
                ],
            },
            'get_vod': {
                'usher_method': 'self.usher.video',
                'identifier': 'video_id',
                'keywords': ['headers', 'allow_audio_only'],
                'decorators': ['api_error_handler', 'cache.cache_method'],
            },
            'video_request': {
                'usher_method': 'self.usher.video_request',
                'identifier': 'video_id',
                'keywords': [
                    'supported_codecs', 'headers', 'allow_audio_only',
                ],
                'decorators': ['api_error_handler', 'cache.cache_method'],
            },
        }

        for name, contract in expected.items():
            with self.subTest(seam=name):
                function = functions[name]
                decorators = [_attribute_name(item.func) if isinstance(item, ast.Call)
                              else _attribute_name(item) for item in function.decorator_list]
                self.assertEqual(decorators, contract['decorators'])

                call = _usher_call(function)
                self.assertEqual(_attribute_name(call.func), contract['usher_method'])
                self.assertEqual(len(call.args), 1)
                self.assertEqual(_attribute_name(call.args[0]), contract['identifier'])
                self.assertEqual(
                    [keyword.arg for keyword in call.keywords],
                    contract['keywords'],
                )

                keyword_values = {
                    keyword.arg: keyword.value for keyword in call.keywords
                }
                self.assertEqual(
                    _attribute_name(keyword_values['headers'].func),
                    'self.get_private_credential_headers',
                )
                self.assertFalse(keyword_values['headers'].args)
                self.assertIsInstance(keyword_values['allow_audio_only'], ast.Constant)
                self.assertIs(keyword_values['allow_audio_only'].value, False)

                if 'low_latency' in keyword_values:
                    self.assertEqual(
                        _attribute_name(keyword_values['low_latency']),
                        'low_latency',
                    )
                if 'supported_codecs' in keyword_values:
                    self.assertIsInstance(keyword_values['supported_codecs'], ast.Constant)
                    self.assertEqual(
                        keyword_values['supported_codecs'].value,
                        'av1,h265,h264',
                    )

                returns = [
                    node for node in function.body if isinstance(node, ast.Return)
                ]
                self.assertEqual(len(returns), 1)
                result = returns[0].value
                self.assertIsInstance(result, ast.Call)
                self.assertEqual(_attribute_name(result.func), 'self.error_check')
                self.assertEqual(_attribute_name(result.args[0]), 'results')
                self.assertEqual(
                    [(keyword.arg, keyword.value.value) for keyword in result.keywords],
                    [('private', True)],
                )

    def test_addon_requires_module_305_and_records_issue_57(self):
        root = ET.parse(ROOT / 'addon.xml').getroot()
        dependencies = {
            item.get('addon'): item.get('version')
            for item in root.findall('./requires/import')
        }
        self.assertEqual(dependencies['script.module.python.twitch'], '3.0.5')

        news = root.findtext('.//news') or ''
        self.assertIn(
            '[fix] Exclude audio-only Twitch renditions from video playback',
            news,
        )


if __name__ == '__main__':
    unittest.main()
