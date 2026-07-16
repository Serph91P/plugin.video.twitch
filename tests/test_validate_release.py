"""Tests for immutable release contract validation.

Verifies that the release workflow binds the exact successful
validation run and candidate commit to artifact SHA-256, add-on ID,
version, asset name, tag identity, and downstream publication identity.
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.validate_release import (
    load_validation_evidence,
    validate_publication_identity,
    validate_release_contract,
    write_validation_evidence,
)


def _make_evidence(**overrides):
    evidence = {
        'candidate_sha': 'abc123def456abc123def456abc123def456abc1',
        'validation_run_id': 'run-999',
        'validation_head_sha': 'abc123def456abc123def456abc123def456abc1',
        'addon_id': 'plugin.video.twitch',
        'addon_version': '3.1.8',
        'asset_name': 'plugin.video.twitch-3.1.8.zip',
        'artifact_sha256': 'a' * 64,
        'tag': 'v3.1.8',
        'publication_id': 'plugin.video.twitch@3.1.8',
    }
    evidence.update(overrides)
    return evidence


def _make_artifact(path, addon_id='plugin.video.twitch', addon_version='3.1.8'):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f'{addon_id}/addon.xml',
                     f'<addon id="{addon_id}" version="{addon_version}" name="Twitch"/>')
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    return sha256


class WriteLoadEvidenceTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'evidence.json'
            evidence = _make_evidence()
            write_validation_evidence(path, evidence)
            loaded = load_validation_evidence(path)
            self.assertEqual(loaded, evidence)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_validation_evidence('/nonexistent/evidence.json')

    def test_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'bad.json'
            path.write_text('not json')
            with self.assertRaises(json.JSONDecodeError):
                load_validation_evidence(path)


class ValidContractTests(unittest.TestCase):
    def test_valid_contract_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = Path(tmpdir) / 'plugin.video.twitch-3.1.8.zip'
            checksum = _make_artifact(artifact)
            evidence = _make_evidence(artifact_sha256=checksum)
            errors = validate_release_contract(
                evidence, artifact, expected_tag='v3.1.8',
                expected_sha=evidence['candidate_sha'])
            self.assertEqual(errors, [], f'errors: {errors}')


class StaleSourceTests(unittest.TestCase):
    def test_wrong_sha_fails(self):
        evidence = _make_evidence(candidate_sha='aaa')
        errors = validate_release_contract(
            evidence, Path('/dev/null'), expected_tag='v3.1.8',
            expected_sha='bbb')
        self.assertTrue(any('sha' in e.lower() for e in errors))


class BranchMovementTests(unittest.TestCase):
    def test_validation_head_mismatch_fails(self):
        evidence = _make_evidence(
            candidate_sha='aaa',
            validation_head_sha='bbb')
        errors = validate_release_contract(
            evidence, Path('/dev/null'), expected_tag='v3.1.8',
            expected_sha='aaa')
        self.assertTrue(any('validation' in e.lower() and 'head' in e.lower()
                           for e in errors))


class ChecksumMismatchTests(unittest.TestCase):
    def test_checksum_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = Path(tmpdir) / 'plugin.video.twitch-3.1.8.zip'
            _make_artifact(artifact)
            evidence = _make_evidence(artifact_sha256='bad' + '0' * 60)
            errors = validate_release_contract(
                evidence, artifact, expected_tag='v3.1.8',
                expected_sha=evidence['candidate_sha'])
            self.assertTrue(any('checksum' in e.lower() for e in errors))


class TagVersionMismatchTests(unittest.TestCase):
    def test_wrong_tag_fails(self):
        evidence = _make_evidence()
        errors = validate_release_contract(
            evidence, Path('/dev/null'), expected_tag='v3.1.9',
            expected_sha=evidence['candidate_sha'])
        self.assertTrue(any('tag' in e.lower() or 'version' in e.lower()
                           for e in errors))

    def test_dev_tag_with_matching_version_passes(self):
        """Dev tag v3.1.8-dev should pass when addon_version is 3.1.8."""
        evidence = _make_evidence(tag='v3.1.8-dev', addon_version='3.1.8')
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = Path(tmpdir) / 'plugin.video.twitch-3.1.8.zip'
            checksum = _make_artifact(artifact, addon_id='plugin.video.twitch')
            evidence['artifact_sha256'] = checksum
            errors = validate_release_contract(
                evidence, artifact, expected_tag='v3.1.8-dev',
                expected_sha=evidence['candidate_sha'])
            self.assertEqual(errors, [], f'dev tag should pass: {errors}')

    def test_dev_tag_with_mismatched_version_fails(self):
        """Dev tag v3.1.8-dev should fail when addon_version is 3.1.9."""
        evidence = _make_evidence(tag='v3.1.8-dev', addon_version='3.1.9')
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = Path(tmpdir) / 'plugin.video.twitch-3.1.9.zip'
            checksum = _make_artifact(artifact)
            evidence['artifact_sha256'] = checksum
            errors = validate_release_contract(
                evidence, artifact, expected_tag='v3.1.8-dev',
                expected_sha=evidence['candidate_sha'])
            self.assertTrue(any('version' in e.lower() for e in errors),
                           f'expected version mismatch error: {errors}')

    def test_malformed_dev_tag_rejected(self):
        """Dev tag v3.1.8-dev-extra should be rejected as malformed."""
        evidence = _make_evidence(tag='v3.1.8-dev-extra', addon_version='3.1.8')
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = Path(tmpdir) / 'plugin.video.twitch-3.1.8.zip'
            checksum = _make_artifact(artifact)
            evidence['artifact_sha256'] = checksum
            errors = validate_release_contract(
                evidence, artifact, expected_tag='v3.1.8-dev-extra',
                expected_sha=evidence['candidate_sha'])
            self.assertTrue(len(errors) > 0, f'malformed dev tag should fail: {errors}')

    def test_malformed_dev_tag_consistent_version_rejected(self):
        """Dev tag v3.1.8-dev-extra with matching addon_version 3.1.8-dev-extra
        should fail only due to malformed tag grammar, not version mismatch."""
        evidence = _make_evidence(
            tag='v3.1.8-dev-extra',
            addon_version='3.1.8-dev-extra',
            asset_name='plugin.video.twitch-3.1.8-dev-extra.zip',
            publication_id='plugin.video.twitch@3.1.8-dev-extra',
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = Path(tmpdir) / 'plugin.video.twitch-3.1.8-dev-extra.zip'
            checksum = _make_artifact(artifact, addon_version='3.1.8-dev-extra')
            evidence['artifact_sha256'] = checksum
            errors = validate_release_contract(
                evidence, artifact, expected_tag='v3.1.8-dev-extra',
                expected_sha=evidence['candidate_sha'])
            self.assertTrue(len(errors) > 0, f'malformed dev tag should fail: {errors}')
            # Must fail due to malformed tag grammar, not version mismatch
            self.assertTrue(any('malformed tag' in e for e in errors),
                           f'expected malformed tag error, got: {errors}')
            self.assertFalse(any('version mismatch' in e.lower() for e in errors),
                           f'failed due to version mismatch, not grammar: {errors}')

    def test_dev_tag_mismatched_base_version_fails(self):
        """Dev tag v3.1.9-dev should fail when addon_version is 3.1.8."""
        evidence = _make_evidence(tag='v3.1.9-dev', addon_version='3.1.8')
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = Path(tmpdir) / 'plugin.video.twitch-3.1.8.zip'
            checksum = _make_artifact(artifact)
            evidence['artifact_sha256'] = checksum
            errors = validate_release_contract(
                evidence, artifact, expected_tag='v3.1.9-dev',
                expected_sha=evidence['candidate_sha'])
            self.assertTrue(any('version' in e.lower() for e in errors),
                           f'expected version mismatch error: {errors}')


class MissingInputTests(unittest.TestCase):
    def test_missing_artifact_fails(self):
        evidence = _make_evidence()
        errors = validate_release_contract(
            evidence, Path('/nonexistent/artifact.zip'),
            expected_tag='v3.1.8',
            expected_sha=evidence['candidate_sha'])
        self.assertTrue(len(errors) > 0)

    def test_missing_evidence_fields_fail(self):
        evidence = _make_evidence()
        for key in ('candidate_sha', 'addon_id', 'addon_version',
                     'artifact_sha256', 'tag'):
            incomplete = {k: v for k, v in evidence.items() if k != key}
            errors = validate_release_contract(
                incomplete, Path('/dev/null'),
                expected_tag='v3.1.8',
                expected_sha=incomplete.get('candidate_sha', ''))
            self.assertTrue(len(errors) > 0,
                            f'missing {key} should cause failure')


class PublicationIdentityTests(unittest.TestCase):
    def test_valid_publication_passes(self):
        errors = validate_publication_identity(
            addon_id='plugin.video.twitch',
            version='3.1.8',
            tag='v3.1.8')
        self.assertEqual(errors, [])

    def test_version_tag_mismatch_fails(self):
        errors = validate_publication_identity(
            addon_id='plugin.video.twitch',
            version='3.1.8',
            tag='v3.1.9')
        self.assertTrue(len(errors) > 0)

    def test_addon_id_mismatch_fails(self):
        errors = validate_publication_identity(
            addon_id='plugin.video.other',
            version='3.1.8',
            tag='v3.1.8',
            expected_addon_id='plugin.video.twitch')
        self.assertTrue(len(errors) > 0)

    def test_dev_tag_publication_passes(self):
        """Dev tag v3.1.8-dev should pass publication identity with version 3.1.8."""
        errors = validate_publication_identity(
            addon_id='plugin.video.twitch',
            version='3.1.8',
            tag='v3.1.8-dev')
        self.assertEqual(errors, [], f'dev tag publication should pass: {errors}')

    def test_dev_tag_publication_mismatch_fails(self):
        """Dev tag v3.1.9-dev should fail publication identity with version 3.1.8."""
        errors = validate_publication_identity(
            addon_id='plugin.video.twitch',
            version='3.1.8',
            tag='v3.1.9-dev')
        self.assertTrue(len(errors) > 0, f'dev tag mismatch should fail: {errors}')

    def test_malformed_dev_tag_publication_rejected(self):
        """Malformed dev tag v3.1.8-dev-extra should be rejected."""
        errors = validate_publication_identity(
            addon_id='plugin.video.twitch',
            version='3.1.8',
            tag='v3.1.8-dev-extra')
        self.assertTrue(len(errors) > 0, f'malformed dev tag should fail: {errors}')

    def test_malformed_stable_tag_extra_suffix_rejected(self):
        """Stable tag v3.1.8-extra should be rejected as malformed."""
        errors = validate_publication_identity(
            addon_id='plugin.video.twitch',
            version='3.1.8',
            tag='v3.1.8-extra')
        self.assertTrue(len(errors) > 0, f'malformed stable tag should fail: {errors}')
        self.assertTrue(any('malformed tag' in e for e in errors),
                       f'expected malformed tag error: {errors}')

    def test_malformed_tag_missing_patch_rejected(self):
        """Stable tag v3.1 should be rejected (missing patch segment)."""
        errors = validate_publication_identity(
            addon_id='plugin.video.twitch',
            version='3.1',
            tag='v3.1')
        self.assertTrue(len(errors) > 0, f'missing patch tag should fail: {errors}')
        self.assertTrue(any('malformed tag' in e for e in errors),
                       f'expected malformed tag error: {errors}')

    def test_malformed_tag_missing_minor_patch_rejected(self):
        """Stable tag v3 should be rejected (missing minor and patch)."""
        errors = validate_publication_identity(
            addon_id='plugin.video.twitch',
            version='3',
            tag='v3')
        self.assertTrue(len(errors) > 0, f'missing minor/patch tag should fail: {errors}')
        self.assertTrue(any('malformed tag' in e for e in errors),
                       f'expected malformed tag error: {errors}')

    def test_malformed_tag_whitespace_rejected(self):
        """Tag with whitespace should be rejected."""
        errors = validate_publication_identity(
            addon_id='plugin.video.twitch',
            version='3.1.8',
            tag='v3.1.8 ')
        self.assertTrue(len(errors) > 0, f'whitespace tag should fail: {errors}')
        self.assertTrue(any('malformed tag' in e for e in errors),
                       f'expected malformed tag error: {errors}')

    def test_malformed_tag_empty_version_rejected(self):
        """Tag v with no version should be rejected."""
        errors = validate_publication_identity(
            addon_id='plugin.video.twitch',
            version='3.1.8',
            tag='v')
        self.assertTrue(len(errors) > 0, f'empty version tag should fail: {errors}')
        self.assertTrue(any('malformed tag' in e for e in errors),
                       f'expected malformed tag error: {errors}')

    def test_dev_tag_with_double_dev_rejected(self):
        """Dev tag v3.1.8-dev-dev should be rejected."""
        errors = validate_publication_identity(
            addon_id='plugin.video.twitch',
            version='3.1.8',
            tag='v3.1.8-dev-dev')
        self.assertTrue(len(errors) > 0, f'double-dev tag should fail: {errors}')
        self.assertTrue(any('malformed tag' in e for e in errors),
                       f'expected malformed tag error: {errors}')

    def test_stable_tag_publication_passes(self):
        """Stable tag v3.1.8 should pass publication identity with version 3.1.8."""
        errors = validate_publication_identity(
            addon_id='plugin.video.twitch',
            version='3.1.8',
            tag='v3.1.8')
        self.assertEqual(errors, [], f'stable tag publication should pass: {errors}')

    def test_dev_tag_publication_passes(self):
        """Dev tag v3.1.8-dev should pass publication identity with version 3.1.8."""
        errors = validate_publication_identity(
            addon_id='plugin.video.twitch',
            version='3.1.8',
            tag='v3.1.8-dev')
        self.assertEqual(errors, [], f'dev tag publication should pass: {errors}')


if __name__ == '__main__':
    unittest.main()
