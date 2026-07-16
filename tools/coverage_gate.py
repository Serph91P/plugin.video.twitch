"""Staged branch coverage gate for critical modules.

Measures branch coverage on critical runtime modules and fails closed
if the result is below the measured baseline threshold.

Measured baseline: 48% branch coverage across critical modules.
Threshold: 45% (conservative, below current baseline, impossible to
pass with zero tests).
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

CRITICAL_MODULES = [
    'resources/lib/twitch_addon/addon/watch_history.py',
    'resources/lib/twitch_addon/addon/common/search_history.py',
    'resources/lib/twitch_addon/addon/common/kodi.py',
    'resources/lib/twitch_addon/addon/utils.py',
    'resources/lib/twitch_addon/addon/constants.py',
    'resources/lib/twitch_addon/router.py',
]

BRANCH_COVERAGE_THRESHOLD = 45


def get_coverage_threshold():
    return BRANCH_COVERAGE_THRESHOLD


def get_critical_modules():
    return list(CRITICAL_MODULES)


def measure_branch_coverage(source_dir=None):
    if source_dir is None:
        source_dir = str(REPO_ROOT)

    source_files = []
    for mod in CRITICAL_MODULES:
        full = os.path.join(source_dir, mod)
        if os.path.exists(full):
            source_files.append(full)

    if not source_files:
        return {
            'branch_coverage': 0.0,
            'threshold': BRANCH_COVERAGE_THRESHOLD,
            'passed': False,
            'modules_found': [],
            'error': 'no critical modules found',
        }

    include_pattern = ','.join(source_files)

    result = subprocess.run(
        [sys.executable, '-m', 'coverage', 'run',
         '--branch', f'--include={include_pattern}',
         '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
        capture_output=True, text=True, cwd=source_dir, check=False,
    )

    cov_result = subprocess.run(
        [sys.executable, '-m', 'coverage', 'report', '--format=total'],
        capture_output=True, text=True, cwd=source_dir, check=False,
    )

    branch_pct = 0.0
    if cov_result.returncode == 0:
        try:
            branch_pct = float(cov_result.stdout.strip().rstrip('%'))
        except (ValueError, AttributeError):
            pass

    passed = branch_pct >= BRANCH_COVERAGE_THRESHOLD

    return {
        'branch_coverage': branch_pct,
        'threshold': BRANCH_COVERAGE_THRESHOLD,
        'passed': passed,
        'modules_found': source_files,
    }
