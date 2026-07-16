"""Tests for staged branch coverage gate.

Verifies that the coverage gate tool correctly identifies critical
modules, has a measured threshold, produces valid results, and fails
closed when any declared critical module is absent.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.coverage_gate import (
    BRANCH_COVERAGE_THRESHOLD,
    CRITICAL_MODULES,
    _parse_coverage_report_modules,
    check_critical_module_presence,
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

    def test_kodi_module_is_in_critical_list(self):
        """kodi.py must be explicitly included even if unimported by tests."""
        modules = get_critical_modules()
        kodi_entry = 'resources/lib/twitch_addon/addon/common/kodi.py'
        self.assertIn(kodi_entry, modules,
                      f'{kodi_entry} must be in CRITICAL_MODULES')


class ModulePresenceTests(unittest.TestCase):
    """Every CRITICAL_MODULE must be present on disk or the gate fails closed."""

    def test_all_critical_modules_present_on_disk(self):
        missing = check_critical_module_presence(str(ROOT))
        self.assertEqual(missing, [],
                         f'critical modules missing from source: {missing}')

    def test_gate_fails_when_module_file_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = check_critical_module_presence(
                tmpdir, ['nonexistent_module.py'])
            self.assertEqual(missing, ['nonexistent_module.py'])

    def test_gate_fails_when_partial_modules_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            real_mod = ROOT / CRITICAL_MODULES[0]
            dest = Path(tmpdir) / CRITICAL_MODULES[0]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text('# stub\n')
            missing = check_critical_module_presence(
                tmpdir, [CRITICAL_MODULES[0], 'bogus/missing.py'])
            self.assertIn('bogus/missing.py', missing)
            self.assertNotIn(CRITICAL_MODULES[0], missing)

    def test_empty_module_list_returns_no_missing(self):
        missing = check_critical_module_presence(str(ROOT), [])
        self.assertEqual(missing, [])


class CoverageReportParsingTests(unittest.TestCase):
    """Verify that coverage report output is parsed correctly for module presence."""

    def test_parse_empty_report(self):
        result = _parse_coverage_report_modules('')
        self.assertEqual(result, set())

    def test_parse_report_with_modules(self):
        report = (
            'Name                                        Stmts   Miss  Cover   Missing\n'
            '---\n'
            'resources/lib/foo.py                           10      2    80%   5-6\n'
            'resources/lib/bar.py                            5      1    80%   3\n'
            'TOTAL                                         15      3    80%\n'
        )
        result = _parse_coverage_report_modules(report)
        self.assertIn('resources/lib/foo.py', result)
        self.assertIn('resources/lib/bar.py', result)
        self.assertNotIn('TOTAL', result)

    def test_absent_module_detected_in_report(self):
        report = (
            'Name                                        Stmts   Miss  Cover\n'
            '---\n'
            'resources/lib/other.py                          5      1    80%\n'
            'TOTAL                                           5      1    80%\n'
        )
        result = _parse_coverage_report_modules(report)
        for mod in CRITICAL_MODULES:
            full = os.path.join(str(ROOT), mod)
            self.assertNotIn(
                full, result,
                f'{mod} should not appear in mock report')

    def test_present_module_detected_in_report(self):
        full_path = os.path.join(str(ROOT), CRITICAL_MODULES[0])
        report = (
            'Name                                        Stmts   Miss  Cover\n'
            '---\n'
            f'{full_path}                                  10      2    80%\n'
            'TOTAL                                           10      2    80%\n'
        )
        result = _parse_coverage_report_modules(report)
        self.assertIn(full_path, result)


if __name__ == '__main__':
    unittest.main()
