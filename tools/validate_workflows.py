"""Validate workflow YAML files with a real parser.

Parses all workflow YAML files under .github/workflows/ and validates
structural integrity. Optionally runs actionlint if available.
"""

import re
import shlex
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = REPO_ROOT / '.github' / 'workflows'
ACTION_REF_PATTERN = re.compile(r'^[^@\s]+@[0-9a-f]{40}$')
WORKFLOW_REQUIREMENTS_PATTERN = re.compile(
    r'^\.github/workflow-requirements/[A-Za-z0-9._-]+\.txt$'
)
VCS_PREFIXES = ('git+', 'hg+', 'svn+', 'bzr+')
PINNED_VCS_REQUIREMENT_PATTERN = re.compile(
    r'^git\+https://[^@\s]+@[0-9a-f]{40}$'
)
WORKFLOW_PERMISSIONS = {
    'addon-validations.yml': {'contents': 'read'},
    'make-release.yml': {'actions': 'read', 'contents': 'write'},
    'notify-repository.yml': {'actions': 'read'},
    'deploy-pages.yml': {
        'contents': 'read',
        'id-token': 'write',
        'pages': 'write',
    },
}


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


def _uses_references(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'uses':
                yield child
            else:
                yield from _uses_references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _uses_references(child)


def _run_scripts(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'run' and isinstance(child, str):
                yield child
            else:
                yield from _run_scripts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _run_scripts(child)


def _pip_install_argument_sets(command):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|')
    lexer.whitespace_split = True
    lexer.commenters = '#'
    try:
        tokens = list(lexer)
    except ValueError:
        return ()

    commands = [[]]
    for token in tokens:
        if token and all(character in ';&|' for character in token):
            commands.append([])
        else:
            commands[-1].append(token)

    argument_sets = []
    for tokens in commands:
        argument_sets.extend(_extract_pip_install_from_tokens(tokens))
    return argument_sets


def _extract_pip_install_from_tokens(tokens, index=0):
    """Recursively extract pip install argument sets from token list.
    Handles command/env prefixes, shell wrappers (bash -c, sh -c), and command/exec.
    """
    argument_sets = []
    n = len(tokens)
    if index >= n:
        return argument_sets

    # Skip environment variable assignments
    while index < n and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', tokens[index]):
        index += 1

    # Skip 'env' command with its options (--ignore-environment, -i), assignments, and -- separator
    if index < n and Path(tokens[index]).name == 'env':
        index += 1
        while index < n and (
            re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', tokens[index])
            or tokens[index] in ('-i', '--ignore-environment')
        ):
            index += 1
        if index < n and tokens[index] == '--':
            index += 1

    if index >= n:
        return argument_sets

    executable = Path(tokens[index]).name

    # Handle 'command' and 'exec' prefixes - skip options and -- separator
    if executable in ('command', 'exec'):
        index += 1
        # Skip command/exec options like -p, -v, -V, -a label
        while index < n and tokens[index].startswith('-') and tokens[index] != '--':
            if tokens[index] == '-a':
                index += 2  # skip -a and its label
            else:
                index += 1
        if index < n and tokens[index] == '--':
            index += 1
        return _extract_pip_install_from_tokens(tokens, index)

    # Handle shell wrappers: bash/sh with -c or option clusters containing c
    if executable in ('bash', 'sh'):
        index += 1
        # Scan for -c or option cluster containing c, and get the following command string
        shell_command = _find_shell_command_arg(tokens, index, n)
        if shell_command is not None:
            argument_sets.extend(_pip_install_argument_sets(shell_command))
        return argument_sets

    # Direct pip or python -m pip
    if re.fullmatch(r'pip(?:\d+(?:\.\d+)?)?', executable):
        pip_arguments = tokens[index + 1:]
    elif (
        re.fullmatch(r'python(?:\d+(?:\.\d+)?)?', executable)
        and index + 3 < n
        and tokens[index + 1] == '-m'
        and tokens[index + 2] == 'pip'
    ):
        pip_arguments = tokens[index + 3:]
    else:
        return argument_sets

    try:
        install_index = pip_arguments.index('install')
    except ValueError:
        return argument_sets

    argument_sets.append(pip_arguments[install_index + 1:])
    return argument_sets


def _find_shell_command_arg(tokens, index, n):
    """Find the shell command string argument for bash/sh -c.
    Handles: -c "cmd", -c 'cmd', -lc "cmd", -cl "cmd", --longopt -c "cmd", -c -l "cmd"
    Returns the command string or None if not found.
    """
    while index < n:
        token = tokens[index]
        # Long option (--foo)
        if token.startswith('--'):
            index += 1
            continue
        # Standalone -c (check BEFORE short option cluster)
        if token == '-c':
            index += 1
            # Skip any additional options after -c (e.g., -l in bash -c -l "cmd")
            while index < n and tokens[index].startswith('-') and len(tokens[index]) > 1:
                index += 1
            if index < n:
                return tokens[index]
            return None
        # Short option cluster (-abc) or single short option (-l, -c)
        if token.startswith('-') and len(token) > 1 and not token.startswith('--'):
            # Check if this cluster contains 'c'
            if 'c' in token[1:]:
                # Next token should be the command string
                if index + 1 < n:
                    return tokens[index + 1]
                return None
            # Cluster without c, just skip
            index += 1
            continue
        # Not an option, stop scanning
        break
    return None


def _requirement_references(arguments):
    references = []
    for index, argument in enumerate(arguments):
        if argument in ('-r', '--requirement'):
            references.append(
                arguments[index + 1] if index + 1 < len(arguments) else ''
            )
        elif argument.startswith('--requirement='):
            references.append(argument.split('=', 1)[1])
        elif argument.startswith('-r') and len(argument) > 2:
            references.append(argument[2:])
    return references


def _unreviewed_install_arguments(arguments):
    reviewed_indexes = set()
    for index, argument in enumerate(arguments):
        if argument in (
            '--require-hashes', '--no-build-isolation', '--no-deps'
        ):
            reviewed_indexes.add(index)
        elif argument in ('-r', '--requirement'):
            reviewed_indexes.add(index)
            if index + 1 < len(arguments):
                reviewed_indexes.add(index + 1)
        elif (
            argument.startswith('--requirement=')
            or argument.startswith('-r') and len(argument) > 2
            or argument.startswith(VCS_PREFIXES)
        ):
            reviewed_indexes.add(index)
    return [
        argument for index, argument in enumerate(arguments)
        if index not in reviewed_indexes
    ]


def validate_workflow_policy(data, filename=''):
    errors = []
    if not isinstance(data, dict):
        return errors

    for reference in _uses_references(data):
        if (
            not isinstance(reference, str)
            or not ACTION_REF_PATTERN.fullmatch(reference)
        ):
            errors.append(
                f'{filename}: action reference "{reference}" must use a '
                '40-character commit SHA'
            )

    for script in _run_scripts(data):
        for line in script.replace('\\\n', ' ').splitlines():
            for arguments in _pip_install_argument_sets(line.strip()):
                if (
                    any(
                        argument in ('-U', '--upgrade')
                        for argument in arguments
                    )
                    and any(
                        argument.lower() == 'pip' for argument in arguments
                    )
                ):
                    errors.append(f'{filename}: pip self-upgrade is not allowed')
                references = _requirement_references(arguments)
                vcs_requirements = tuple(
                    argument for argument in arguments
                    if argument.startswith(VCS_PREFIXES)
                )
                if references and (
                    '--require-hashes' not in arguments
                    or any(
                        not WORKFLOW_REQUIREMENTS_PATTERN.fullmatch(reference)
                        for reference in references
                    )
                ):
                    errors.append(
                        f'{filename}: mutable requirements references are not '
                        'allowed'
                    )
                if vcs_requirements and any(
                    not PINNED_VCS_REQUIREMENT_PATTERN.fullmatch(requirement)
                    for requirement in vcs_requirements
                ):
                    errors.append(
                        f'{filename}: VCS installs must use a full 40-character '
                        'commit SHA'
                    )
                if vcs_requirements and '--no-deps' not in arguments:
                    errors.append(
                        f'{filename}: VCS installs must use --no-deps'
                    )
                if (
                    vcs_requirements
                    and '--no-build-isolation' not in arguments
                ):
                    errors.append(
                        f'{filename}: VCS installs must use '
                        '--no-build-isolation'
                    )
                if (
                    not references and not vcs_requirements
                    or _unreviewed_install_arguments(arguments)
                ):
                    errors.append(
                        f'{filename}: pip installs must use hash-locked '
                        'requirements'
                    )

    expected = WORKFLOW_PERMISSIONS.get(filename)
    if expected is None:
        errors.append(f'{filename}: no reviewed workflow permissions policy')
        return errors

    permissions = data.get('permissions')
    if permissions is None:
        errors.append(f'{filename}: missing top-level permissions')
    elif permissions != expected:
        errors.append(
            f'{filename}: top-level permissions must be exactly {expected}'
        )

    jobs = data.get('jobs', {})
    if isinstance(jobs, dict):
        for job_name, job in jobs.items():
            if not isinstance(job, dict) or 'permissions' not in job:
                continue
            job_permissions = job['permissions']
            if not isinstance(job_permissions, dict):
                errors.append(
                    f'{filename}: job "{job_name}" has overbroad job permissions'
                )
                continue
            for scope, access in job_permissions.items():
                allowed = expected.get(scope)
                if (
                    access not in ('none', 'read', 'write')
                    or allowed is None
                    or access == 'write' and allowed != 'write'
                ):
                    errors.append(
                        f'{filename}: job "{job_name}" has overbroad job '
                        f'permissions for {scope}'
                    )

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
            all_errors.extend(validate_workflow_policy(data, yml.name))
        except Exception as exc:
            all_errors.append(f'{yml.name}: parse error: {exc}')

    for yaml_file in sorted(workflows_dir.glob('*.yaml')):
        if yaml_file.name not in parsed:
            try:
                data = parse_yaml_file(yaml_file)
                parsed[yaml_file.name] = data
                errors = validate_workflow_structure(data, yaml_file.name)
                all_errors.extend(errors)
                all_errors.extend(
                    validate_workflow_policy(data, yaml_file.name)
                )
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
