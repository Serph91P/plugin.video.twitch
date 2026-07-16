"""Tests for commit SHA validation.

Verifies that candidate commit SHA validation correctly detects
matches, mismatches, and branch identity.
"""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.validate_commit import (
    compute_tree_sha,
    git_branch_name,
    git_rev_parse,
    validate_branch,
    validate_commit_sha,
)


class CommitShaValidationTests(unittest.TestCase):
    def test_matching_sha_passes(self):
        sha = git_rev_parse('HEAD')
        errors = validate_commit_sha(sha, sha)
        self.assertEqual(errors, [])

    def test_mismatched_sha_fails(self):
        errors = validate_commit_sha('aaa', 'bbb')
        self.assertEqual(len(errors), 1)
        self.assertIn('mismatch', errors[0])

    def test_current_head_is_valid_sha(self):
        sha = git_rev_parse('HEAD')
        self.assertEqual(len(sha), 40)
        self.assertTrue(all(c in '0123456789abcdef' for c in sha))

    def test_branch_name_returns_string(self):
        name = git_branch_name()
        self.assertIsInstance(name, str)
        self.assertTrue(len(name) > 0)

    def test_branch_validation_passes_for_current(self):
        name = git_branch_name()
        errors = validate_branch(name)
        self.assertEqual(errors, [])

    def test_branch_validation_fails_for_wrong_name(self):
        errors = validate_branch('nonexistent-branch-xyz')
        self.assertEqual(len(errors), 1)
        self.assertIn('mismatch', errors[0])

    def test_compute_tree_sha_returns_hash(self):
        tree = compute_tree_sha()
        self.assertEqual(len(tree), 40)
        self.assertTrue(all(c in '0123456789abcdef' for c in tree))


if __name__ == '__main__':
    unittest.main()
