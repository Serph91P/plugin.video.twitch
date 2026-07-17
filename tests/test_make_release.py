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

    def test_validation_run_is_selected_for_exact_candidate_sha(self):
        find_step = next(
            step for step in self.job['steps']
            if step.get('id') == 'find-run'
        )
        script = find_step.get('with', {}).get('script', '')
        self.assertIn('run.head_sha === context.sha', script)

    def test_cross_run_artifact_downloads_are_authenticated(self):
        download_steps = [
            step for step in self.job['steps']
            if 'download-artifact' in step.get('uses', '')
        ]
        for step in download_steps:
            self.assertEqual(
                step.get('with', {}).get('github-token'),
                '${{ github.token }}',
            )


@unittest.skipUnless(_HAS_YAML, 'pyyaml not installed')
class MakeReleasePaginationTests(unittest.TestCase):
    """Tests that the find-run step paginates through multiple API pages."""

    def setUp(self):
        self.data = parse_yaml_file(
            ROOT / '.github' / 'workflows' / 'make-release.yml'
        )
        self.job = self.data['jobs']['release']

    def _find_run_script(self):
        for step in self.job['steps']:
            if step.get('id') == 'find-run':
                return step.get('with', {}).get('script', '')
        self.fail('no find-run step found')

    def test_find_run_paginates_eligible_branches(self):
        """The find-run script must iterate pages beyond the first API page."""
        script = self._find_run_script()
        has_page_loop = 'while' in script or 'page' in script.split('for')[0] if 'for' in script else False
        if not has_page_loop:
            has_page_loop = any(
                kw in script
                for kw in ('page', 'paginate', 'listWorkflowRuns')
            ) and ('for' in script or 'while' in script)
        # Verify: there must be a loop that references 'page' as a variable (not per_page)
        import re
        page_var = re.search(r'\bpage\b(?!:?\s*\d)', script.replace('per_page', ''))
        self.assertTrue(
            page_var is not None,
            'find-run script must contain a page variable for iterating API pages',
        )

    def test_find_run_uses_page_api_parameter(self):
        """The find-run script must pass page: N to listWorkflowRuns for pagination."""
        script = self._find_run_script()
        self.assertRegex(
            script,
            r'\bpage\s*:',
            'find-run script must pass a page: parameter to the GitHub API',
        )

    def test_find_run_has_bounded_page_limit(self):
        """Pagination must have a finite maximum page count to prevent runaway."""
        script = self._find_run_script()
        has_limit = any(
            kw in script
            for kw in ('maxPage', 'max_page', 'MAX_PAGE', 'maxPages', 'MAX_PAGES')
        )
        self.assertTrue(
            has_limit,
            'find-run script must define a maximum page bound',
        )


@unittest.skipUnless(_HAS_YAML, 'pyyaml not installed')
class MakeReleaseValidationBranchSearchTests(unittest.TestCase):
    """Tests that the find-run step searches all branches eligible for validation."""

    def setUp(self):
        self.release_data = parse_yaml_file(
            ROOT / '.github' / 'workflows' / 'make-release.yml'
        )
        self.validations_data = parse_yaml_file(
            ROOT / '.github' / 'workflows' / 'addon-validations.yml'
        )
        self.release_job = self.release_data['jobs']['release']

    def _find_run_script(self):
        for step in self.release_job['steps']:
            if step.get('id') == 'find-run':
                return step.get('with', {}).get('script', '')
        self.fail('no find-run step found')

    def _validation_push_branches(self):
        on_block = self.validations_data.get('on', self.validations_data.get(True, {}))
        return set(on_block.get('push', {}).get('branches', []))

    def test_find_run_searches_all_validation_eligible_branches(self):
        """The find-run step must search every branch that addon-validations pushes to."""
        eligible = self._validation_push_branches()
        script = self._find_run_script()
        for branch in eligible:
            self.assertIn(
                f"'{branch}'",
                script,
                f"find-run script must reference branch '{branch}' from validation triggers",
            )


if __name__ == '__main__':
    unittest.main()
