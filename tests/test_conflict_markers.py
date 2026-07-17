"""Tests for conflict marker scanning.

Verifies that the conflict marker scan only matches anchored Git
conflict markers and does not match decorative XML separators or
its own workflow pattern.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LT = '<'
GT = '>'
EQ = '='

def _mk_markers(left, mid, right):
    return f'{LT*7} {left}\n{EQ*7}\n{GT*7} {right}\n'


class ConflictMarkerScanTests(unittest.TestCase):
    def setUp(self):
        self.pattern = re.compile(r'^<<<<<<<|^=======|^>>>>>>>', re.MULTILINE)

    def test_anchored_pattern_matches_git_markers(self):
        """The anchored pattern matches lines that are exactly conflict markers."""
        test_content = "some code\n" + _mk_markers('HEAD', 'ours', 'theirs') + "more code\n"
        matches = self.pattern.findall(test_content)
        self.assertEqual(len(matches), 3)

    def test_anchored_pattern_rejects_decorative_xml(self):
        """The anchored pattern does not match decorative XML separators."""
        test_content = '<!-- ' + EQ*20 + ' Login ' + EQ*20 + ' -->\n'
        test_content += '<settings>\n'
        test_content += '    <!-- ' + EQ*20 + ' Main Menu ' + EQ*20 + ' -->\n'
        test_content += '</settings>\n'
        matches = self.pattern.findall(test_content)
        self.assertEqual(len(matches), 0)

    def test_anchored_pattern_rejects_workflow_pattern(self):
        """The anchored pattern does not match the workflow grep command itself."""
        test_content = "if grep -rn '" + LT*7 + "\\|" + EQ*7 + "\\|" + GT*7 + "' .; then\n"
        test_content += '  echo "CONFLICT MARKERS FOUND"\n'
        test_content += '  exit 1\n'
        test_content += 'fi\n'
        matches = self.pattern.findall(test_content)
        self.assertEqual(len(matches), 0)

    def test_anchored_pattern_rejects_inline_markers(self):
        """The anchored pattern does not match conflict markers embedded in text."""
        test_content = "# This is not a real conflict: " + EQ*7 + " or " + LT*7 + " or " + GT*7 + "\n"
        test_content += _mk_markers('HEAD', 'actual conflict', 'branch')
        matches = self.pattern.findall(test_content)
        self.assertEqual(len(matches), 3)


if __name__ == '__main__':
    unittest.main()
