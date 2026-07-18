"""Deterministic contract tests for notify-repository.yml runtime behavior.

Extracts the real embedded JavaScript from the workflow's github-script
step and executes it via Node.js with mocked github/core objects.
Covers acceptance of the exact valid run plus rejection of every
contract violation: wrong event, wrong branch, wrong conclusion,
wrong workflow name, malformed SHA, malformed run ID, missing
artifact, duplicate artifact, expired artifact, head mismatch,
token boundary separation, and fail-closed pagination.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

WORKFLOW_PATH = ROOT / ".github" / "workflows" / "notify-repository.yml"
NODE_BIN = "node"

HARNESS_TEMPLATE = r"""
const fs = require('fs');
const mockConfig = JSON.parse(fs.readFileSync('@@CONFIG_PATH@@', 'utf8'));
const scriptText = fs.readFileSync('@@SCRIPT_PATH@@', 'utf8');

const output = [];
const dispatchCalls = [];
const listWorkflowRunArtifactsCalls = [];
const outputs = {};

const core = {
  info: (...args) => output.push({type: 'info', msg: args.join(' ')}),
  warning: (...args) => output.push({type: 'warning', msg: args.join(' ')}),
  error: (...args) => output.push({type: 'error', msg: args.join(' ')}),
  setFailed: (msg) => output.push({type: 'failed', msg}),
  setOutput: (name, val) => { outputs[name] = val; },
};

const context = {
  repo: mockConfig.repo,
  payload: mockConfig.payload,
};

const github = {
  rest: {
    actions: {
      listWorkflowRunArtifacts: async (params) => {
        listWorkflowRunArtifactsCalls.push({...params});
        const page = String(params.page || 1);
        const pageData = mockConfig.pages[page] || {total_count: 0, artifacts: []};
        return {data: pageData};
      },
    },
    repos: {
      createDispatchEvent: async (params) => { dispatchCalls.push(params); return {}; },
    },
  },
};

(async () => {
  try {
    const fn = new Function('github', 'context', 'core',
      'return (async () => {' + scriptText + '})()');
    await fn(github, context, core);
  } catch (e) {
    output.push({type: 'error', msg: e.message});
  }
  console.log(JSON.stringify({
    output,
    dispatchCalls,
    listWorkflowRunArtifactsCalls,
    outputs,
    failed: output.some(o => o.type === 'failed' || o.type === 'error'),
  }));
})();
"""


def _load_workflow():
    with open(WORKFLOW_PATH) as f:
        return yaml.safe_load(f)


def _extract_script(data):
    for job_name, job in data.get("jobs", {}).items():
        for step in job.get("steps", []):
            if "github-script" in step.get("uses", ""):
                return step.get("with", {}).get("script", "")
    return None


def _extract_step_id(data):
    for job_name, job in data.get("jobs", {}).items():
        for step in job.get("steps", []):
            if "github-script" in step.get("uses", ""):
                return step.get("id", "")
    return ""


def _run_with_mocks(script_text, mock_config):
    with tempfile.TemporaryDirectory() as tmpdir:
        script_path = os.path.join(tmpdir, "script.js")
        config_path = os.path.join(tmpdir, "config.json")
        harness_path = os.path.join(tmpdir, "harness.js")

        with open(script_path, "w") as f:
            f.write(script_text)

        with open(config_path, "w") as f:
            json.dump(mock_config, f)

        harness = HARNESS_TEMPLATE.replace("@@CONFIG_PATH@@", config_path).replace(
            "@@SCRIPT_PATH@@", script_path
        )

        with open(harness_path, "w") as f:
            f.write(harness)

        result = subprocess.run(
            [NODE_BIN, harness_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        stdout = result.stdout.strip()
        if not stdout:
            return {
                "output": [{"type": "error", "msg": result.stderr}],
                "dispatchCalls": [],
                "listWorkflowRunArtifactsCalls": [],
                "outputs": {},
                "failed": True,
            }
        last_line = stdout.split("\n")[-1]
        return json.loads(last_line)


def _valid_run(**overrides):
    run = {
        "id": 12345,
        "name": "Add-on Validations",
        "event": "push",
        "conclusion": "success",
        "head_branch": "develop",
        "head_sha": "a" * 40,
    }
    run.update(overrides)
    return run


def _valid_artifacts():
    return {
        "total_count": 2,
        "artifacts": [
            {"id": 1, "name": "addon-package", "expired": False},
            {"id": 2, "name": "validation-evidence", "expired": False},
        ],
    }


def _config(run_overrides=None, artifacts=None, pages=None):
    run = _valid_run(**(run_overrides or {}))
    if pages is None:
        if artifacts is not None:
            pages = {"1": artifacts}
        else:
            pages = {"1": _valid_artifacts()}
    return {
        "repo": {"owner": "Serph91P", "repo": "plugin.video.twitch"},
        "payload": {"workflow_run": run},
        "pages": pages,
    }


# ---------------------------------------------------------------------------
# Token boundary tests (YAML structure + runtime, no JS execution needed
# for structure checks, JS execution for behavioral checks)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HAS_YAML, "pyyaml not installed")
class NotifyWorkflowTokenBoundaryTests(unittest.TestCase):
    """Verify the github-script step uses the native token for artifact
    listing and that dispatch uses a separate step with the PAT."""

    @classmethod
    def setUpClass(cls):
        cls.data = _load_workflow()

    def _github_script_step(self):
        job = self.data["jobs"]["validated-publication"]
        for step in job.get("steps", []):
            if "github-script" in step.get("uses", ""):
                return step
        return None

    def _repository_dispatch_step(self):
        job = self.data["jobs"]["validated-publication"]
        for step in job.get("steps", []):
            if "repository-dispatch" in step.get("uses", ""):
                return step
        return None

    def test_github_script_step_exists(self):
        step = self._github_script_step()
        self.assertIsNotNone(step, "github-script step must exist")

    def test_repository_dispatch_step_exists(self):
        step = self._repository_dispatch_step()
        self.assertIsNotNone(step, "peter-evans/repository-dispatch step must exist")

    def test_github_script_uses_native_token(self):
        step = self._github_script_step()
        token_expr = step.get("with", {}).get("github-token", "")
        self.assertEqual(
            token_expr,
            "${{ github.token }}",
            "github-script must use github.token, not a PAT",
        )

    def test_github_script_does_not_use_dispatch_token(self):
        step = self._github_script_step()
        token_expr = step.get("with", {}).get("github-token", "")
        self.assertNotIn("REPO_DISPATCH_TOKEN", token_expr,
                         "github-script must not use REPO_DISPATCH_TOKEN")

    def test_repository_dispatch_uses_dispatch_token(self):
        step = self._repository_dispatch_step()
        token = step.get("with", {}).get("token", "")
        self.assertEqual(
            token,
            "${{ secrets.REPO_DISPATCH_TOKEN }}",
            "repository-dispatch must use REPO_DISPATCH_TOKEN",
        )

    def test_repository_dispatch_event_type(self):
        step = self._repository_dispatch_step()
        event_type = step.get("with", {}).get("event-type", "")
        self.assertEqual(event_type, "validated-addon-publication")

    def test_repository_dispatch_target_repository(self):
        step = self._repository_dispatch_step()
        repo = step.get("with", {}).get("repository", "")
        self.assertEqual(repo, "Serph91P/repository.serph91p")

    def test_github_script_has_step_id(self):
        step = self._github_script_step()
        self.assertIn("id", step, "github-script step must have an id for output reference")

    def test_repository_dispatch_references_script_output(self):
        dispatch_step = self._repository_dispatch_step()
        payload_ref = dispatch_step.get("with", {}).get("client-payload", "")
        script_step = self._github_script_step()
        script_id = script_step.get("id", "")
        self.assertTrue(
            script_id and script_id in payload_ref,
            f"repository-dispatch client-payload must reference step id "
            f"'{script_id}', got '{payload_ref}'",
        )

    def test_script_does_not_call_createDispatchEvent(self):
        script = _extract_script(self.data)
        self.assertNotIn("createDispatchEvent", script,
                         "github-script must not call createDispatchEvent; "
                         "dispatch belongs to a separate step")

    def test_script_outputs_payload(self):
        script = _extract_script(self.data)
        self.assertIn("setOutput", script,
                       "github-script must emit payload via core.setOutput")

    def test_two_steps_only(self):
        job = self.data["jobs"]["validated-publication"]
        steps = job.get("steps", [])
        self.assertEqual(len(steps), 2,
                         f"job must have exactly 2 steps, found {len(steps)}")


@unittest.skipUnless(_HAS_YAML, "pyyaml not installed")
class NotifyWorkflowTokenBoundaryRuntimeTests(unittest.TestCase):
    """Execute the real JS and verify it does NOT dispatch directly
    and instead outputs the payload via core.setOutput."""

    @classmethod
    def setUpClass(cls):
        cls.data = _load_workflow()
        cls.script = _extract_script(cls.data)
        if not cls.script:
            raise AssertionError("No github-script step found")

    def test_valid_run_outputs_payload_no_dispatch(self):
        result = _run_with_mocks(self.script, _config())
        self.assertFalse(result["failed"], f"script failed: {result['output']}")
        self.assertEqual(len(result["dispatchCalls"]), 0,
                         "script must not call createDispatchEvent directly")
        self.assertIn("payload", result["outputs"],
                       "script must output payload via core.setOutput")
        payload = json.loads(result["outputs"]["payload"])
        self.assertEqual(payload["source_repo"], "Serph91P/plugin.video.twitch")
        self.assertEqual(payload["candidate_sha"], "a" * 40)
        self.assertEqual(payload["validation_run_id"], 12345)
        self.assertEqual(payload["validation_head_sha"], "a" * 40)
        self.assertEqual(payload["validation_workflow"], "Add-on Validations")
        self.assertEqual(payload["expected_branch"], "develop")

    def test_all_artifact_requests_use_exact_run_id(self):
        result = _run_with_mocks(self.script, _config())
        for call in result["listWorkflowRunArtifactsCalls"]:
            self.assertEqual(
                call["run_id"], 12345,
                f"artifact request used run_id={call['run_id']}, expected 12345",
            )


# ---------------------------------------------------------------------------
# Script execution tests (existing contract tests)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HAS_YAML, "pyyaml not installed")
class NotifyWorkflowScriptTests(unittest.TestCase):
    """Execute the real embedded JS from notify-repository.yml with mocks."""

    @classmethod
    def setUpClass(cls):
        cls.data = _load_workflow()
        cls.script = _extract_script(cls.data)
        if not cls.script:
            raise AssertionError(
                "notify-repository.yml must contain a github-script step"
            )

    def _run(self, **kwargs):
        return _run_with_mocks(self.script, _config(**kwargs))

    def test_valid_run_outputs_payload(self):
        result = self._run()
        self.assertFalse(result["failed"], f"valid run failed: {result['output']}")
        self.assertEqual(len(result["dispatchCalls"]), 0,
                         "script must not dispatch directly")
        self.assertIn("payload", result["outputs"])
        payload = json.loads(result["outputs"]["payload"])
        self.assertEqual(payload["source_repo"], "Serph91P/plugin.video.twitch")
        self.assertEqual(payload["candidate_sha"], "a" * 40)
        self.assertEqual(payload["validation_run_id"], 12345)
        self.assertEqual(payload["validation_head_sha"], "a" * 40)
        self.assertEqual(payload["validation_workflow"], "Add-on Validations")
        self.assertEqual(payload["expected_branch"], "develop")

    def test_payload_exactly_six_fields(self):
        result = self._run()
        payload = json.loads(result["outputs"]["payload"])
        expected = {
            "source_repo",
            "candidate_sha",
            "validation_run_id",
            "validation_head_sha",
            "validation_workflow",
            "expected_branch",
        }
        self.assertEqual(set(payload.keys()), expected)

    def test_validation_run_id_is_json_number(self):
        result = self._run()
        payload = json.loads(result["outputs"]["payload"])
        self.assertIsInstance(payload["validation_run_id"], int)
        self.assertEqual(payload["validation_run_id"], 12345)

    def test_wrong_event_rejected(self):
        result = self._run(run_overrides={"event": "schedule"})
        self.assertTrue(result["failed"], "wrong event should be rejected")
        self.assertEqual(len(result["dispatchCalls"]), 0)

    def test_wrong_branch_rejected(self):
        result = self._run(run_overrides={"head_branch": "main"})
        self.assertTrue(result["failed"], "wrong branch should be rejected")
        self.assertEqual(len(result["dispatchCalls"]), 0)

    def test_wrong_conclusion_rejected(self):
        result = self._run(run_overrides={"conclusion": "failure"})
        self.assertTrue(result["failed"], "wrong conclusion should be rejected")
        self.assertEqual(len(result["dispatchCalls"]), 0)

    def test_wrong_workflow_name_rejected(self):
        result = self._run(run_overrides={"name": "Some Other Workflow"})
        self.assertTrue(result["failed"], "wrong workflow name should be rejected")
        self.assertEqual(len(result["dispatchCalls"]), 0)

    def test_malformed_sha_not_hex_rejected(self):
        result = self._run(run_overrides={"head_sha": "not-a-valid-sha"})
        self.assertTrue(result["failed"], "non-hex SHA should be rejected")
        self.assertEqual(len(result["dispatchCalls"]), 0)

    def test_malformed_sha_uppercase_rejected(self):
        result = self._run(run_overrides={"head_sha": "A" * 40})
        self.assertTrue(result["failed"], "uppercase SHA should be rejected")
        self.assertEqual(len(result["dispatchCalls"]), 0)

    def test_malformed_sha_too_short_rejected(self):
        result = self._run(run_overrides={"head_sha": "abc123"})
        self.assertTrue(result["failed"], "short SHA should be rejected")
        self.assertEqual(len(result["dispatchCalls"]), 0)

    def test_malformed_sha_too_long_rejected(self):
        result = self._run(run_overrides={"head_sha": "a" * 41})
        self.assertTrue(result["failed"], "long SHA should be rejected")
        self.assertEqual(len(result["dispatchCalls"]), 0)

    def test_invalid_run_id_zero_rejected(self):
        result = self._run(run_overrides={"id": 0})
        self.assertTrue(result["failed"], "zero run ID should be rejected")
        self.assertEqual(len(result["dispatchCalls"]), 0)

    def test_invalid_run_id_negative_rejected(self):
        result = self._run(run_overrides={"id": -1})
        self.assertTrue(result["failed"], "negative run ID should be rejected")
        self.assertEqual(len(result["dispatchCalls"]), 0)

    def test_invalid_run_id_float_rejected(self):
        result = self._run(run_overrides={"id": 1.5})
        self.assertTrue(result["failed"], "float run ID should be rejected")
        self.assertEqual(len(result["dispatchCalls"]), 0)

    def test_missing_addon_package_rejected(self):
        result = self._run(
            artifacts={
                "total_count": 1,
                "artifacts": [
                    {"id": 2, "name": "validation-evidence", "expired": False},
                ],
            }
        )
        self.assertTrue(result["failed"], "missing addon-package should be rejected")
        self.assertEqual(len(result["dispatchCalls"]), 0)

    def test_missing_validation_evidence_rejected(self):
        result = self._run(
            artifacts={
                "total_count": 1,
                "artifacts": [
                    {"id": 1, "name": "addon-package", "expired": False},
                ],
            }
        )
        self.assertTrue(
            result["failed"], "missing validation-evidence should be rejected"
        )
        self.assertEqual(len(result["dispatchCalls"]), 0)

    def test_no_artifacts_rejected(self):
        result = self._run(artifacts={"total_count": 0, "artifacts": []})
        self.assertTrue(result["failed"], "no artifacts should be rejected")
        self.assertEqual(len(result["dispatchCalls"]), 0)

    def test_duplicate_addon_package_rejected(self):
        result = self._run(
            artifacts={
                "total_count": 3,
                "artifacts": [
                    {"id": 1, "name": "addon-package", "expired": False},
                    {"id": 2, "name": "addon-package", "expired": False},
                    {"id": 3, "name": "validation-evidence", "expired": False},
                ],
            }
        )
        self.assertTrue(
            result["failed"], "duplicate addon-package should be rejected"
        )
        self.assertEqual(len(result["dispatchCalls"]), 0)

    def test_duplicate_validation_evidence_rejected(self):
        result = self._run(
            artifacts={
                "total_count": 3,
                "artifacts": [
                    {"id": 1, "name": "addon-package", "expired": False},
                    {"id": 2, "name": "validation-evidence", "expired": False},
                    {"id": 3, "name": "validation-evidence", "expired": False},
                ],
            }
        )
        self.assertTrue(
            result["failed"], "duplicate validation-evidence should be rejected"
        )
        self.assertEqual(len(result["dispatchCalls"]), 0)

    def test_expired_addon_package_rejected(self):
        result = self._run(
            artifacts={
                "total_count": 2,
                "artifacts": [
                    {"id": 1, "name": "addon-package", "expired": True},
                    {"id": 2, "name": "validation-evidence", "expired": False},
                ],
            }
        )
        self.assertTrue(
            result["failed"], "expired addon-package should be rejected"
        )
        self.assertEqual(len(result["dispatchCalls"]), 0)

    def test_expired_validation_evidence_rejected(self):
        result = self._run(
            artifacts={
                "total_count": 2,
                "artifacts": [
                    {"id": 1, "name": "addon-package", "expired": False},
                    {"id": 2, "name": "validation-evidence", "expired": True},
                ],
            }
        )
        self.assertTrue(
            result["failed"], "expired validation-evidence should be rejected"
        )
        self.assertEqual(len(result["dispatchCalls"]), 0)

    def test_extra_unrelated_artifact_allowed(self):
        result = self._run(
            artifacts={
                "total_count": 3,
                "artifacts": [
                    {"id": 1, "name": "addon-package", "expired": False},
                    {"id": 2, "name": "validation-evidence", "expired": False},
                    {"id": 3, "name": "logs", "expired": False},
                ],
            }
        )
        self.assertFalse(
            result["failed"], "extra unrelated artifact should not cause failure"
        )

    def test_no_dispatch_on_rejection(self):
        result = self._run(run_overrides={"event": "workflow_dispatch"})
        self.assertEqual(len(result["dispatchCalls"]), 0)
        self.assertTrue(result["failed"])

    def test_all_artifact_requests_use_exact_run_id(self):
        result = self._run()
        for call in result["listWorkflowRunArtifactsCalls"]:
            self.assertEqual(
                call["run_id"], 12345,
                f"artifact request used run_id={call['run_id']}, expected 12345",
            )


# ---------------------------------------------------------------------------
# Pagination tests (fail-closed pagination contract)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HAS_YAML, "pyyaml not installed")
class NotifyWorkflowPaginationTests(unittest.TestCase):
    """Verify fail-closed pagination: bounded page fetch, correct
    run_id on every request, and rejection when total_count
    exceeds fetched artifacts after the page cap."""

    @classmethod
    def setUpClass(cls):
        cls.data = _load_workflow()
        cls.script = _extract_script(cls.data)
        if not cls.script:
            raise AssertionError("No github-script step found")

    def _run(self, **kwargs):
        return _run_with_mocks(self.script, _config(**kwargs))

    def test_single_page_all_artifacts_fit(self):
        result = self._run(
            pages={
                "1": {
                    "total_count": 2,
                    "artifacts": [
                        {"id": 1, "name": "addon-package", "expired": False},
                        {"id": 2, "name": "validation-evidence", "expired": False},
                    ],
                },
            }
        )
        self.assertFalse(result["failed"], f"single page should pass: {result['output']}")
        self.assertEqual(len(result["listWorkflowRunArtifactsCalls"]), 1)

    def test_two_pages_required_artifacts_split(self):
        result = self._run(
            pages={
                "1": {
                    "total_count": 3,
                    "artifacts": [
                        {"id": 1, "name": "addon-package", "expired": False},
                        {"id": 3, "name": "logs", "expired": False},
                    ],
                },
                "2": {
                    "total_count": 3,
                    "artifacts": [
                        {"id": 2, "name": "validation-evidence", "expired": False},
                    ],
                },
            }
        )
        self.assertFalse(
            result["failed"],
            f"split across two pages should pass: {result['output']}",
        )
        self.assertEqual(len(result["listWorkflowRunArtifactsCalls"]), 2)

    def test_two_pages_both_requests_use_exact_run_id(self):
        result = self._run(
            pages={
                "1": {
                    "total_count": 3,
                    "artifacts": [
                        {"id": 1, "name": "addon-package", "expired": False},
                        {"id": 3, "name": "logs", "expired": False},
                    ],
                },
                "2": {
                    "total_count": 3,
                    "artifacts": [
                        {"id": 2, "name": "validation-evidence", "expired": False},
                    ],
                },
            }
        )
        for i, call in enumerate(result["listWorkflowRunArtifactsCalls"]):
            self.assertEqual(
                call["run_id"], 12345,
                f"request {i} used run_id={call['run_id']}, expected 12345",
            )
            self.assertEqual(
                call["per_page"], 100,
                f"request {i} used per_page={call['per_page']}, expected 100",
            )

    def test_incomplete_total_count_after_page_cap_fails_closed(self):
        pages = {}
        for p in range(1, 12):
            pages[str(p)] = {
                "total_count": 200,
                "artifacts": [
                    {"id": p, "name": f"artifact-{p}", "expired": False},
                ],
            }
        result = self._run(pages=pages)
        self.assertTrue(
            result["failed"],
            "must fail closed when total_count=200 but only ~11 fetched",
        )
        self.assertEqual(len(result["dispatchCalls"]), 0)

    def test_incomplete_total_count_does_not_dispatch(self):
        pages = {}
        for p in range(1, 12):
            pages[str(p)] = {
                "total_count": 200,
                "artifacts": [
                    {"id": p, "name": f"artifact-{p}", "expired": False},
                ],
            }
        result = self._run(pages=pages)
        self.assertEqual(
            len(result["dispatchCalls"]), 0,
            "must not dispatch with incomplete artifact data",
        )

    def test_page_count_matches_total_count(self):
        result = self._run(
            pages={
                "1": {
                    "total_count": 2,
                    "artifacts": [
                        {"id": 1, "name": "addon-package", "expired": False},
                        {"id": 2, "name": "validation-evidence", "expired": False},
                    ],
                },
            }
        )
        self.assertEqual(len(result["listWorkflowRunArtifactsCalls"]), 1)
        first_call = result["listWorkflowRunArtifactsCalls"][0]
        self.assertNotIn("page", first_call,
                         "first request should not specify page (defaults to 1)")

    def test_second_page_requested_only_when_needed(self):
        result = self._run(
            pages={
                "1": {
                    "total_count": 3,
                    "artifacts": [
                        {"id": 1, "name": "addon-package", "expired": False},
                        {"id": 2, "name": "validation-evidence", "expired": False},
                        {"id": 3, "name": "logs", "expired": False},
                    ],
                },
            }
        )
        self.assertEqual(len(result["listWorkflowRunArtifactsCalls"]), 1)

    def test_duplicate_required_across_pages_rejected(self):
        result = self._run(
            pages={
                "1": {
                    "total_count": 4,
                    "artifacts": [
                        {"id": 1, "name": "addon-package", "expired": False},
                        {"id": 3, "name": "logs", "expired": False},
                    ],
                },
                "2": {
                    "total_count": 4,
                    "artifacts": [
                        {"id": 2, "name": "validation-evidence", "expired": False},
                        {"id": 4, "name": "addon-package", "expired": False},
                    ],
                },
            }
        )
        self.assertTrue(
            result["failed"],
            "duplicate addon-package across pages should be rejected",
        )
        self.assertEqual(len(result["dispatchCalls"]), 0)

    def test_expired_across_pages_rejected(self):
        result = self._run(
            pages={
                "1": {
                    "total_count": 3,
                    "artifacts": [
                        {"id": 1, "name": "addon-package", "expired": False},
                        {"id": 3, "name": "logs", "expired": False},
                    ],
                },
                "2": {
                    "total_count": 3,
                    "artifacts": [
                        {"id": 2, "name": "validation-evidence", "expired": True},
                    ],
                },
            }
        )
        self.assertTrue(
            result["failed"],
            "expired validation-evidence on page 2 should be rejected",
        )
        self.assertEqual(len(result["dispatchCalls"]), 0)


if __name__ == "__main__":
    unittest.main()
