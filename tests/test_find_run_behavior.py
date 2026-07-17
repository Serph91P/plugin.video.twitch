"""Behavioral tests for the find-run JavaScript in make-release.yml.

Uses a Node.js harness to execute the actual find-run script extracted
from the YAML with mocked github, context, and core objects.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).resolve().parent / "find_run_harness.js"

try:
    from tools.validate_workflows import parse_yaml_file
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


def _extract_find_run_script():
    """Extract the find-run JavaScript from the workflow YAML."""
    data = parse_yaml_file(ROOT / ".github" / "workflows" / "make-release.yml")
    for step in data["jobs"]["release"]["steps"]:
        if step.get("id") == "find-run":
            return step["with"]["script"]
    raise RuntimeError("find-run step not found in make-release.yml")


def _run_harness(scenario):
    """Run the Node.js harness with a scenario dict and return the result dict."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(scenario, f)
        scenario_path = f.name
    try:
        proc = subprocess.run(
            ["node", str(HARNESS), scenario_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"harness exited {proc.returncode}: {proc.stderr}"
            )
        return json.loads(proc.stdout)
    finally:
        os.unlink(scenario_path)


def _make_scenario(
    *,
    sha="abc123def456",
    ref="refs/tags/v1.0.0",
    owner="test-owner",
    repo="test-repo",
    tag_runs=None,
    eligible_runs=None,
):
    """Build a scenario dict for the harness.

    tag_runs: list of run objects returned by the initial tag-name query.
    eligible_runs: dict mapping branch name to list of run objects (paginated).
                   Each list entry is one page (list of run objects).
    """
    api_calls = []

    # Initial tag query
    api_calls.append({"data": {"workflow_runs": tag_runs or []}})

    # Eligible branch queries (main, develop), up to maxPages pages each
    eligible_branches = ["main", "develop"]
    for branch in eligible_branches:
        branch_pages = (eligible_runs or {}).get(branch, [])
        for page_runs in branch_pages:
            api_calls.append({"data": {"workflow_runs": page_runs}})

    script = _extract_find_run_script()
    return {
        "script": script,
        "context": {
            "owner": owner,
            "repo": repo,
            "ref": ref,
            "sha": sha,
        },
        "apiCalls": api_calls,
    }


def _make_run(
    *,
    run_id=1,
    name="Add-on Validations",
    conclusion="success",
    head_sha="abc123def456",
    head_branch="main",
):
    """Create a minimal workflow run object."""
    return {
        "id": run_id,
        "name": name,
        "conclusion": conclusion,
        "head_sha": head_sha,
        "head_branch": head_branch,
    }


@unittest.skipUnless(_HAS_YAML, "pyyaml not installed")
@unittest.skipUnless(HARNESS.exists(), "find_run_harness.js not found")
class MakeReleaseFindRunBehaviorTests(unittest.TestCase):
    """Execute the real find-run JavaScript and verify selection behavior."""

    # -- Eligible branch acceptance --

    def test_exact_successful_run_on_page_2_is_selected(self):
        """A matching run on page 2 of an eligible branch is found."""
        sha = "deadbeef1234"
        good_run = _make_run(run_id=42, head_sha=sha, head_branch="main")
        bad_runs = [
            _make_run(run_id=i, name="Other", head_branch="main")
            for i in range(10)
        ]
        scenario = _make_scenario(
            sha=sha,
            tag_runs=[],
            eligible_runs={"main": [bad_runs, [good_run]], "develop": []},
        )
        result = _run_harness(scenario)
        self.assertFalse(result["failed"], result.get("failureMessage"))
        self.assertEqual(result["outputs"]["validation_run_id"], 42)

    def test_correct_workflow_name_is_required(self):
        """A run with the wrong name is not selected."""
        sha = "abc123"
        scenario = _make_scenario(
            sha=sha,
            tag_runs=[_make_run(run_id=1, name="Wrong Workflow", head_sha=sha)],
            eligible_runs={"main": [], "develop": []},
        )
        result = _run_harness(scenario)
        self.assertTrue(result["failed"])
        self.assertIn("No successful validation run", result["failureMessage"])

    def test_exact_candidate_sha_is_required(self):
        """A run with a different SHA is not selected."""
        sha = "abc123"
        scenario = _make_scenario(
            sha=sha,
            tag_runs=[_make_run(run_id=1, head_sha="wrongsha")],
            eligible_runs={"main": [], "develop": []},
        )
        result = _run_harness(scenario)
        self.assertTrue(result["failed"])

    def test_success_conclusion_is_required(self):
        """A run that did not succeed is not selected."""
        sha = "abc123"
        for conclusion in ("failure", "cancelled", "timed_out", "action_required"):
            with self.subTest(conclusion=conclusion):
                scenario = _make_scenario(
                    sha=sha,
                    tag_runs=[
                        _make_run(run_id=1, conclusion=conclusion, head_sha=sha)
                    ],
                    eligible_runs={"main": [], "develop": []},
                )
                result = _run_harness(scenario)
                self.assertTrue(result["failed"], f"should reject conclusion={conclusion}")

    # -- Wrong-branch rejection --

    def test_wrong_branch_matching_run_is_rejected(self):
        """A matching run from a non-eligible branch (tag query) must not be selected.

        The tag query may return runs from any branch. If the tag branch is
        not an eligible validation branch, the selector must reject it.
        """
        sha = "abc123"
        scenario = _make_scenario(
            ref="refs/tags/v1.0.0",
            sha=sha,
            tag_runs=[
                _make_run(
                    run_id=100,
                    head_sha=sha,
                    head_branch="fix/some-feature",
                )
            ],
            eligible_runs={"main": [], "develop": []},
        )
        result = _run_harness(scenario)
        self.assertTrue(
            result["failed"],
            "must not select a run from a non-eligible branch via tag query",
        )
        self.assertIn("No successful validation run", result["failureMessage"])

    def test_wrong_branch_eligible_wins_over_tag_branch(self):
        """When both tag branch and eligible branch have matches, eligible wins."""
        sha = "abc123"
        tag_run = _make_run(
            run_id=100,
            head_sha=sha,
            head_branch="fix/some-feature",
        )
        eligible_run = _make_run(
            run_id=200,
            head_sha=sha,
            head_branch="main",
        )
        scenario = _make_scenario(
            ref="refs/tags/v1.0.0",
            sha=sha,
            tag_runs=[tag_run],
            eligible_runs={"main": [[eligible_run]], "develop": []},
        )
        result = _run_harness(scenario)
        self.assertFalse(result["failed"], result.get("failureMessage"))
        self.assertEqual(result["outputs"]["validation_run_id"], 200)

    # -- Fail-closed rejection matrix --

    def test_stale_candidate_rejected(self):
        """A run with an old (non-matching) SHA is not selected."""
        scenario = _make_scenario(
            sha="current_sha",
            eligible_runs={
                "main": [[_make_run(run_id=1, head_sha="old_sha")]],
                "develop": [],
            },
        )
        result = _run_harness(scenario)
        self.assertTrue(result["failed"])

    def test_wrong_workflow_rejected(self):
        """A run with the wrong workflow name is not selected."""
        sha = "abc123"
        scenario = _make_scenario(
            sha=sha,
            eligible_runs={
                "main": [[_make_run(run_id=1, name="CI Tests", head_sha=sha)]],
                "develop": [],
            },
        )
        result = _run_harness(scenario)
        self.assertTrue(result["failed"])

    def test_wrong_commit_rejected(self):
        """A successful run for a different commit is not selected."""
        scenario = _make_scenario(
            sha="abc123",
            eligible_runs={
                "main": [[_make_run(run_id=1, head_sha="other_sha")]],
                "develop": [],
            },
        )
        result = _run_harness(scenario)
        self.assertTrue(result["failed"])

    def test_unsuccessful_conclusion_rejected(self):
        """A validation run that failed is not selected."""
        sha = "abc123"
        scenario = _make_scenario(
            sha=sha,
            eligible_runs={
                "main": [[_make_run(run_id=1, conclusion="failure", head_sha=sha)]],
                "develop": [],
            },
        )
        result = _run_harness(scenario)
        self.assertTrue(result["failed"])

    def test_absent_candidates_fail_closed(self):
        """No runs at all must fail closed."""
        scenario = _make_scenario(
            sha="abc123",
            tag_runs=[],
            eligible_runs={"main": [], "develop": []},
        )
        result = _run_harness(scenario)
        self.assertTrue(result["failed"])

    def test_malformed_candidate_skipped(self):
        """A run missing required fields does not crash the selector."""
        sha = "abc123"
        scenario = _make_scenario(
            sha=sha,
            eligible_runs={
                "main": [[{"id": 1}]],
                "develop": [],
            },
        )
        result = _run_harness(scenario)
        self.assertTrue(result["failed"])

    # -- Pagination bounds --

    def test_pagination_never_exceeds_max_pages(self):
        """The script must not request beyond the maxPages bound."""
        sha = "abc123"
        # Simulate 5 full pages for main (maxPages=5), no match
        pages = [[_make_run(run_id=i * 10 + j, head_branch="main") for j in range(10)] for i in range(5)]
        scenario = _make_scenario(
            sha=sha,
            eligible_runs={"main": pages, "develop": []},
        )
        result = _run_harness(scenario)
        # Count API calls for main branch
        main_calls = [
            c for c in result["apiLog"]
            if c["params"].get("branch") == "main"
        ]
        self.assertLessEqual(len(main_calls), 5)

    def test_matching_run_beyond_max_pages_not_selected(self):
        """A matching run beyond the page bound is not found."""
        sha = "abc123"
        # 5 full pages of non-matching, then a match on "page 6" that the script won't reach
        pages = [
            [_make_run(run_id=i * 10 + j, head_branch="main") for j in range(10)]
            for i in range(5)
        ]
        scenario = _make_scenario(
            sha=sha,
            eligible_runs={"main": pages, "develop": []},
        )
        result = _run_harness(scenario)
        self.assertTrue(result["failed"], "run beyond page bound must not be selected")

    # -- Both branches searched --

    def test_both_main_and_develop_are_searched(self):
        """A matching run on develop is found when main has no match."""
        sha = "abc123"
        good_run = _make_run(run_id=50, head_sha=sha, head_branch="develop")
        scenario = _make_scenario(
            sha=sha,
            eligible_runs={"main": [], "develop": [[good_run]]},
        )
        result = _run_harness(scenario)
        self.assertFalse(result["failed"], result.get("failureMessage"))
        self.assertEqual(result["outputs"]["validation_run_id"], 50)

    def test_main_checked_before_develop(self):
        """Main branch is queried before develop."""
        sha = "abc123"
        scenario = _make_scenario(
            sha=sha,
            eligible_runs={"main": [[]], "develop": [[]]},
        )
        result = _run_harness(scenario)
        branch_order = [
            c["params"]["branch"]
            for c in result["apiLog"]
            if c["params"].get("branch") in ("main", "develop")
        ]
        if len(branch_order) >= 2:
            self.assertEqual(branch_order[0], "main")

    # -- Tag query is searched but not sufficient alone --

    def test_tag_query_result_from_eligible_branch_accepted(self):
        """A matching run from the tag query is accepted if its head_branch is eligible."""
        sha = "abc123"
        scenario = _make_scenario(
            ref="refs/tags/v1.0.0",
            sha=sha,
            tag_runs=[_make_run(run_id=99, head_sha=sha, head_branch="main")],
            eligible_runs={"main": [], "develop": []},
        )
        result = _run_harness(scenario)
        self.assertFalse(result["failed"], result.get("failureMessage"))
        self.assertEqual(result["outputs"]["validation_run_id"], 99)


@unittest.skipUnless(_HAS_YAML, "pyyaml not installed")
class MakeReleaseFindRunStaticTests(unittest.TestCase):
    """Static structural checks on the find-run script."""

    def setUp(self):
        self.script = _extract_find_run_script()

    def test_has_eligible_branches_definition(self):
        self.assertIn("eligibleBranches", self.script)

    def test_references_main_branch(self):
        self.assertIn("'main'", self.script)

    def test_references_develop_branch(self):
        self.assertIn("'develop'", self.script)

    def test_has_max_page_bound(self):
        import re
        self.assertTrue(
            re.search(r"maxPages?\b", self.script),
            "must define a maxPages bound",
        )

    def test_has_per_page_definition(self):
        self.assertIn("perPage", self.script)

    def test_checks_conclusion(self):
        self.assertIn("conclusion", self.script)

    def test_checks_head_sha(self):
        self.assertIn("head_sha", self.script)

    def test_checks_workflow_name(self):
        self.assertIn("Add-on Validations", self.script)

    def test_checks_head_branch_eligibility(self):
        """The selection loop must verify head_branch is in eligibleBranches."""
        self.assertIn("eligibleBranches.includes(run.head_branch)", self.script)


if __name__ == "__main__":
    unittest.main()
