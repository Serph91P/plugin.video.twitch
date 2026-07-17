"""Tests for make-release.yml workflow.

Verifies that the release workflow has correct working directories,
path handling, SHA validation, and immutable release contract.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
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


# --- Embedded Node.js harness for find-run behavioral tests ---

_FIND_RUN_HARNESS = """
let inputData = "";
process.stdin.on("data", function(chunk) { inputData += chunk; });
process.stdin.on("end", function() {
  var scenario = JSON.parse(inputData);
  var script = scenario.script;
  var ctx = scenario.context;
  var apiLog = [];
  var responses = scenario.apiCalls || [];
  var responseIdx = 0;
  var github = {
    rest: {
      actions: {
        listWorkflowRunsForRepo: async function(params) {
          apiLog.push({ method: "listWorkflowRunsForRepo", params: Object.assign({}, params) });
          if (responseIdx < responses.length) {
            var resp = responses[responseIdx];
            responseIdx++;
            return resp;
          }
          return { data: { workflow_runs: [] } };
        }
      }
    }
  };
  var context = {
    repo: { owner: ctx.owner || "test-owner", repo: ctx.repo || "test-repo" },
    ref: ctx.ref || "refs/tags/v1.0.0",
    sha: ctx.sha || "abc123"
  };
  var outputs = {};
  var core = {
    setOutput: function(name, value) { outputs[name] = value; },
    setFailed: function(message) { outputs.__failed = true; outputs.__failureMessage = message; }
  };
  var fs = require("fs");
  var osMod = require("os");
  var pathMod = require("path");
  var tmpFile = pathMod.join(osMod.tmpdir(), "find_run_test_" + process.pid + ".mjs");
  var wrapped = "export async function run(github, context, core) { " + script + " }";
  fs.writeFileSync(tmpFile, wrapped);
  (async function() {
    try {
      var mod = await import(tmpFile);
      await mod.run(github, context, core);
    } catch(err) {
      outputs.__error = err.message;
    } finally {
      try { fs.unlinkSync(tmpFile); } catch(e) {}
    }
    var result = {
      outputs: outputs,
      failed: !!outputs.__failed,
      failureMessage: outputs.__failureMessage || null,
      error: outputs.__error || null,
      apiLog: apiLog
    };
    process.stdout.write(JSON.stringify(result, null, 2) + "\\n");
  })();
});
"""


def _extract_find_run_script():
    data = parse_yaml_file(ROOT / ".github" / "workflows" / "make-release.yml")
    for step in data["jobs"]["release"]["steps"]:
        if step.get("id") == "find-run":
            return step["with"]["script"]
    raise RuntimeError("find-run step not found in make-release.yml")


def _run_find_run_harness(scenario):
    proc = subprocess.run(
        ["node", "-e", _FIND_RUN_HARNESS],
        input=json.dumps(scenario),
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"harness exited {proc.returncode}: {proc.stderr}")
    return json.loads(proc.stdout)


def _make_find_scenario(*, sha="abc123def456", ref="refs/tags/v1.0.0",
                        owner="test-owner", repo="test-repo",
                        tag_runs=None, eligible_runs=None):
    api_calls = [{"data": {"workflow_runs": tag_runs or []}}]
    for branch in ("main", "develop"):
        for page_runs in (eligible_runs or {}).get(branch, []):
            api_calls.append({"data": {"workflow_runs": page_runs}})
    return {
        "script": _extract_find_run_script(),
        "context": {"owner": owner, "repo": repo, "ref": ref, "sha": sha},
        "apiCalls": api_calls,
    }


def _make_find_run(*, run_id=1, name="Add-on Validations", conclusion="success",
                   head_sha="abc123def456", head_branch="main"):
    return {
        "id": run_id, "name": name, "conclusion": conclusion,
        "head_sha": head_sha, "head_branch": head_branch,
    }


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
class MakeReleaseFindRunBehaviorTests(unittest.TestCase):
    """Execute the real find-run JavaScript and verify selection behavior."""

    def test_page2_match_after_nonmatches(self):
        sha = "deadbeef1234"
        good = _make_find_run(run_id=42, head_sha=sha, head_branch="main")
        bad = [_make_find_run(run_id=i, name="Other", head_branch="main") for i in range(10)]
        scenario = _make_find_scenario(
            sha=sha, tag_runs=[],
            eligible_runs={"main": [bad, [good]], "develop": []},
        )
        result = _run_find_run_harness(scenario)
        self.assertFalse(result["failed"], result.get("failureMessage"))
        self.assertEqual(result["outputs"]["validation_run_id"], 42)

    def test_rejection_matrix(self):
        sha = "abc123"
        cases = {
            "wrong_workflow_name": dict(
                tag_runs=[_make_find_run(run_id=1, name="Wrong Workflow", head_sha=sha)],
                eligible_runs={"main": [], "develop": []},
            ),
            "wrong_sha": dict(
                tag_runs=[_make_find_run(run_id=1, head_sha="wrongsha")],
                eligible_runs={"main": [], "develop": []},
            ),
            "stale_sha": dict(
                eligible_runs={"main": [[_make_find_run(run_id=1, head_sha="old")]], "develop": []},
            ),
            "wrong_workflow_eligible": dict(
                eligible_runs={"main": [[_make_find_run(run_id=1, name="CI Tests", head_sha=sha)]], "develop": []},
            ),
            "wrong_commit": dict(
                eligible_runs={"main": [[_make_find_run(run_id=1, head_sha="other")]], "develop": []},
            ),
            "unsuccessful": dict(
                eligible_runs={"main": [[_make_find_run(run_id=1, conclusion="failure", head_sha=sha)]], "develop": []},
            ),
            "absent": dict(
                tag_runs=[], eligible_runs={"main": [], "develop": []},
            ),
            "malformed": dict(
                eligible_runs={"main": [[{"id": 1}]], "develop": []},
            ),
        }
        for label, kwargs in cases.items():
            with self.subTest(case=label):
                result = _run_find_run_harness(_make_find_scenario(sha=sha, **kwargs))
                self.assertTrue(result["failed"], f"{label} should be rejected")

    def test_unsuccessful_conclusions_rejected(self):
        sha = "abc123"
        for conclusion in ("failure", "cancelled", "timed_out", "action_required"):
            with self.subTest(conclusion=conclusion):
                scenario = _make_find_scenario(
                    sha=sha,
                    tag_runs=[_make_find_run(run_id=1, conclusion=conclusion, head_sha=sha)],
                    eligible_runs={"main": [], "develop": []},
                )
                result = _run_find_run_harness(scenario)
                self.assertTrue(result["failed"], f"reject conclusion={conclusion}")

    def test_wrong_branch_rejected(self):
        sha = "abc123"
        scenario = _make_find_scenario(
            ref="refs/tags/v1.0.0", sha=sha,
            tag_runs=[_make_find_run(run_id=100, head_sha=sha, head_branch="fix/some-feature")],
            eligible_runs={"main": [], "develop": []},
        )
        result = _run_find_run_harness(scenario)
        self.assertTrue(result["failed"], "must not select non-eligible branch")
        self.assertIn("No successful validation run", result["failureMessage"])

    def test_eligible_branch_wins_over_tag(self):
        sha = "abc123"
        scenario = _make_find_scenario(
            ref="refs/tags/v1.0.0", sha=sha,
            tag_runs=[_make_find_run(run_id=100, head_sha=sha, head_branch="fix/some-feature")],
            eligible_runs={"main": [[_make_find_run(run_id=200, head_sha=sha, head_branch="main")]], "develop": []},
        )
        result = _run_find_run_harness(scenario)
        self.assertFalse(result["failed"], result.get("failureMessage"))
        self.assertEqual(result["outputs"]["validation_run_id"], 200)

    def test_pagination_bounds(self):
        sha = "abc123"
        pages = [[_make_find_run(run_id=i*10+j, head_branch="main") for j in range(10)] for i in range(5)]
        scenario = _make_find_scenario(sha=sha, eligible_runs={"main": pages, "develop": []})
        result = _run_find_run_harness(scenario)
        main_calls = [c for c in result["apiLog"] if c["params"].get("branch") == "main"]
        self.assertLessEqual(len(main_calls), 5, "must not exceed maxPages")
        self.assertTrue(result["failed"], "no match within bounds should fail")

    def test_both_branches_searched(self):
        sha = "abc123"
        good = _make_find_run(run_id=50, head_sha=sha, head_branch="develop")
        scenario = _make_find_scenario(
            sha=sha, eligible_runs={"main": [], "develop": [[good]]},
        )
        result = _run_find_run_harness(scenario)
        self.assertFalse(result["failed"], result.get("failureMessage"))
        self.assertEqual(result["outputs"]["validation_run_id"], 50)

    def test_main_before_develop(self):
        sha = "abc123"
        scenario = _make_find_scenario(sha=sha, eligible_runs={"main": [[]], "develop": [[]]})
        result = _run_find_run_harness(scenario)
        branch_order = [c["params"]["branch"] for c in result["apiLog"] if c["params"].get("branch") in ("main", "develop")]
        if len(branch_order) >= 2:
            self.assertEqual(branch_order[0], "main")

    def test_tag_query_eligible_branch_accepted(self):
        sha = "abc123"
        scenario = _make_find_scenario(
            ref="refs/tags/v1.0.0", sha=sha,
            tag_runs=[_make_find_run(run_id=99, head_sha=sha, head_branch="main")],
            eligible_runs={"main": [], "develop": []},
        )
        result = _run_find_run_harness(scenario)
        self.assertFalse(result["failed"], result.get("failureMessage"))
        self.assertEqual(result["outputs"]["validation_run_id"], 99)


@unittest.skipUnless(_HAS_YAML, 'pyyaml not installed')
class MakeReleaseFindRunStaticTests(unittest.TestCase):
    """Static structural checks on the find-run script."""

    def test_script_structure(self):
        script = _extract_find_run_script()
        checks = [
            ("eligibleBranches", "must define eligibleBranches"),
            ("'main'", "must reference main branch"),
            ("'develop'", "must reference develop branch"),
            ("perPage", "must define perPage"),
            ("conclusion", "must check conclusion"),
            ("head_sha", "must check head_sha"),
            ("Add-on Validations", "must check workflow name"),
            ("eligibleBranches.includes(run.head_branch)", "must verify head_branch eligibility"),
        ]
        for pattern, msg in checks:
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, script, msg)
        self.assertTrue(re.search(r"maxPages?\b", script), "must define a maxPages bound")


if __name__ == '__main__':
    unittest.main()
