"""Release notes contract tests."""

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseNewsContractTests(unittest.TestCase):
    def test_addon_news_matches_311_changelog(self):
        news = ET.parse(ROOT / 'addon.xml').getroot().findtext('.//news') or ''
        self.assertTrue(news)

        changelog = (ROOT / 'changelog.txt').read_text(encoding='utf-8')
        release_notes = changelog.split('3.1.11\n', 1)[1].split('\n3.', 1)[0]

        actual = [line.strip() for line in news.splitlines() if line.strip()]
        expected = [
            line.strip() for line in release_notes.splitlines() if line.strip()
        ]
        self.assertEqual(actual, expected)
