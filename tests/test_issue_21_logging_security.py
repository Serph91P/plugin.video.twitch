import importlib.util
import sys
import types
import unittest
from pathlib import Path
from urllib.parse import quote_plus


ROOT = Path(__file__).resolve().parents[1]
LOG_UTILS = (
    ROOT / 'resources' / 'lib' / 'twitch_addon' / 'addon' / 'common'
    / 'log_utils.py'
)


def load_log_utils():
    for name in ('twitch_addon', 'twitch_addon.addon', 'twitch_addon.addon.common'):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    records = []
    kodi = types.ModuleType('twitch_addon.addon.common.kodi')
    kodi.is_unicode = lambda value: False
    kodi.get_name = lambda: 'Twitch'
    kodi.__log = lambda message, level: records.append(message)
    sys.modules[kodi.__name__] = kodi

    xbmc = types.ModuleType('xbmc')
    for index, name in enumerate(
        ('LOGDEBUG', 'LOGERROR', 'LOGFATAL', 'LOGINFO', 'LOGNONE', 'LOGWARNING')
    ):
        setattr(xbmc, name, index)
    sys.modules[xbmc.__name__] = xbmc

    spec = importlib.util.spec_from_file_location(
        'twitch_addon.addon.common.log_utils', LOG_UTILS
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, records


class Issue21LoggingSecurityTests(unittest.TestCase):
    def setUp(self):
        self.log_utils, self.records = load_log_utils()

    def assert_sentinel_is_redacted(self, message, sentinel, category):
        self.log_utils.log(message)

        leaked = any(sentinel in record for record in self.records)
        self.assertFalse(leaked, '%s reached the Kodi log' % category)

    def test_raw_authorization_value_is_redacted(self):
        sentinel = 'ISSUE21_RAW_AUTHORIZATION_SENTINEL'
        self.assert_sentinel_is_redacted(
            "append_headers called with: {'Authorization': 'Bearer %s'}" % sentinel,
            sentinel,
            'raw Authorization value',
        )

    def test_website_token_is_redacted(self):
        sentinel = 'ISSUE21_WEBSITE_TOKEN_SENTINEL'
        self.assert_sentinel_is_redacted(
            "append_headers called with: {'Authorization': 'OAuth %s'}" % sentinel,
            sentinel,
            'website token',
        )

    def test_authenticated_proxy_credentials_are_redacted(self):
        username = 'ISSUE21_PROXY_USERNAME_SENTINEL'
        pw = 'ISSUE21_PROXY_PASSWORD_SENTINEL'
        self.log_utils.log(
            'Adding proxy to video URL: http://%s:%s@proxy.example:8080'
            % (username, pw)
        )

        leaked = any(
            username in record or pw in record for record in self.records
        )
        self.assertFalse(leaked, 'authenticated proxy credentials reached the Kodi log')

    def test_encoded_proxy_credentials_are_redacted(self):
        username = 'ISSUE21_ENCODED_PROXY_USERNAME_SENTINEL'
        pw = 'ISSUE21_ENCODED_PROXY_PASSWORD_SENTINEL'
        proxy = quote_plus(
            'http://%s:%s@proxy.example:8080' % (username, pw)
        )
        self.log_utils.log(
            'Playback URL: https://video.example/live.m3u8|http-proxy=%s' % proxy
        )

        leaked = any(
            username in record or pw in record for record in self.records
        )
        self.assertFalse(
            leaked, 'encoded authenticated proxy credentials reached the Kodi log'
        )

    def test_raw_proxy_credentials_with_reserved_characters_are_redacted(self):
        username = 'ISSUE21_RESERVED_PROXY_USERNAME_SENTINEL'
        pw = 'ISSUE21_RESERVED/PROXY@PASSWORD_SENTINEL'
        self.log_utils.log(
            'Adding proxy to video URL: http://%s:%s@proxy.example:8080'
            % (username, pw)
        )

        leaked = any(
            username in record or 'PASSWORD_SENTINEL' in record
            for record in self.records
        )
        self.assertFalse(
            leaked, 'reserved raw proxy credentials reached the Kodi log'
        )

    def test_encoded_proxy_credentials_with_reserved_characters_are_redacted(self):
        username = 'ISSUE21_ENCODED_RESERVED_PROXY_USERNAME_SENTINEL'
        pw = 'ISSUE21_ENCODED_RESERVED@PROXY_PASSWORD_SENTINEL'
        proxy = quote_plus(
            'http://%s:%s@proxy.example:8080' % (username, pw)
        )
        self.log_utils.log(
            'Playback URL: https://video.example/live.m3u8|http-proxy=%s' % proxy
        )

        leaked = any(
            username in record or 'PROXY_PASSWORD_SENTINEL' in record
            for record in self.records
        )
        self.assertFalse(
            leaked, 'reserved encoded proxy credentials reached the Kodi log'
        )

    def test_signed_url_token_is_redacted_but_location_is_preserved(self):
        sentinel = 'ISSUE21_SIGNED_URL_TOKEN_SENTINEL'
        self.assert_sentinel_is_redacted(
            'Attempting playback @ '
            'https://usher.example/channel/index.m3u8?token=%s&allow_source=true'
            % sentinel,
            sentinel,
            'signed playback URL token',
        )
        self.assertTrue(
            any('usher.example/channel/index.m3u8' in record for record in self.records),
            'non-sensitive playback location was removed from diagnostics',
        )

    def test_signature_query_parameter_is_redacted(self):
        sentinel = 'ISSUE21_SIGNATURE_SENTINEL'
        self.assert_sentinel_is_redacted(
            'Playback request: https://usher.example/live.m3u8?allow_source=true&sig=%s'
            % sentinel,
            sentinel,
            'signature query parameter',
        )


if __name__ == '__main__':
    unittest.main()
