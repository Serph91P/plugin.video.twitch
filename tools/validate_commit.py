"""Validate that a candidate commit SHA matches the expected identity.

This prevents branch movement or cherry-pick drift from silently
publishing a different commit than the one that passed validation.
"""

import hashlib
import subprocess
import sys


def git_rev_parse(ref='HEAD'):
    result = subprocess.run(
        ['git', 'rev-parse', ref],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f'git rev-parse {ref} failed: {result.stderr.strip()}')
    return result.stdout.strip()


def git_branch_name():
    result = subprocess.run(
        ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f'git branch failed: {result.stderr.strip()}')
    return result.stdout.strip()


def validate_commit_sha(candidate_sha, expected_sha):
    errors = []
    if candidate_sha != expected_sha:
        errors.append(
            f'candidate SHA mismatch: candidate={candidate_sha}, expected={expected_sha}'
        )
    return errors


def validate_branch(expected_branch):
    errors = []
    actual = git_branch_name()
    if actual != expected_branch:
        errors.append(f'branch mismatch: expected {expected_branch}, got {actual}')
    return errors


def compute_tree_sha(commit_sha=None):
    if commit_sha is None:
        commit_sha = git_rev_parse('HEAD')
    result = subprocess.run(
        ['git', 'rev-parse', f'{commit_sha}^{{tree}}'],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f'git tree sha failed: {result.stderr.strip()}')
    return result.stdout.strip()
