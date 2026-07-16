"""Build a deterministic runtime-only ZIP for the Kodi add-on.

The package is assembled from an explicit allowlist and rejects any
repository-only or source-only content that must never ship in a
release artifact.
"""

import hashlib
import io
import os
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ADDON_ID_PATTERN = re.compile(r'^plugin\.video\.')

RUNTIME_ENTRIES = (
    'addon.xml',
    'changelog.txt',
    'resources/',
)

FORBIDDEN_PREFIXES = (
    '.github/',
    '.patches/',
    'docs/',
    'tests/',
    'LICENSES/',
)

FORBIDDEN_EXACT = frozenset({
    'README.md',
    'CONTRIBUTORS.md',
    '.gitignore',
})


def _normalize_member(path):
    return path.replace(os.sep, '/')


def _parse_addon_identity(addon_xml_text):
    root = ET.fromstring(addon_xml_text)
    return root.attrib['id'], root.attrib['version'], root.attrib.get('name', '')


def _is_runtime_member(relpath):
    normalized = _normalize_member(relpath)
    if normalized.startswith('__pycache__/') or normalized.endswith('.pyc'):
        return False
    for prefix in FORBIDDEN_PREFIXES:
        if normalized.startswith(prefix):
            return False
    basename = normalized.split('/')[-1]
    if basename in FORBIDDEN_EXACT:
        return False
    for entry in RUNTIME_ENTRIES:
        if entry.endswith('/'):
            if normalized.startswith(entry):
                return True
        elif normalized == entry:
            return True
    return False


def collect_runtime_members(source_dir):
    source_dir = Path(source_dir)
    members = []
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__')
        for name in sorted(files):
            full = Path(root) / name
            rel = _normalize_member(str(full.relative_to(source_dir)))
            if rel.endswith('.pyc'):
                continue
            if _is_runtime_member(rel):
                members.append(rel)
    return sorted(members)


def build_package(source_dir, output_path):
    source_dir = Path(source_dir)
    output_path = Path(output_path)

    addon_xml = source_dir / 'addon.xml'
    if not addon_xml.exists():
        raise FileNotFoundError('addon.xml not found in source directory')

    addon_xml_text = addon_xml.read_text(encoding='utf-8')
    addon_id, addon_version, addon_name = _parse_addon_identity(addon_xml_text)

    if not ADDON_ID_PATTERN.match(addon_id):
        raise ValueError(f'addon id does not match expected pattern: {addon_id}')

    members = collect_runtime_members(source_dir)

    required = ['addon.xml', 'changelog.txt']
    for req in required:
        if req not in members:
            raise FileNotFoundError(f'required runtime member missing: {req}')

    resources_found = any(m.startswith('resources/') for m in members)
    if not resources_found:
        raise FileNotFoundError('resources/ directory missing from runtime members')

    output_path.parent.mkdir(parents=True, exist_ok=True)

    prefix = addon_id + '/'

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for member in members:
            full = source_dir / member
            data = full.read_bytes()
            info = zipfile.ZipInfo(prefix + member)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)

    sha256 = hashlib.sha256()
    with open(output_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)

    checksum = sha256.hexdigest()
    filename = f'{addon_id}-{addon_version}.zip'

    return {
        'addon_id': addon_id,
        'addon_version': addon_version,
        'addon_name': addon_name,
        'filename': filename,
        'checksum': checksum,
        'member_count': len(members),
        'members': members,
        'output_path': str(output_path),
    }


def validate_package(output_path, source_dir):
    source_dir = Path(source_dir)
    output_path = Path(output_path)
    errors = []

    addon_xml = source_dir / 'addon.xml'
    addon_xml_text = addon_xml.read_text(encoding='utf-8')
    expected_id, expected_version, _ = _parse_addon_identity(addon_xml_text)

    expected_filename = f'{expected_id}-{expected_version}.zip'
    actual_filename = output_path.name
    if actual_filename != expected_filename:
        errors.append(f'filename mismatch: expected {expected_filename}, got {actual_filename}')

    prefix = expected_id + '/'
    rooted_addon_xml = prefix + 'addon.xml'

    with zipfile.ZipFile(output_path, 'r') as zf:
        names = zf.namelist()
        sorted_names = sorted(names)
        if names != sorted_names:
            errors.append('members are not sorted; package is non-deterministic')

        for name in names:
            if not name.startswith(prefix):
                errors.append(f'member {name} is not under the {prefix} root')

        for name in names:
            for suffix in FORBIDDEN_PREFIXES:
                stripped = name[len(prefix):] if name.startswith(prefix) else name
                if stripped.startswith(suffix):
                    errors.append(f'forbidden member: {name}')
            basename = name.split('/')[-1]
            if basename in FORBIDDEN_EXACT:
                errors.append(f'forbidden member: {name}')

        if rooted_addon_xml in names:
            with zf.open(rooted_addon_xml) as f:
                pkg_xml_text = f.read().decode('utf-8')
            pkg_id, pkg_version, _ = _parse_addon_identity(pkg_xml_text)
            if pkg_id != expected_id:
                errors.append(f'embedded id mismatch: expected {expected_id}, got {pkg_id}')
            if pkg_version != expected_version:
                errors.append(f'embedded version mismatch: expected {expected_version}, got {pkg_version}')
        else:
            errors.append(f'{rooted_addon_xml} missing from package')

    manifest_errors = validate_manifest_references(output_path, source_dir)
    errors.extend(manifest_errors)

    return errors


def verify_checksum(output_path, expected_checksum):
    sha256 = hashlib.sha256()
    with open(output_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    actual = sha256.hexdigest()
    if actual != expected_checksum:
        return False, actual
    return True, actual


def _extract_local_references(addon_xml_text):
    """Return local file paths referenced by addon.xml manifest.

    Extracts library, icon, and fanart attributes/elements that point to
    local files. External URLs and addon dependency imports are excluded.
    """
    root = ET.fromstring(addon_xml_text)
    local_refs = []

    for ext in root.findall('.//extension'):
        library = ext.attrib.get('library', '')
        if library and not library.startswith(('http://', 'https://')):
            local_refs.append(library)

    for tag in ('icon', 'fanart'):
        for elem in root.iter(tag):
            text = (elem.text or '').strip()
            if text and not text.startswith(('http://', 'https://')):
                local_refs.append(text)

    return sorted(set(local_refs))


def validate_manifest_references(output_path, source_dir):
    """Parse addon.xml and reject each missing manifest-referenced local asset.

    The ZIP uses rooted topology where every member is prefixed with
    the addon ID directory. Local references from addon.xml are checked
    against the ZIP contents under that root.
    """
    source_dir = Path(source_dir)
    output_path = Path(output_path)
    errors = []

    addon_xml = source_dir / 'addon.xml'
    addon_xml_text = addon_xml.read_text(encoding='utf-8')
    expected_id, _, _ = _parse_addon_identity(addon_xml_text)

    local_refs = _extract_local_references(addon_xml_text)
    if not local_refs:
        return errors

    prefix = expected_id + '/'

    with zipfile.ZipFile(output_path, 'r') as zf:
        names = zf.namelist()
        for ref in local_refs:
            rooted_ref = prefix + ref
            if rooted_ref not in names:
                errors.append(
                    f'manifest-referenced local asset missing from package: {ref}'
                )

    return errors
