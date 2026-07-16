"""Validate immutable release contract.

Binds the exact successful validation run and candidate commit to
artifact SHA-256, add-on ID, version, asset name, tag identity, and
downstream publication identity. The release workflow must download
and consume verified validation evidence and the exact validated
artifact, not rebuild it.
"""

import hashlib
import json
from pathlib import Path


def write_validation_evidence(path, evidence):
    """Write validation evidence as JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')


def load_validation_evidence(path):
    """Load and return validation evidence from JSON."""
    path = Path(path)
    return json.loads(path.read_text(encoding='utf-8'))


def _compute_file_sha256(path):
    sha256 = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    return sha256.hexdigest()


REQUIRED_EVIDENCE_KEYS = (
    'candidate_sha',
    'validation_run_id',
    'validation_head_sha',
    'addon_id',
    'addon_version',
    'asset_name',
    'artifact_sha256',
    'tag',
    'publication_id',
)


def validate_release_contract(evidence, artifact_path, expected_tag, expected_sha):
    """Verify the full immutable release contract.

    Checks:
    - All required evidence fields are present
    - candidate_sha matches the expected tag commit SHA
    - validation_head_sha equals candidate_sha (no branch movement)
    - artifact SHA-256 matches the evidence
    - tag version matches addon_version
    - asset_name matches addon_id and addon_version
    - artifact file exists
    """
    errors = []

    for key in REQUIRED_EVIDENCE_KEYS:
        if key not in evidence or not evidence[key]:
            errors.append(f'missing or empty evidence field: {key}')

    if errors:
        return errors

    if evidence['candidate_sha'] != expected_sha:
        errors.append(
            f'candidate SHA mismatch: evidence={evidence["candidate_sha"]}, '
            f'expected={expected_sha}'
        )

    if evidence['validation_head_sha'] != evidence['candidate_sha']:
        errors.append(
            f'validation head SHA differs from candidate SHA: '
            f'validation_head={evidence["validation_head_sha"]}, '
            f'candidate={evidence["candidate_sha"]}'
        )

    if expected_tag and evidence['tag'] != expected_tag:
        errors.append(
            f'tag mismatch: evidence={evidence["tag"]}, expected={expected_tag}'
        )

    version_from_tag = expected_tag.lstrip('v') if expected_tag else ''
    if version_from_tag and version_from_tag != evidence['addon_version']:
        errors.append(
            f'tag version mismatch: tag implies {version_from_tag}, '
            f'evidence version={evidence["addon_version"]}'
        )

    expected_asset = f'{evidence["addon_id"]}-{evidence["addon_version"]}.zip'
    if evidence['asset_name'] != expected_asset:
        errors.append(
            f'asset name mismatch: evidence={evidence["asset_name"]}, '
            f'expected={expected_asset}'
        )

    expected_pub = f'{evidence["addon_id"]}@{evidence["addon_version"]}'
    if evidence['publication_id'] != expected_pub:
        errors.append(
            f'publication ID mismatch: evidence={evidence["publication_id"]}, '
            f'expected={expected_pub}'
        )

    artifact_path = Path(artifact_path)
    if not artifact_path.exists():
        errors.append(f'artifact file not found: {artifact_path}')
    else:
        actual_sha = _compute_file_sha256(artifact_path)
        if actual_sha != evidence['artifact_sha256']:
            errors.append(
                f'artifact checksum mismatch: actual={actual_sha}, '
                f'evidence={evidence["artifact_sha256"]}'
            )

    return errors


def validate_publication_identity(addon_id, version, tag, expected_addon_id=None):
    """Verify the downstream publication identity is consistent.

    The tag must encode the version, and the addon_id must be valid.
    If expected_addon_id is given, addon_id must also match it.
    """
    errors = []

    if not addon_id or not version or not tag:
        errors.append('addon_id, version, and tag are all required')
        return errors

    version_from_tag = tag.lstrip('v')
    if version_from_tag != version:
        errors.append(
            f'tag-version mismatch: tag={tag} implies {version_from_tag}, '
            f'version={version}'
        )

    if not addon_id.startswith('plugin.'):
        errors.append(f'addon_id must start with "plugin.": {addon_id}')

    if expected_addon_id and addon_id != expected_addon_id:
        errors.append(
            f'addon_id mismatch: got {addon_id}, expected {expected_addon_id}'
        )

    return errors
