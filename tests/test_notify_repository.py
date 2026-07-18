"""Deterministic contract tests for notify-repository.yml runtime behavior.

Extracts the real embedded JavaScript from the workflow's github-script
step and executes it via Node.js with mocked github/core objects.
Covers acceptance of the exact valid run and evidence plus rejection of
contract violations including workflow path, publication identity,
artifact cardinality, expiry, token separation, and bounded pagination.
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
VALIDATION_WORKFLOW_PATH = (
    ROOT / ".github" / "workflows" / "addon-validations.yml"
)
NODE_BIN = "node"

HARNESS_TEMPLATE = r"""
const fs = require('fs');
const mockConfig = JSON.parse(fs.readFileSync('@@CONFIG_PATH@@', 'utf8'));
const scriptTexts = JSON.parse(fs.readFileSync('@@SCRIPT_PATH@@', 'utf8'));
process.env.VALIDATION_EVIDENCE_PATH = mockConfig.evidencePath;

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
    for (const scriptText of scriptTexts) {
      const fn = new Function('github', 'context', 'core', 'require',
        'return (async () => {' + scriptText + '})()');
      await fn(github, context, core, require);
      if (output.some(o => o.type === 'failed' || o.type === 'error')) break;
    }
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


def _load_validation_workflow():
    with open(VALIDATION_WORKFLOW_PATH) as f:
        return yaml.safe_load(f)


def _extract_scripts(data):
    scripts = []
    for job in data.get("jobs", {}).values():
        for step in job.get("steps", []):
            if "github-script" in step.get("uses", ""):
                scripts.append(step.get("with", {}).get("script", ""))
    return scripts


def _extract_script(data, step_id):
    for job in data.get("jobs", {}).values():
        for step in job.get("steps", []):
            if step.get("id") == step_id:
                return step.get("with", {}).get("script", "")
    return None


def _run_with_mocks(script_texts, mock_config):
    with tempfile.TemporaryDirectory() as tmpdir:
        script_path = os.path.join(tmpdir, "script.js")
        config_path = os.path.join(tmpdir, "config.json")
        harness_path = os.path.join(tmpdir, "harness.js")
        evidence_dir = Path(tmpdir) / "validation-artifacts"
        evidence_dir.mkdir()
        evidence_path = evidence_dir / "validation-evidence.json"

        with open(script_path, "w") as f:
            json.dump(script_texts, f)

        evidence = mock_config.get("evidence")
        if evidence is not None:
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        mock_config = dict(mock_config)
        mock_config["evidencePath"] = str(evidence_path)

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
        "path": ".github/workflows/addon-validations.yml",
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


def _valid_evidence(**overrides):
    evidence = {
        "candidate_sha": "a" * 40,
        "validation_run_id": "12345",
        "validation_head_sha": "a" * 40,
        "addon_id": "plugin.video.twitch",
        "addon_version": "3.1.11",
        "asset_name": "plugin.video.twitch-3.1.11.zip",
        "artifact_sha256": "b" * 64,
        "tag": "",
        "publication_id": "plugin.video.twitch@3.1.11",
    }
    evidence.update(overrides)
    return evidence


def _config(run_overrides=None, artifacts=None, pages=None, evidence=None):
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
        "evidence": _valid_evidence() if evidence is None else evidence,
    }


# ---------------------------------------------------------------------------
# Token boundary tests (YAML structure + runtime, no JS execution needed
# for structure checks, JS execution for behavioral checks)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HAS_YAML, "pyyaml not installed")
class NotifyWorkflowTokenBoundaryTests(unittest.TestCase):
    """Verify validation uses the native token and dispatch uses the PAT."""

    @classmethod
    def setUpClass(cls):
        cls.data = _load_workflow()

    def _github_script_steps(self):
        job = self.data["jobs"]["validated-publication"]
        return [
            step for step in job.get("steps", [])
            if "github-script" in step.get("uses", "")
        ]

    def _download_step(self):
        job = self.data["jobs"]["validated-publication"]
        for step in job.get("steps", []):
            if "download-artifact" in step.get("uses", ""):
                return step
        return None

    def _repository_dispatch_step(self):
        job = self.data["jobs"]["validated-publication"]
        for step in job.get("steps", []):
            if "repository-dispatch" in step.get("uses", ""):
                return step
        return None

    def test_two_github_script_steps_exist(self):
        self.assertEqual(len(self._github_script_steps()), 2)

    def test_repository_dispatch_step_exists(self):
        step = self._repository_dispatch_step()
        self.assertIsNotNone(step, "peter-evans/repository-dispatch step must exist")

    def test_github_scripts_use_native_token(self):
        for step in self._github_script_steps():
            token_expr = step.get("with", {}).get("github-token", "")
            self.assertEqual(
                token_expr,
                "${{ github.token }}",
                "github-script must use github.token, not a PAT",
            )

    def test_native_validation_steps_do_not_use_dispatch_token(self):
        native_steps = self._github_script_steps() + [self._download_step()]
        for step in native_steps:
            self.assertIsNotNone(step)
            self.assertNotIn("REPO_DISPATCH_TOKEN", json.dumps(step))

    def test_download_uses_exact_artifact_id_and_run(self):
        step = self._download_step()
        self.assertIsNotNone(step)
        inputs = step.get("with", {})
        self.assertEqual(inputs.get("github-token"), "${{ github.token }}")
        self.assertEqual(
            inputs.get("artifact-ids"),
            "${{ steps.validate-run.outputs.validation_evidence_artifact_id }}",
        )
        self.assertEqual(inputs.get("run-id"), "${{ github.event.workflow_run.id }}")
        self.assertEqual(inputs.get("repository"), "${{ github.repository }}")
        self.assertTrue(inputs.get("merge-multiple"))

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
        for step in self._github_script_steps():
            self.assertIn(
                "id", step, "github-script step must have an id for output reference"
            )

    def test_repository_dispatch_references_script_output(self):
        dispatch_step = self._repository_dispatch_step()
        payload_ref = dispatch_step.get("with", {}).get("client-payload", "")
        self.assertEqual(payload_ref, "${{ steps.validate-evidence.outputs.payload }}")

    def test_script_does_not_call_createDispatchEvent(self):
        scripts = "\n".join(_extract_scripts(self.data))
        self.assertNotIn("createDispatchEvent", scripts,
                         "github-script must not call createDispatchEvent; "
                         "dispatch belongs to a separate step")

    def test_evidence_script_outputs_payload(self):
        script = _extract_script(self.data, "validate-evidence")
        self.assertIn("setOutput", script,
                       "evidence script must emit payload via core.setOutput")

    def test_four_steps_only(self):
        job = self.data["jobs"]["validated-publication"]
        steps = job.get("steps", [])
        self.assertEqual(len(steps), 4,
                         f"job must have exactly 4 steps, found {len(steps)}")


@unittest.skipUnless(_HAS_YAML, "pyyaml not installed")
class NotifyWorkflowTokenBoundaryRuntimeTests(unittest.TestCase):
    """Execute the real JS and verify it does NOT dispatch directly
    and instead outputs the payload via core.setOutput."""

    @classmethod
    def setUpClass(cls):
        cls.data = _load_workflow()
        cls.scripts = _extract_scripts(cls.data)
        if len(cls.scripts) != 2:
            raise AssertionError("Expected two github-script steps")

    def test_valid_run_outputs_payload_no_dispatch(self):
        result = _run_with_mocks(self.scripts, _config())
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
        self.assertEqual(
            payload["validation_workflow_path"],
            ".github/workflows/addon-validations.yml",
        )
        self.assertEqual(payload["expected_branch"], "develop")
        self.assertEqual(payload["publication_id"], "plugin.video.twitch@3.1.11")

    def test_all_artifact_requests_use_exact_run_id(self):
        result = _run_with_mocks(self.scripts, _config())
        for call in result["listWorkflowRunArtifactsCalls"]:
            self.assertEqual(
                call["run_id"], 12345,
                f"artifact request used run_id={call['run_id']}, expected 12345",
            )


@unittest.skipUnless(_HAS_YAML, "pyyaml not installed")
class AddonValidationPublicationContractTests(unittest.TestCase):
    """Verify validation publishes only the two retained contract artifacts."""

    @classmethod
    def setUpClass(cls):
        cls.data = _load_validation_workflow()

    def _upload_steps(self):
        return [
            step
            for job in self.data.get("jobs", {}).values()
            for step in job.get("steps", [])
            if step.get("uses", "").startswith("actions/upload-artifact@")
        ]

    def _kodi_steps(self):
        return self.data["jobs"]["kodi-check"]["steps"]

    def test_exactly_two_publication_artifacts_are_uploaded(self):
        uploads = self._upload_steps()
        self.assertEqual(len(uploads), 2)
        self.assertEqual(
            {step.get("with", {}).get("name") for step in uploads},
            {"addon-package", "validation-evidence"},
        )

    def test_publication_artifacts_have_30_day_retention(self):
        for step in self._upload_steps():
            self.assertEqual(step.get("with", {}).get("retention-days"), 30)

    def test_checker_lock_is_sparse_checked_out_at_candidate_sha(self):
        checkout = next(
            step
            for step in self._kodi_steps()
            if step.get("uses", "").startswith("actions/checkout@")
        )
        inputs = checkout.get("with", {})
        self.assertEqual(inputs.get("ref"), "${{ github.sha }}")
        self.assertFalse(inputs.get("persist-credentials"))
        self.assertEqual(
            inputs.get("sparse-checkout"),
            ".github/workflow-requirements/addon-check.txt",
        )
        self.assertFalse(inputs.get("sparse-checkout-cone-mode"))
        self.assertNotIn("path", inputs)

    def test_checker_lock_does_not_use_an_artifact_or_expose_a_token(self):
        serialized = json.dumps(self._kodi_steps())
        self.assertNotIn("addon-check-dependencies", serialized)
        self.assertNotIn("github.token", serialized)
        self.assertNotIn("secrets.", serialized)
        install = next(
            step for step in self._kodi_steps()
            if step.get("name") == "Install dependencies"
        )
        self.assertIn(
            "-r .github/workflow-requirements/addon-check.txt",
            install.get("run", ""),
        )

    def test_checker_lock_checkout_precedes_package_download(self):
        steps = self._kodi_steps()
        checkout_index = next(
            index for index, step in enumerate(steps)
            if step.get("uses", "").startswith("actions/checkout@")
        )
        download_index = next(
            index for index, step in enumerate(steps)
            if step.get("with", {}).get("name") == "addon-package"
        )
        self.assertLess(checkout_index, download_index)


# ---------------------------------------------------------------------------
# Script execution tests (existing contract tests)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HAS_YAML, "pyyaml not installed")
class NotifyWorkflowScriptTests(unittest.TestCase):
    """Execute the real embedded JS from notify-repository.yml with mocks."""

    @classmethod
    def setUpClass(cls):
        cls.data = _load_workflow()
        cls.scripts = _extract_scripts(cls.data)
        if len(cls.scripts) != 2:
            raise AssertionError(
                "notify-repository.yml must contain two github-script steps"
            )

    def _run(self, **kwargs):
        return _run_with_mocks(self.scripts, _config(**kwargs))

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
        self.assertEqual(
            payload["validation_workflow_path"],
            ".github/workflows/addon-validations.yml",
        )
        self.assertEqual(payload["expected_branch"], "develop")
        self.assertEqual(payload["publication_id"], "plugin.video.twitch@3.1.11")

    def test_payload_has_exact_ordered_fields(self):
        result = self._run()
        payload = json.loads(result["outputs"]["payload"])
        expected = [
            "source_repo",
            "candidate_sha",
            "validation_run_id",
            "validation_head_sha",
            "validation_workflow",
            "validation_workflow_path",
            "expected_branch",
            "publication_id",
        ]
        self.assertEqual(list(payload), expected)

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

    def test_missing_workflow_path_rejected(self):
        result = self._run(run_overrides={"path": None})
        self.assertTrue(result["failed"], "missing workflow path should be rejected")
        self.assertNotIn("payload", result["outputs"])

    def test_wrong_workflow_path_rejected(self):
        result = self._run(
            run_overrides={"path": ".github/workflows/addon-validations.yml@develop"}
        )
        self.assertTrue(result["failed"], "wrong workflow path should be rejected")
        self.assertNotIn("payload", result["outputs"])

    def test_missing_publication_id_rejected(self):
        evidence = _valid_evidence()
        del evidence["publication_id"]
        result = self._run(evidence=evidence)
        self.assertTrue(result["failed"], "missing publication ID should be rejected")
        self.assertNotIn("payload", result["outputs"])

    def test_wrong_publication_id_rejected(self):
        result = self._run(
            evidence=_valid_evidence(publication_id="plugin.video.other@3.1.11")
        )
        self.assertTrue(result["failed"], "wrong publication ID should be rejected")
        self.assertNotIn("payload", result["outputs"])

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

    def test_extra_unrelated_artifact_rejected(self):
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
        self.assertTrue(
            result["failed"], "extra unrelated artifact should cause failure"
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
        cls.scripts = _extract_scripts(cls.data)
        if len(cls.scripts) != 2:
            raise AssertionError("Expected two github-script steps")

    def _run(self, **kwargs):
        return _run_with_mocks(self.scripts, _config(**kwargs))

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
                    "total_count": 2,
                    "artifacts": [
                        {"id": 1, "name": "addon-package", "expired": False},
                    ],
                },
                "2": {
                    "total_count": 2,
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
                    "total_count": 2,
                    "artifacts": [
                        {"id": 1, "name": "addon-package", "expired": False},
                    ],
                },
                "2": {
                    "total_count": 2,
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
                    "total_count": 2,
                    "artifacts": [
                        {"id": 1, "name": "addon-package", "expired": False},
                        {"id": 2, "name": "validation-evidence", "expired": False},
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
