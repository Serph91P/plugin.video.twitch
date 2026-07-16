"""Staged branch coverage gate for critical modules.

Measures branch coverage on critical runtime modules and fails closed
if the result is below the measured baseline threshold or if any
declared critical module is absent from the coverage report.

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


def check_critical_module_presence(source_dir, expected_modules=None):
    """Verify every expected critical module appears in the coverage report.

    Returns a list of missing module paths. Empty list means all present.
    """
    if expected_modules is None:
        expected_modules = CRITICAL_MODULES

    missing = []
    for mod in expected_modules:
        full = os.path.join(source_dir, mod)
        if not os.path.exists(full):
            missing.append(mod)
    return missing


def _parse_coverage_report_modules(report_text):
    """Extract module file paths from a coverage report output."""
    modules = set()
    for line in report_text.splitlines():
        line = line.strip()
        if not line or line.startswith('---') or line.startswith('Name'):
            continue
        if line.startswith('TOTAL'):
            continue
        parts = line.split()
        if parts:
            modules.add(parts[0])
    return modules


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
            'modules_missing': list(CRITICAL_MODULES),
            'error': 'no critical modules found',
        }

    missing = check_critical_module_presence(source_dir)
    if missing:
        return {
            'branch_coverage': 0.0,
            'threshold': BRANCH_COVERAGE_THRESHOLD,
            'passed': False,
            'modules_found': source_files,
            'modules_missing': missing,
            'error': f'critical modules missing from source: {missing}',
        }

    include_pattern = ','.join(source_files)

    subprocess.run(
        [sys.executable, '-m', 'coverage', 'run',
         '--branch', f'--include={include_pattern}',
         '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
        capture_output=True, text=True, cwd=source_dir, check=False,
    )

    cov_result = subprocess.run(
        [sys.executable, '-m', 'coverage', 'report', '--format=total'],
        capture_output=True, text=True, cwd=source_dir, check=False,
    )

    detail_result = subprocess.run(
        [sys.executable, '-m', 'coverage', 'report'],
        capture_output=True, text=True, cwd=source_dir, check=False,
    )

    branch_pct = 0.0
    if cov_result.returncode == 0:
        try:
            branch_pct = float(cov_result.stdout.strip().rstrip('%'))
        except (ValueError, AttributeError):
            pass

    report_modules = set()
    if detail_result.returncode == 0:
        report_modules = _parse_coverage_report_modules(detail_result.stdout)

    report_absent = []
    for mod in CRITICAL_MODULES:
        full = os.path.join(source_dir, mod)
        if full not in report_modules:
            report_absent.append(mod)

    passed = branch_pct >= BRANCH_COVERAGE_THRESHOLD and not report_absent

    return {
        'branch_coverage': branch_pct,
        'threshold': BRANCH_COVERAGE_THRESHOLD,
        'passed': passed,
        'modules_found': source_files,
        'modules_missing': report_absent,
    }
