"""Tests for staged branch coverage gate.

Verifies that the coverage gate tool correctly identifies critical
modules, has a measured threshold, and produces valid results.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.coverage_gate import (
    BRANCH_COVERAGE_THRESHOLD,
    CRITICAL_MODULES,
    get_critical_modules,
    get_coverage_threshold,
)


class CoverageGateStructureTests(unittest.TestCase):
    def test_threshold_is_positive_integer(self):
        threshold = get_coverage_threshold()
        self.assertIsInstance(threshold, int)
        self.assertGreater(threshold, 0)
        self.assertLessEqual(threshold, 100)

    def test_threshold_is_conservative(self):
        threshold = get_coverage_threshold()
        self.assertLessEqual(threshold, 50,
                             'threshold should be conservative, not speculative')

    def test_critical_modules_list_nonempty(self):
        modules = get_critical_modules()
        self.assertTrue(len(modules) > 0)

    def test_critical_modules_are_tracked(self):
        modules = get_critical_modules()
        for mod in modules:
            full = ROOT / mod
            self.assertTrue(full.exists(), f'{mod} does not exist')

    def test_threshold_is_documented_constant(self):
        self.assertEqual(BRANCH_COVERAGE_THRESHOLD, get_coverage_threshold())

    def test_threshold_not_zero(self):
        self.assertNotEqual(BRANCH_COVERAGE_THRESHOLD, 0,
                            'zero threshold would pass with no tests')


if __name__ == '__main__':
    unittest.main()
