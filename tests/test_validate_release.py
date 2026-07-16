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


def _make_artifact(path, addon_id='plugin.video.twitch'):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f'{addon_id}/addon.xml',
                     '<addon id="plugin.video.twitch" version="3.1.8" name="Twitch"/>')
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


if __name__ == '__main__':
    unittest.main()
