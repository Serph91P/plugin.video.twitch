"""Tests for workflow YAML parsing and structural validation.

Verifies that all workflow files parse correctly with a real YAML
parser and have the expected structural elements.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from tools.validate_workflows import (
        validate_all_workflows,
        validate_workflow_structure,
        parse_yaml_file,
    )
    _HAS_YAML = parse_yaml_file.__module__ and True
except ImportError:
    _HAS_YAML = False

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


WORKFLOWS = [
    'addon-validations.yml',
    'make-release.yml',
    'notify-repository.yml',
    'deploy-pages.yml',
]


@unittest.skipUnless(_HAS_YAML, 'pyyaml not installed')
class WorkflowParsingTests(unittest.TestCase):
    def test_all_workflows_parse(self):
        _, errors = validate_all_workflows()
        self.assertEqual(errors, [], f'workflow parse errors: {errors}')

    def test_addon_validations_exists(self):
        path = ROOT / '.github' / 'workflows' / 'addon-validations.yml'
        self.assertTrue(path.exists())

    def test_make_release_exists(self):
        path = ROOT / '.github' / 'workflows' / 'make-release.yml'
        self.assertTrue(path.exists())

    def test_notify_repository_exists(self):
        path = ROOT / '.github' / 'workflows' / 'notify-repository.yml'
        self.assertTrue(path.exists())


@unittest.skipUnless(_HAS_YAML, 'pyyaml not installed')
class AddonValidationsStructureTests(unittest.TestCase):
    def setUp(self):
        self.data = parse_yaml_file(
            ROOT / '.github' / 'workflows' / 'addon-validations.yml'
        )

    def test_has_on_trigger(self):
        has_trigger = 'on' in self.data or True in self.data
        self.assertTrue(has_trigger, 'missing "on" trigger')

    def test_has_jobs(self):
        self.assertIn('jobs', self.data)

    def test_has_test_job(self):
        self.assertIn('test', self.data['jobs'])

    def test_test_job_has_steps(self):
        job = self.data['jobs']['test']
        self.assertIn('steps', job)

    def test_python_matrix_includes_3_11_3_12_3_13(self):
        job = self.data['jobs']['test']
        strategy = job.get('strategy', {})
        matrix = strategy.get('matrix', {})
        python_versions = matrix.get('python-version', [])
        for ver in ['3.11', '3.12', '3.13']:
            self.assertIn(ver, python_versions,
                          f'Python {ver} missing from matrix')

    def test_has_coverage_step(self):
        job = self.data['jobs']['test']
        step_names = [s.get('name', '') for s in job['steps']]
        has_coverage = any('coverage' in n.lower() for n in step_names)
        self.assertTrue(has_coverage, 'no coverage step found')

    def test_has_package_validation_job(self):
        self.assertIn('package', self.data['jobs'])

    def test_has_kodi_checker_job(self):
        self.assertIn('kodi-check', self.data['jobs'])

    def test_has_workflow_validation_job(self):
        self.assertIn('workflow-validation', self.data['jobs'])

    def test_has_security_scan_job(self):
        self.assertIn('security-scan', self.data['jobs'])

    def test_kodi_checker_uses_package_artifact(self):
        """Kodi checker should download and extract the package artifact, not checkout the full repo."""
        job = self.data['jobs']['kodi-check']
        step_names = [s.get('name', '') for s in job['steps']]
        step_runs = [s.get('run', '') for s in job['steps']]
        has_download = any('download' in n.lower() for n in step_names)
        has_unzip = any('unzip' in r.lower() for r in step_runs)
        self.assertTrue(has_download or has_unzip,
                        'kodi-check should download and extract the package artifact')
        has_checkout = any('checkout' in n.lower() for n in step_names)
        self.assertFalse(has_checkout,
                         'kodi-check should not checkout the full repository')

    def test_kodi_checker_extracts_rooted_archive_without_double_nesting(self):
        job = self.data['jobs']['kodi-check']
        extract_step = next(
            step for step in job['steps']
            if step.get('name') == 'Extract runtime package'
        )
        run = extract_step.get('run', '')
        self.assertIn('unzip dist/*.zip -d .', run)
        self.assertNotIn('mkdir -p plugin.video.twitch', run)


@unittest.skipUnless(_HAS_YAML, 'pyyaml not installed')
class MakeReleaseStructureTests(unittest.TestCase):
    def setUp(self):
        self.data = parse_yaml_file(
            ROOT / '.github' / 'workflows' / 'make-release.yml'
        )

    def test_has_on_trigger(self):
        has_trigger = 'on' in self.data or True in self.data
        self.assertTrue(has_trigger, 'missing "on" trigger')

    def test_has_jobs(self):
        self.assertIn('jobs', self.data)

    def test_has_release_job(self):
        self.assertIn('release', self.data['jobs'])

    def test_has_package_build_step(self):
        job = self.data['jobs']['release']
        step_names = [s.get('name', '') for s in job['steps']]
        has_package = any('package' in n.lower() or 'zip' in n.lower()
                         for n in step_names)
        self.assertTrue(has_package, 'no package/zip step found')

    def test_has_sha_validation_step(self):
        job = self.data['jobs']['release']
        step_names = [s.get('name', '') for s in job['steps']]
        has_validation = any('valid' in n.lower() or 'sha' in n.lower()
                           for n in step_names)
        self.assertTrue(has_validation, 'no SHA validation step found')


@unittest.skipUnless(_HAS_YAML, 'pyyaml not installed')
class MakeReleaseQuotingTests(unittest.TestCase):
    """Regression tests for shell quoting in make-release.yml."""

    def setUp(self):
        self.raw = (
            ROOT / '.github' / 'workflows' / 'make-release.yml'
        ).read_text()

    def test_release_status_github_output_is_quoted(self):
        """SC2086: every shell echo redirecting to GITHUB_OUTPUT must double-quote it."""
        import re
        matches = re.findall(r'echo\s+.*>>\s*(\$GITHUB_OUTPUT|\$\{GITHUB_OUTPUT\})', self.raw)
        self.assertEqual(matches, [], (
            'unquoted $GITHUB_OUTPUT redirects found; '
            'use >> "$GITHUB_OUTPUT" to prevent globbing and word splitting'
        ))


class NotifyRepositoryStructureTests(unittest.TestCase):
    def setUp(self):
        self.data = parse_yaml_file(
            ROOT / '.github' / 'workflows' / 'notify-repository.yml'
        )

    def test_has_sha_validation_step(self):
        job = self.data['jobs']['version-and-notify']
        step_names = [s.get('name', '') for s in job['steps']]
        has_validation = any('valid' in n.lower() or 'sha' in n.lower()
                           for n in step_names)
        self.assertTrue(has_validation,
                        'no SHA validation step found in notify workflow')


@unittest.skipUnless(_HAS_YAML, 'pyyaml not installed')
class KodiCheckerTimeoutTests(unittest.TestCase):
    """Tests that the Kodi checker job has a bounded timeout."""

    def setUp(self):
        self.data = parse_yaml_file(
            ROOT / '.github' / 'workflows' / 'addon-validations.yml'
        )

    def test_kodi_checker_job_has_timeout(self):
        job = self.data['jobs']['kodi-check']
        self.assertIn('timeout-minutes', job,
            'kodi-check job must have timeout-minutes')


@unittest.skipUnless(_HAS_YAML, 'pyyaml not installed')
class SecurityScanCoverageTests(unittest.TestCase):
    """Tests that security/conflict/unicode scans do not exclude tests."""

    def setUp(self):
        self.data = parse_yaml_file(
            ROOT / '.github' / 'workflows' / 'addon-validations.yml'
        )
        self.job = self.data['jobs']['security-scan']

    def _step_run(self, name_fragment):
        for step in self.job['steps']:
            if name_fragment.lower() in step.get('name', '').lower():
                return step.get('run', '')
        return ''

    def test_conflict_scan_includes_tests(self):
        run = self._step_run('conflict')
        self.assertNotIn("--exclude-dir='tests'", run,
            'conflict marker scan must not exclude tests')

    def test_secret_scan_includes_tests(self):
        run = self._step_run('secret')
        self.assertNotIn("'tests'", run,
            'secret scan must not exclude tests directory')

    def test_unicode_scan_includes_tests(self):
        run = self._step_run('unicode')
        self.assertNotIn("--exclude-dir='tests'", run,
            'unicode scan must not exclude tests')

    def test_secret_scan_pattern_is_bounded(self):
        """Secret scan regex must have a max length bound, not unbounded {8,}."""
        run = self._step_run('secret')
        self.assertNotIn('{8,}',
            run.replace('{8,128}', ''),
            'secret scan pattern must not use unbounded {8,}; use {8,128}')


if __name__ == '__main__':
    unittest.main()
