"""Validate workflow YAML files with a real parser.

Parses all workflow YAML files under .github/workflows/ and validates
structural integrity. Optionally runs actionlint if available.
"""

import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = REPO_ROOT / '.github' / 'workflows'


def parse_yaml_file(path):
    if yaml is None:
        raise ImportError('pyyaml is required: pip install pyyaml')
    path = Path(path)
    text = path.read_text(encoding='utf-8')
    data = yaml.safe_load(text)
    return data


def validate_workflow_structure(data, filename=''):
    errors = []
    if not isinstance(data, dict):
        errors.append(f'{filename}: workflow must be a mapping')
        return errors

    if 'on' not in data and True not in data:
        errors.append(f'{filename}: missing "on" trigger')

    if 'jobs' not in data:
        errors.append(f'{filename}: missing "jobs" section')
    elif not isinstance(data['jobs'], dict):
        errors.append(f'{filename}: "jobs" must be a mapping')
    else:
        for job_name, job_def in data['jobs'].items():
            if not isinstance(job_def, dict):
                errors.append(f'{filename}: job "{job_name}" must be a mapping')
                continue
            if 'runs-on' not in job_def and 'uses' not in job_def:
                errors.append(f'{filename}: job "{job_name}" missing runs-on')
            if 'steps' not in job_def and 'uses' not in job_def:
                errors.append(f'{filename}: job "{job_name}" missing steps')

    return errors


def validate_all_workflows(workflows_dir=None):
    if workflows_dir is None:
        workflows_dir = WORKFLOWS_DIR
    else:
        workflows_dir = Path(workflows_dir)

    all_errors = []
    parsed = {}

    for yml in sorted(workflows_dir.glob('*.yml')):
        try:
            data = parse_yaml_file(yml)
            parsed[yml.name] = data
            errors = validate_workflow_structure(data, yml.name)
            all_errors.extend(errors)
        except Exception as exc:
            all_errors.append(f'{yml.name}: parse error: {exc}')

    for yaml_file in sorted(workflows_dir.glob('*.yaml')):
        if yaml_file.name not in parsed:
            try:
                data = parse_yaml_file(yaml_file)
                parsed[yaml_file.name] = data
                errors = validate_workflow_structure(data, yaml_file.name)
                all_errors.extend(errors)
            except Exception as exc:
                all_errors.append(f'{yaml_file.name}: parse error: {exc}')

    return parsed, all_errors


def run_actionlint(workflows_dir=None):
    result = subprocess.run(
        ['actionlint'],
        capture_output=True, text=True,
        cwd=str(workflows_dir.parent.parent) if workflows_dir else str(REPO_ROOT),
        check=False,
    )
    return {
        'returncode': result.returncode,
        'stdout': result.stdout,
        'stderr': result.stderr,
    }
