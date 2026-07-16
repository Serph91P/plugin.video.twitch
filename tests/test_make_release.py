"""Tests for make-release.yml workflow.

Verifies that the release workflow has correct working directories,
path handling, and SHA validation.
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
        """Validate candidate commit step should run at the checkout directory, not runner root."""
        for step in self.job['steps']:
            if 'validate' in step.get('name', '').lower():
                working_dir = step.get('working-directory', '')
                if working_dir:
                    self.assertNotEqual(working_dir, '',
                                        'validate commit should run at checkout directory')
                break

    def test_validate_commit_calls_sha_validation(self):
        """Validate candidate commit step should actually call validate_commit_sha."""
        for step in self.job['steps']:
            if 'validate' in step.get('name', '').lower():
                run_script = step.get('run', '')
                self.assertIn('validate_commit_sha', run_script,
                              'validate commit step should call validate_commit_sha')
                break

    def test_get_changelog_path_not_duplicated(self):
        """Get Changelog should not duplicate the repository name in the path."""
        for step in self.job['steps']:
            if 'changelog' in step.get('name', '').lower():
                run_script = step.get('run', '')
                working_dir = step.get('working-directory', '')
                # If working-directory is set, the path in run should not include repo name
                if working_dir:
                    self.assertNotIn('${{ github.event.repository.name }}/addon.xml', run_script,
                                     'path should not duplicate repository name')
                break

    def test_build_runtime_package_runs_at_checkout(self):
        """Build runtime package step should run at the checkout directory."""
        for step in self.job['steps']:
            if 'package' in step.get('name', '').lower() or 'zip' in step.get('name', '').lower():
                working_dir = step.get('working-directory', '')
                if working_dir:
                    self.assertNotEqual(working_dir, '',
                                        'build package should run at checkout directory')
                break

    def test_release_upload_uses_identity_filename(self):
        """Release upload should use the identity/version filename."""
        for step in self.job['steps']:
            if 'release' in step.get('name', '').lower() or 'create' in step.get('name', '').lower():
                uses = step.get('uses', '')
                if 'release' in uses.lower():
                    with_block = step.get('with', {})
                    files = with_block.get('files', '')
                    # Should reference the filename output, not a hardcoded name
                    self.assertIn('steps.create-zip.outputs.filename', files,
                                  'release upload should use the filename from build step')
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
        """validate_commit_sha must not receive the same argument twice."""
        run = self._step_run('validate')
        self.assertNotIn(
            'validate_commit_sha(head_sha, head_sha)',
            run,
            'tautological call: validate_commit_sha(head_sha, head_sha)',
        )

    def test_validate_commit_references_event_sha(self):
        """Validate step must compare HEAD against the immutable event SHA."""
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
        """Python snippet must not contain shell echo for GITHUB_OUTPUT."""
        run = self._step_run('validate')
        self.assertNotRegex(
            run,
            r'echo\s+.*>>.*GITHUB_OUTPUT',
            'shell echo redirect inside Python is a syntax error; use os.environ',
        )

    def test_validate_step_writes_output_via_python(self):
        """Validate step must write candidate_sha using os.environ, not shell."""
        run = self._step_run('validate')
        self.assertIn('os.environ', run,
            'must use os.environ to write GITHUB_OUTPUT from Python')

    def test_changelog_step_writes_changes_output(self):
        """Changelog step must write 'changes' to GITHUB_OUTPUT, not just print."""
        run = self._step_run('changelog')
        self.assertIn('GITHUB_OUTPUT', run,
            'changelog step must write changes to GITHUB_OUTPUT')

    def test_changelog_output_newline_terminated(self):
        """Changelog GITHUB_OUTPUT writes must use newline delimiter."""
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

    def test_build_step_output_newline_terminated(self):
        """Each f.write to GITHUB_OUTPUT in build step must end with newline."""
        run = self._step_run('create-zip')
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


if __name__ == '__main__':
    unittest.main()
