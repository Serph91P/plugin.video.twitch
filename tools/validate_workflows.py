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


_GNU_ENV_LONG_OPTIONS = frozenset((
    'argv0',
    'block-signal',
    'chdir',
    'debug',
    'default-signal',
    'help',
    'ignore-environment',
    'ignore-signal',
    'list-signal-handling',
    'null',
    'split-string',
    'unset',
    'version',
))

_GNU_ENV_OPTIONS_WITH_OPERAND = frozenset((
    'argv0',
    'block-signal',
    'chdir',
    'default-signal',
    'ignore-signal',
    'split-string',
    'unset',
))

_GNU_ENV_OPTIONS_WITH_SEPARATED_OPERAND = frozenset((
    'argv0',
    'chdir',
    'split-string',
    'unset',
))

_GNU_ENV_TERMINAL_OPTIONS = frozenset((
    'help',
    'version',
))


def _is_gnu_env_long_option_abbreviation(token):
    """Check if token is an unambiguous abbreviation of a GNU env long option.

    Returns the canonical option name (without --) if it is an unambiguous
    abbreviation, None otherwise.
    """
    if not token.startswith('--'):
        return None
    option_name = token[2:]
    if '=' in option_name:
        option_name = option_name.split('=', 1)[0]
    if option_name in _GNU_ENV_LONG_OPTIONS:
        return option_name
    matches = [opt for opt in _GNU_ENV_LONG_OPTIONS if opt.startswith(option_name)]
    if len(matches) == 1:
        return matches[0]
    return None


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


def _parse_env_short_options(token, tokens, index, n):
    """Parse combined GNU env short options from a single token.

    Returns ``(new_index, new_split_strings)`` where *new_index* is the
    position after consuming the current token and any separated operands,
    and *new_split_strings* is a list of ``-S`` operand values collected.

    Returns ``None`` when the token contains an unrecognised option flag.
    """
    split_strings = []
    pos = 1
    advance = 1
    while pos < len(token):
        c = token[pos]
        if c in ('i', 'v'):
            pos += 1
        elif c in ('u', 'C', 'a'):
            pos += 1
            if pos < len(token):
                pos = len(token)
            else:
                advance += 1
        elif c == 'S':
            pos += 1
            if pos < len(token):
                split_strings.append(token[pos:])
                pos = len(token)
            else:
                if index + advance < n:
                    split_strings.append(tokens[index + advance])
                advance += 1
        else:
            return None
    return (index + advance, split_strings)


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

    # Skip 'env' command with its options, assignments, and -- separator
    if index < n and Path(tokens[index]).name == 'env':
        index += 1
        split_strings = []
        while index < n:
            token = tokens[index]
            if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', token):
                index += 1
            elif token == '--ignore-environment':
                index += 1
            else:
                canonical = _is_gnu_env_long_option_abbreviation(token)
                if canonical is not None:
                    # Terminal options (--help, --version) do not execute a command;
                    # stop parsing env options and let the outer logic handle them.
                    if canonical in _GNU_ENV_TERMINAL_OPTIONS:
                        break
                    # --split-string (-S): collect the operand for later processing
                    if canonical == 'split-string':
                        if '=' in token:
                            split_strings.append(token.split('=', 1)[1])
                            index += 1
                        else:
                            if index + 1 < n:
                                split_strings.append(tokens[index + 1])
                            index += 2
                    # Options with operands: consume operand based on supported forms
                    elif canonical in _GNU_ENV_OPTIONS_WITH_OPERAND:
                        if '=' not in token:
                            # Separated operand form: --option value
                            # Only some options support this form
                            if canonical in _GNU_ENV_OPTIONS_WITH_SEPARATED_OPERAND:
                                index += 2
                            else:
                                # This option doesn't support separated form;
                                # stop parsing env options
                                break
                        else:
                            # Equals form: --option=value
                            index += 1
                    else:
                        # No-operand options: --debug, --list-signal-handling, --ignore-environment
                        index += 1
                elif (
                    token.startswith('-')
                    and not token.startswith('--')
                    and len(token) > 1
                ):
                    result = _parse_env_short_options(
                        token, tokens, index, n
                    )
                    if result is None:
                        break
                    index, new_split_strings = result
                    split_strings.extend(new_split_strings)
                else:
                    break
        if index < n and tokens[index] == '--':
            index += 1
        if split_strings:
            extra = []
            for s in split_strings:
                try:
                    extra.extend(_gnu_env_split(s))
                except ValueError:
                    return argument_sets
            tokens = tokens[:index] + extra + tokens[index:]
            n = len(tokens)
            while (
                index < n
                and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', tokens[index])
            ):
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
    _LONG_OPTIONS_WITH_OPERANDS = frozenset((
        '--rcfile', '--init-file',
    ))
    while index < n:
        token = tokens[index]
        # Long option (--foo or --foo=bar)
        if token.startswith('--'):
            option_name = token.split('=', 1)[0]
            if '=' in token or option_name not in _LONG_OPTIONS_WITH_OPERANDS:
                index += 1
            else:
                index += 1
                if index < n:
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
            # Short options -O and -o take an operand
            if len(token) == 2 and token[1] in ('O', 'o'):
                index += 2
            else:
                index += 1
            continue
        # Not an option, stop scanning
        break
    return None


_ESCAPED_CH = {
    'f': '\f', 'n': '\n', 'r': '\r', 't': '\t', 'v': '\v',
    '#': '#', '$': '$', '_': ' ', '"': '"', "'": "'", '\\': '\\',
}


class _SplitStringPolicyError(Exception):
    """Raised when an env -S string contains a dynamic pattern that must
    be rejected rather than evaluated (e.g. ``${VARNAME}`` expansion)."""
    pass


def _gnu_env_split(s):
    """Tokenize a GNU env -S string per documented lexical semantics.

    Implements the split-string syntax from GNU Coreutils env(1):

    * Outside quotes: ``\\_`` is an argument separator.
    * Outside quotes: ``\\t``, ``\\n``, ``\\f``, ``\\r``, ``\\v`` produce
      literal control characters (tab, newline, etc.) inside the current
      argument.  GNU coreutils does NOT treat them as separators.
    * Inside double quotes: the same escapes produce literal characters
      (space for ``\\_``, tab for ``\\t``, etc.).  ``\\c`` is *not*
      recognized inside double quotes.
    * ``\\c`` outside quotes ignores the remainder of the string.
    * ``#`` as the first character of an unquoted argument ignores the
      remainder of the string.  ``\\#`` yields a literal hash.
    * Single quotes disable all escapes except ``\\'`` and ``\\\\``.
    * ``${VARNAME}`` expansion patterns are rejected (fail-closed)
      since they could bypass mutable-install policy.
    """
    if '${' in s:
        raise _SplitStringPolicyError(
            'variable expansion ${...} in env -S string'
        )
    result = []
    token = []
    i = 0
    n = len(s)
    in_dquote = False
    in_squote = False
    first_unquoted = True

    def _flush_token():
        nonlocal first_unquoted
        if token:
            result.append(''.join(token))
            token.clear()
        first_unquoted = False

    while i < n:
        ch = s[i]

        if in_squote:
            if ch == '\\' and i + 1 < n and s[i + 1] in ("'", '\\'):
                token.append(s[i + 1])
                i += 2
                continue
            if ch == "'":
                in_squote = False
                i += 1
                continue
            token.append(ch)
            i += 1
            continue

        if in_dquote:
            if ch == '"':
                in_dquote = False
                i += 1
                continue
            if ch == '\\' and i + 1 < n:
                nxt = s[i + 1]
                if nxt == '\n':
                    i += 2
                    continue
                mapped = _ESCAPED_CH.get(nxt)
                if mapped is not None:
                    token.append(mapped)
                else:
                    token.append('\\')
                    token.append(nxt)
                i += 2
                continue
            token.append(ch)
            i += 1
            continue

        if ch == "'":
            in_squote = True
            first_unquoted = False
            i += 1
            continue
        if ch == '"':
            in_dquote = True
            first_unquoted = False
            i += 1
            continue
        if ch in (' ', '\t', '\n', '\r', '\f', '\v'):
            _flush_token()
            first_unquoted = True
            i += 1
            continue
        if ch == '#':
            if first_unquoted:
                break
            token.append(ch)
            i += 1
            continue
        if ch == '\\' and i + 1 < n:
            nxt = s[i + 1]
            if nxt == '\n':
                i += 2
                continue
            if nxt == 'c':
                i += 2
                break
            mapped = _ESCAPED_CH.get(nxt)
            if mapped is not None:
                if nxt == '_':
                    _flush_token()
                    first_unquoted = True
                else:
                    token.append(mapped)
            else:
                token.append('\\')
                token.append(nxt)
            i += 2
            continue
        token.append(ch)
        first_unquoted = False
        i += 1

    if in_squote or in_dquote:
        raise ValueError('unterminated quote in env -S string')
    _flush_token()
    return result


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
            try:
                argument_sets = _pip_install_argument_sets(line.strip())
            except _SplitStringPolicyError:
                errors.append(
                    f'{filename}: env -S variable expansion is not allowed'
                )
                continue
            for arguments in argument_sets:
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
