"""Deterministic contract tests for notify-repository.yml runtime behavior.

Extracts the real embedded JavaScript from the workflow's github-script
step and executes it via Node.js with mocked github/core objects.
Covers acceptance of the exact valid run plus rejection of every
contract violation: wrong event, wrong branch, wrong conclusion,
wrong workflow name, malformed SHA, malformed run ID, missing
artifact, duplicate artifact, expired artifact, and head mismatch.
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

const core = {
  info: (...args) => output.push({type: 'info', msg: args.join(' ')}),
  warning: (...args) => output.push({type: 'warning', msg: args.join(' ')}),
  error: (...args) => output.push({type: 'error', msg: args.join(' ')}),
  setFailed: (msg) => output.push({type: 'failed', msg}),
  setOutput: (name, val) => {},
};

const github = {
  context: {
    repo: mockConfig.repo,
    payload: mockConfig.payload,
  },
  rest: {
    actions: {
      listWorkflowRunArtifacts: async (params) => ({data: mockConfig.artifactsResponse}),
    },
    repos: {
      createDispatchEvent: async (params) => { dispatchCalls.push(params); return {}; },
    },
  },
};

(async () => {
  try {
    const fn = new Function('github', 'core',
      'return (async () => {' + scriptText + '})()');
    await fn(github, core);
  } catch (e) {
    output.push({type: 'error', msg: e.message});
  }
  console.log(JSON.stringify({
    output,
    dispatchCalls,
    failed: output.some(o => o.type === 'failed'),
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


def _config(run_overrides=None, artifacts=None):
    run = _valid_run(**(run_overrides or {}))
    return {
        "repo": {"owner": "Serph91P", "repo": "plugin.video.twitch"},
        "payload": {"workflow_run": run},
        "artifactsResponse": artifacts if artifacts is not None else _valid_artifacts(),
    }


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

    def test_valid_run_dispatches(self):
        result = self._run()
        self.assertFalse(result["failed"], f"valid run failed: {result['output']}")
        self.assertEqual(len(result["dispatchCalls"]), 1)
        call = result["dispatchCalls"][0]
        self.assertEqual(call["event_type"], "validated-addon-publication")
        self.assertEqual(call["owner"], "Serph91P")
        self.assertEqual(call["repo"], "repository.serph91p")
        payload = call["client_payload"]
        self.assertEqual(payload["source_repo"], "Serph91P/plugin.video.twitch")
        self.assertEqual(payload["candidate_sha"], "a" * 40)
        self.assertEqual(payload["validation_run_id"], 12345)
        self.assertEqual(payload["validation_head_sha"], "a" * 40)
        self.assertEqual(payload["validation_workflow"], "Add-on Validations")
        self.assertEqual(payload["expected_branch"], "develop")

    def test_payload_exactly_six_fields(self):
        result = self._run()
        payload = result["dispatchCalls"][0]["client_payload"]
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
        payload = result["dispatchCalls"][0]["client_payload"]
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
        self.assertEqual(len(result["dispatchCalls"]), 1)

    def test_no_dispatch_on_rejection(self):
        result = self._run(run_overrides={"event": "workflow_dispatch"})
        self.assertEqual(len(result["dispatchCalls"]), 0)
        self.assertTrue(result["failed"])


if __name__ == "__main__":
    unittest.main()
