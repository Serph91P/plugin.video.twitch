"""Tests for workflow YAML parsing and structural validation.

Verifies that all workflow files parse correctly with a real YAML
parser and have the expected structural elements.
"""

import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from tools.validate_workflows import (
        validate_all_workflows,
        validate_workflow_policy,
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

    def test_action_pins_have_readable_version_comments(self):
        pattern = re.compile(
            r'^\s*uses:\s+\S+@[0-9a-f]{40}\s+# v\d+(?:\.\d+)*\s*$'
        )
        workflows_dir = ROOT / '.github' / 'workflows'
        paths = list(workflows_dir.glob('*.yml'))
        paths.extend(workflows_dir.glob('*.yaml'))
        for path in paths:
            for line_number, line in enumerate(path.read_text().splitlines(), 1):
                if 'uses:' in line:
                    self.assertRegex(
                        line, pattern,
                        f'{path.name}:{line_number} action pin needs a '
                        'version comment',
                    )


@unittest.skipUnless(_HAS_YAML, 'pyyaml not installed')
class WorkflowPolicyTests(unittest.TestCase):
    def setUp(self):
        self.workflow = {
            'permissions': {'contents': 'read'},
            'jobs': {
                'test': {
                    'runs-on': 'ubuntu-latest',
                    'steps': [
                        {
                            'uses': 'actions/checkout@'
                            '34e114876b0b11c390a56381ad16ebd13914f8d5',
                        },
                    ],
                },
            },
        }

    def test_accepts_pinned_action_and_reviewed_permissions(self):
        errors = validate_workflow_policy(
            self.workflow, 'addon-validations.yml'
        )
        self.assertEqual(errors, [])

    def test_rejects_mutable_action_ref(self):
        self.workflow['jobs']['test']['steps'][0]['uses'] = (
            'actions/checkout@v4'
        )
        errors = validate_workflow_policy(
            self.workflow, 'addon-validations.yml'
        )
        self.assertTrue(any('40-character commit SHA' in e for e in errors))

    def test_rejects_short_action_sha(self):
        self.workflow['jobs']['test']['steps'][0]['uses'] = (
            'actions/checkout@34e1148'
        )
        errors = validate_workflow_policy(
            self.workflow, 'addon-validations.yml'
        )
        self.assertTrue(any('40-character commit SHA' in e for e in errors))

    def test_rejects_missing_top_level_permissions(self):
        del self.workflow['permissions']
        errors = validate_workflow_policy(
            self.workflow, 'addon-validations.yml'
        )
        self.assertTrue(any('top-level permissions' in e for e in errors))

    def test_rejects_overbroad_top_level_permissions(self):
        self.workflow['permissions']['contents'] = 'write'
        errors = validate_workflow_policy(
            self.workflow, 'addon-validations.yml'
        )
        self.assertTrue(any('permissions must be exactly' in e for e in errors))

    def test_rejects_overbroad_job_permission(self):
        self.workflow['jobs']['test']['permissions'] = {'contents': 'write'}
        errors = validate_workflow_policy(
            self.workflow, 'addon-validations.yml'
        )
        self.assertTrue(any('overbroad job permissions' in e for e in errors))

    def test_rejects_pip_self_upgrade(self):
        self.workflow['jobs']['test']['steps'].append({
            'run': 'python -m pip install --upgrade pip',
        })
        errors = validate_workflow_policy(
            self.workflow, 'addon-validations.yml'
        )
        self.assertTrue(any('pip self-upgrade' in e for e in errors))

    def test_rejects_pip_self_upgrade_command_variants(self):
        commands = (
            'pip install pip --upgrade',
            'python3 -m pip install -U "pip"',
        )
        for command in commands:
            with self.subTest(command=command):
                self.workflow['jobs']['test']['steps'].append({'run': command})
                errors = validate_workflow_policy(
                    self.workflow, 'addon-validations.yml'
                )
                self.assertTrue(any('pip self-upgrade' in e for e in errors))
                self.workflow['jobs']['test']['steps'].pop()

    def test_rejects_mutable_requirements_references(self):
        commands = (
            'python -m pip install -r requirements.txt',
            'pip install --require-hashes '
            '--requirement=https://example.com/requirements.txt',
        )
        for command in commands:
            with self.subTest(command=command):
                self.workflow['jobs']['test']['steps'].append({'run': command})
                errors = validate_workflow_policy(
                    self.workflow, 'addon-validations.yml'
                )
                self.assertTrue(any('mutable requirements' in e for e in errors))
                self.workflow['jobs']['test']['steps'].pop()

    def test_rejects_unlocked_index_packages(self):
        commands = (
            'python -m pip install pyyaml',
            'pip install "coverage==7.6.12"',
        )
        for command in commands:
            with self.subTest(command=command):
                self.workflow['jobs']['test']['steps'].append({'run': command})
                errors = validate_workflow_policy(
                    self.workflow, 'addon-validations.yml'
                )
                self.assertTrue(
                    any('hash-locked requirements' in e for e in errors)
                )
                self.workflow['jobs']['test']['steps'].pop()

    def test_rejects_unlocked_index_package_after_environment_assignment(self):
        self.workflow['jobs']['test']['steps'].append({
            'run': 'FOO=bar python -m pip install pyyaml',
        })
        errors = validate_workflow_policy(
            self.workflow, 'addon-validations.yml'
        )
        self.assertTrue(any('hash-locked requirements' in e for e in errors))

    def test_rejects_unlocked_index_package_after_env_command(self):
        self.workflow['jobs']['test']['steps'].append({
            'run': 'env FOO=bar python -m pip install pyyaml',
        })
        errors = validate_workflow_policy(
            self.workflow, 'addon-validations.yml'
        )
        self.assertTrue(any('hash-locked requirements' in e for e in errors))

    def test_rejects_unlocked_index_package_after_shell_chain(self):
        self.workflow['jobs']['test']['steps'].append({
            'run': 'true && python -m pip install pyyaml',
        })
        errors = validate_workflow_policy(
            self.workflow, 'addon-validations.yml'
        )
        self.assertTrue(any('hash-locked requirements' in e for e in errors))

    def test_rejects_mutable_requirements_after_line_continuation(self):
        self.workflow['jobs']['test']['steps'].append({
            'run': 'python -m pip install --require-hashes \\\n'
            '-r requirements.txt',
        })
        errors = validate_workflow_policy(
            self.workflow, 'addon-validations.yml'
        )
        self.assertTrue(any('mutable requirements' in e for e in errors))

    def test_rejects_unpinned_vcs_installs(self):
        commands = (
            'python -m pip install '
            'git+https://github.com/xbmc/addon-check.git',
            'pip install git+https://github.com/xbmc/addon-check.git@34e1148',
            'pip install hg+https://example.com/project',
        )
        for command in commands:
            with self.subTest(command=command):
                self.workflow['jobs']['test']['steps'].append({'run': command})
                errors = validate_workflow_policy(
                    self.workflow, 'addon-validations.yml'
                )
                self.assertTrue(
                    any('full 40-character commit SHA' in e for e in errors)
                )
                self.workflow['jobs']['test']['steps'].pop()

    def test_rejects_vcs_dependency_resolution(self):
        self.workflow['jobs']['test']['steps'].append({
            'run': 'python -m pip install '
            'git+https://github.com/xbmc/addon-check.git@'
            '0123456789abcdef0123456789abcdef01234567',
        })
        errors = validate_workflow_policy(
            self.workflow, 'addon-validations.yml'
        )
        self.assertTrue(any('--no-deps' in e for e in errors))

    def test_rejects_vcs_build_isolation(self):
        self.workflow['jobs']['test']['steps'].append({
            'run': 'python -m pip install --no-deps '
            'git+https://github.com/xbmc/addon-check.git@'
            '0123456789abcdef0123456789abcdef01234567',
        })
        errors = validate_workflow_policy(
            self.workflow, 'addon-validations.yml'
        )
        self.assertTrue(any('--no-build-isolation' in e for e in errors))

    def test_rejects_packages_mixed_with_immutable_inputs(self):
        commands = (
            'pip install --require-hashes '
            '-r .github/workflow-requirements/test.txt pyyaml',
            'pip install --no-deps '
            'git+https://github.com/xbmc/addon-check.git@'
            '0123456789abcdef0123456789abcdef01234567 coverage',
        )
        for command in commands:
            with self.subTest(command=command):
                self.workflow['jobs']['test']['steps'].append({'run': command})
                errors = validate_workflow_policy(
                    self.workflow, 'addon-validations.yml'
                )
                self.assertTrue(
                    any('hash-locked requirements' in e for e in errors)
                )
                self.workflow['jobs']['test']['steps'].pop()

    def test_rejects_wrapped_pip_install(self):
        """Reject pip installs wrapped in command/exec/env/shell prefixes."""
        commands = (
            # command/exec plain
            'command pip install pyyaml',
            'exec pip install pyyaml',
            'command python -m pip install pyyaml',
            'exec python3 -m pip install pyyaml',
            # command/exec with options and -- separator
            'command -- pip install pyyaml',
            'command -p -- pip install pyyaml',
            'exec -- pip install pyyaml',
            'exec -a installer pip install pyyaml',
            'exec -a "my installer" pip install pyyaml',
            # env with assignments, -i, --ignore-environment, and -- separator
            'env FOO=bar command pip install pyyaml',
            'env FOO=bar -- pip install pyyaml',
            'env -- pip install pyyaml',
            'env -i pip install pyyaml',
            'env --ignore-environment pip install pyyaml',
            # shell wrappers: bash/sh -c and option clusters containing c
            'bash -c "pip install pyyaml"',
            'bash -c \'pip install pyyaml\'',
            'sh -c "pip install pyyaml"',
            'sh -c \'python -m pip install pyyaml\'',
            'bash -c "exec pip install pyyaml"',
            'bash -lc "pip install pyyaml"',
            'bash -ec "pip install pyyaml"',
            'sh -lc "pip install pyyaml"',
            'bash -c -l "pip install pyyaml"',
            # bash long options before -c
            'bash --noprofile -c "pip install pyyaml"',
            'bash --norc -c "pip install pyyaml"',
            'bash --noprofile --norc -c "pip install pyyaml"',
            # chained commands
            'true && command pip install pyyaml',
            'true && exec python -m pip install pyyaml',
        )
        for command in commands:
            with self.subTest(command=command):
                self.workflow['jobs']['test']['steps'].append({'run': command})
                errors = validate_workflow_policy(
                    self.workflow, 'addon-validations.yml'
                )
                self.assertTrue(
                    any('hash-locked requirements' in e for e in errors),
                    f'wrapped pip not rejected: {command}'
                )
                self.workflow['jobs']['test']['steps'].pop()

    def test_accepts_hash_locked_requirements(self):
        self.workflow['jobs']['test']['steps'].append({
            'run': 'python3 -m pip install '
            '-r ".github/workflow-requirements/test.txt" --require-hashes',
        })
        errors = validate_workflow_policy(
            self.workflow, 'addon-validations.yml'
        )
        self.assertEqual(errors, [])

    def test_accepts_full_sha_vcs_install_without_dependencies(self):
        self.workflow['jobs']['test']['steps'].append({
            'run': 'pip install --no-build-isolation '
            'git+https://github.com/xbmc/addon-check.git@'
            '0123456789abcdef0123456789abcdef01234567 --no-deps',
        })
        errors = validate_workflow_policy(
            self.workflow, 'addon-validations.yml'
        )
        self.assertEqual(errors, [])

    def test_validate_all_workflows_applies_policy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / 'addon-validations.yml'
            path.write_text(
                'on: push\n'
                'permissions:\n'
                '  contents: read\n'
                'jobs:\n'
                '  test:\n'
                '    runs-on: ubuntu-latest\n'
                '    steps:\n'
                '      - uses: actions/checkout@v4\n',
                encoding='utf-8',
            )
            _, errors = validate_all_workflows(temp_dir)
        self.assertTrue(any('40-character commit SHA' in e for e in errors))


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

    def test_has_validation_step(self):
        job = self.data['jobs']['validated-publication']
        step_names = [s.get('name', '') for s in job['steps']]
        has_validation = any('valid' in n.lower() for n in step_names)
        self.assertTrue(has_validation,
                        'no validation step found in notify workflow')

    def test_uses_github_script(self):
        job = self.data['jobs']['validated-publication']
        step_uses = [s.get('uses', '') for s in job['steps']]
        has_script = any('github-script' in u for u in step_uses)
        self.assertTrue(has_script,
                        'notify workflow must use github-script action')

    def test_permissions_declared(self):
        self.assertIn('permissions', self.data)
        perms = self.data['permissions']
        self.assertIn('actions', perms)
        self.assertEqual(perms['actions'], 'read')

    def test_workflow_run_trigger(self):
        trigger_key = 'on' if 'on' in self.data else True
        trigger = self.data[trigger_key]
        self.assertIn('workflow_run', trigger)
        wr = trigger['workflow_run']
        self.assertIn('Add-on Validations', wr['workflows'])
        self.assertIn('completed', wr['types'])

    def test_job_has_if_condition(self):
        job = self.data['jobs']['validated-publication']
        self.assertIn('if', job, 'job must have an if gate')


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
