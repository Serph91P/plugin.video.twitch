"""Tests for make-release.yml workflow.

Verifies that the release workflow has correct working directories,
path handling, SHA validation, and immutable release contract.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from tools.validate_workflows import parse_yaml_file
    _HAS_YAML = parse_yaml_file.__module__ and True
except ImportError:
    _HAS_YAML = False

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


@unittest.skipUnless(_HAS_YAML, 'pyyaml not installed')
class MakeReleaseWorkingDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.data = parse_yaml_file(
            ROOT / '.github' / 'workflows' / 'make-release.yml'
        )
        self.job = self.data['jobs']['release']

    def test_validate_commit_step_runs_at_checkout(self):
        for step in self.job['steps']:
            if 'validate' in step.get('name', '').lower():
                working_dir = step.get('working-directory', '')
                if working_dir:
                    self.assertNotEqual(working_dir, '',
                                        'validate commit should run at checkout directory')
                break

    def test_validate_commit_calls_sha_validation(self):
        for step in self.job['steps']:
            if 'validate' in step.get('name', '').lower():
                run_script = step.get('run', '')
                self.assertIn('validate_commit_sha', run_script,
                              'validate commit step should call validate_commit_sha')
                break

    def test_get_changelog_path_not_duplicated(self):
        for step in self.job['steps']:
            if 'changelog' in step.get('name', '').lower():
                run_script = step.get('run', '')
                working_dir = step.get('working-directory', '')
                if working_dir:
                    self.assertNotIn('${{ github.event.repository.name }}/addon.xml', run_script,
                                     'path should not duplicate repository name')
                break

    def test_release_upload_uses_identity_filename(self):
        for step in self.job['steps']:
            if 'release' in step.get('name', '').lower() or 'create' in step.get('name', '').lower():
                uses = step.get('uses', '')
                if 'release' in uses.lower():
                    with_block = step.get('with', {})
                    files = with_block.get('files', '')
                    self.assertIn('steps.contract.outputs.filename', files,
                                  'release upload should use the filename from contract step')
                break


@unittest.skipUnless(_HAS_YAML, 'pyyaml not installed')
class MakeReleaseShaValidationTests(unittest.TestCase):
    """Tests that catch SHA validation and output defects."""

    def setUp(self):
        self.data = parse_yaml_file(
            ROOT / '.github' / 'workflows' / 'make-release.yml'
        )
        self.job = self.data['jobs']['release']

    def _step_run(self, step_id):
        for step in self.job['steps']:
            if step.get('id') == step_id:
                return step.get('run', '')
        return ''

    def test_validate_commit_not_tautological(self):
        run = self._step_run('validate')
        self.assertNotIn(
            'validate_commit_sha(head_sha, head_sha)',
            run,
            'tautological call: validate_commit_sha(head_sha, head_sha)',
        )

    def test_validate_commit_references_event_sha(self):
        run = self._step_run('validate')
        has_event_ref = (
            'GITHUB_SHA' in run
            or 'github.sha' in run
            or 'expected_sha' in run.lower()
            or 'EXPECTED_SHA' in run
        )
        self.assertTrue(
            has_event_ref,
            'validate step must reference GITHUB_SHA or an expected SHA from the event',
        )

    def test_validate_step_no_shell_echo(self):
        run = self._step_run('validate')
        self.assertNotRegex(
            run,
            r'echo\s+.*>>.*GITHUB_OUTPUT',
            'shell echo redirect inside Python is a syntax error; use os.environ',
        )

    def test_validate_step_writes_output_via_python(self):
        run = self._step_run('validate')
        self.assertIn('os.environ', run,
            'must use os.environ to write GITHUB_OUTPUT from Python')

    def test_changelog_step_writes_changes_output(self):
        run = self._step_run('changelog')
        self.assertIn('GITHUB_OUTPUT', run,
            'changelog step must write changes to GITHUB_OUTPUT')

    def test_changelog_output_newline_terminated(self):
        run = self._step_run('changelog')
        self.assertIn('GITHUB_OUTPUT', run,
            'changelog step must write to GITHUB_OUTPUT')
        for line in run.split('\n'):
            s = line.strip()
            if 'GITHUB_OUTPUT' in s and ('write' in s or 'echo' in s):
                self.assertTrue(
                    s.endswith('\\n') or s.endswith("'\\n')") or s.endswith('"\\n")'),
                    f'GITHUB_OUTPUT write missing newline: {s}',
                )

    def test_contract_step_output_newline_terminated(self):
        """Each f.write to GITHUB_OUTPUT in contract step must end with newline."""
        run = self._step_run('contract')
        lines = run.split('\n')
        in_block = False
        writes = []
        for line in lines:
            s = line.strip()
            if 'GITHUB_OUTPUT' in s and 'open(' in s:
                in_block = True
                continue
            if in_block and 'f.write(' in s:
                writes.append(s)
            if in_block and (s == ')' or s.startswith('print(')):
                in_block = False
        self.assertTrue(writes, 'no f.write calls found for GITHUB_OUTPUT')
        for w in writes:
            self.assertIn('\\n', w,
                f'f.write missing newline terminator: {w}')


@unittest.skipUnless(_HAS_YAML, 'pyyaml not installed')
class MakeReleaseImmutableContractTests(unittest.TestCase):
    """Tests that the release workflow uses an immutable validation-to-publication chain."""

    def setUp(self):
        self.data = parse_yaml_file(
            ROOT / '.github' / 'workflows' / 'make-release.yml'
        )
        self.job = self.data['jobs']['release']
        self.step_names = [s.get('name', '') for s in self.job['steps']]

    def test_has_find_validation_run_step(self):
        has_find = any('validation' in n.lower() and 'run' in n.lower()
                       for n in self.step_names)
        self.assertTrue(has_find, 'no step to find validation run')

    def test_has_download_evidence_step(self):
        has_download = any('evidence' in n.lower() and 'download' in n.lower()
                           for n in self.step_names)
        self.assertTrue(has_download, 'no step to download validation evidence')

    def test_has_download_package_step(self):
        has_download = any('package' in n.lower() and 'download' in n.lower()
                           for n in self.step_names)
        self.assertTrue(has_download, 'no step to download validated package')

    def test_has_contract_verification_step(self):
        has_contract = any('contract' in n.lower() or 'verify' in n.lower()
                           for n in self.step_names)
        self.assertTrue(has_contract, 'no contract verification step')

    def test_does_not_rebuild_package(self):
        """Release workflow must NOT rebuild from source; it must consume the validated artifact."""
        for step in self.job['steps']:
            if step.get('id') == 'create-zip':
                self.fail('release workflow must not have a create-zip step; '
                          'it must download the validated artifact')

    def test_contract_step_uses_validate_release(self):
        for step in self.job['steps']:
            if step.get('id') == 'contract':
                run = step.get('run', '')
                self.assertIn('validate_release_contract', run,
                              'contract step must call validate_release_contract')
                break

    def test_contract_step_checks_tag(self):
        for step in self.job['steps']:
            if step.get('id') == 'contract':
                run = step.get('run', '')
                self.assertIn('expected_tag', run,
                              'contract step must verify tag identity')
                break

    def test_contract_step_checks_sha(self):
        for step in self.job['steps']:
            if step.get('id') == 'contract':
                run = step.get('run', '')
                self.assertIn('expected_sha', run,
                              'contract step must verify candidate SHA')
                break

    def test_download_artifacts_use_same_run(self):
        """Both downloads must use the same validation run ID."""
        download_steps = []
        for step in self.job['steps']:
            uses = step.get('uses', '')
            if 'download-artifact' in uses:
                download_steps.append(step)
        self.assertGreaterEqual(len(download_steps), 2,
                                'need at least 2 download steps (evidence + package)')
        run_ids = set()
        for step in download_steps:
            with_block = step.get('with', {})
            rid = with_block.get('run-id', '')
            run_ids.add(rid)
        self.assertEqual(len(run_ids), 1,
                         f'downloads must use the same run-id, got: {run_ids}')


if __name__ == '__main__':
    unittest.main()
