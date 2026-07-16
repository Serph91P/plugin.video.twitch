"""Tests for runtime-only packaging with explicit allowlist.

Verifies that the release ZIP contains exactly the runtime members,
no forbidden source-only content, correct identity, deterministic
ordering, and valid checksum.
"""

import hashlib
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.build_package import (
    FORBIDDEN_EXACT,
    FORBIDDEN_PREFIXES,
    RUNTIME_ENTRIES,
    _is_runtime_member,
    _normalize_member,
    _parse_addon_identity,
    build_package,
    collect_runtime_members,
    validate_package,
    verify_checksum,
)


FORBIDDEN_MEMBERS = [
    '.github/workflows/addon-validations.yml',
    '.github/workflows/make-release.yml',
    '.github/workflows/notify-repository.yml',
    '.github/workflows/deploy-pages.yml',
    '.patches/matrix.patch',
    'docs/index.html',
    'tests/test_issue_25_history_storage.py',
    'LICENSES/GPL-3.0-only',
    'README.md',
    'CONTRIBUTORS.md',
    '.gitignore',
    'tools/build_package.py',
]


class RuntimeAllowlistTests(unittest.TestCase):
    def test_forbidden_members_are_rejected(self):
        for member in FORBIDDEN_MEMBERS:
            with self.subTest(member=member):
                self.assertFalse(
                    _is_runtime_member(member),
                    f'{member} should be forbidden',
                )

    def test_addon_xml_is_runtime(self):
        self.assertTrue(_is_runtime_member('addon.xml'))

    def test_changelog_is_runtime(self):
        self.assertTrue(_is_runtime_member('changelog.txt'))

    def test_resources_are_runtime(self):
        self.assertTrue(_is_runtime_member('resources/lib/__init__.py'))
        self.assertTrue(_is_runtime_member('resources/settings.xml'))
        self.assertTrue(_is_runtime_member('resources/media/icon.png'))

    def test_pyc_files_are_rejected(self):
        self.assertFalse(_is_runtime_member('resources/lib/__pycache__/foo.pyc'))
        self.assertFalse(_is_runtime_member('resources/lib/foo.pyc'))


class CollectMembersTests(unittest.TestCase):
    def test_collect_from_real_repo(self):
        members = collect_runtime_members(ROOT)
        self.assertIn('addon.xml', members)
        self.assertIn('changelog.txt', members)
        self.assertTrue(any(m.startswith('resources/') for m in members))
        self.assertTrue(len(members) > 50)

    def test_no_forbidden_members(self):
        members = collect_runtime_members(ROOT)
        for member in members:
            for prefix in FORBIDDEN_PREFIXES:
                self.assertFalse(
                    member.startswith(prefix),
                    f'{member} has forbidden prefix {prefix}',
                )
            basename = member.split('/')[-1]
            self.assertNotIn(basename, FORBIDDEN_EXACT)


class ParseAddonIdentityTests(unittest.TestCase):
    def test_parse_valid_addon_xml(self):
        xml_text = '<addon id="plugin.video.twitch" version="3.1.8" name="Twitch"/>'
        aid, ver, name = _parse_addon_identity(xml_text)
        self.assertEqual(aid, 'plugin.video.twitch')
        self.assertEqual(ver, '3.1.8')
        self.assertEqual(name, 'Twitch')


class BuildPackageTests(unittest.TestCase):
    def _expected_output(self, tmpdir):
        return Path(tmpdir) / 'plugin.video.twitch-3.1.8.zip'

    def test_build_and_validate_package(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = self._expected_output(tmpdir)
            info = build_package(ROOT, output)
            self.assertEqual(info['addon_id'], 'plugin.video.twitch')
            self.assertEqual(info['addon_version'], '3.1.8')
            self.assertTrue(info['member_count'] > 50)
            self.assertTrue(output.exists())

            errors = validate_package(output, ROOT)
            self.assertEqual(errors, [], f'validation errors: {errors}')

    def test_package_filename_matches_identity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = self._expected_output(tmpdir)
            info = build_package(ROOT, output)
            self.assertEqual(info['filename'], 'plugin.video.twitch-3.1.8.zip')

    def test_package_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out1 = Path(tmpdir) / 'a.zip'
            out2 = Path(tmpdir) / 'b.zip'
            build_package(ROOT, out1)
            build_package(ROOT, out2)
            self.assertEqual(
                hashlib.sha256(out1.read_bytes()).hexdigest(),
                hashlib.sha256(out2.read_bytes()).hexdigest(),
            )

    def test_checksum_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'out.zip'
            info = build_package(ROOT, output)
            ok, actual = verify_checksum(output, info['checksum'])
            self.assertTrue(ok)
            self.assertEqual(actual, info['checksum'])

    def test_checksum_mismatch_detected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'out.zip'
            info = build_package(ROOT, output)
            ok, _ = verify_checksum(output, 'bad_checksum')
            self.assertFalse(ok)

    def test_no_forbidden_members_in_zip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'out.zip'
            build_package(ROOT, output)
            with zipfile.ZipFile(output) as zf:
                for name in zf.namelist():
                    for prefix in FORBIDDEN_PREFIXES:
                        self.assertFalse(
                            name.startswith(prefix),
                            f'{name} has forbidden prefix',
                        )
                    basename = name.split('/')[-1]
                    self.assertNotIn(basename, FORBIDDEN_EXACT)

    def test_missing_addon_xml_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(FileNotFoundError):
                build_package(Path(tmpdir), Path(tmpdir) / 'out.zip')

    def test_members_are_sorted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'out.zip'
            build_package(ROOT, output)
            with zipfile.ZipFile(output) as zf:
                names = zf.namelist()
                self.assertEqual(names, sorted(names))

    def test_embedded_identity_matches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'out.zip'
            build_package(ROOT, output)
            with zipfile.ZipFile(output) as zf:
                with zf.open('addon.xml') as f:
                    text = f.read().decode('utf-8')
                aid, ver, _ = _parse_addon_identity(text)
                self.assertEqual(aid, 'plugin.video.twitch')
                self.assertEqual(ver, '3.1.8')

    def test_validate_package_rejects_wrong_filename(self):
        """validate_package rejects dist/addon.zip because the identity filename is plugin.video.twitch-3.1.8.zip."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'addon.zip'
            build_package(ROOT, output)
            errors = validate_package(output, ROOT)
            self.assertTrue(len(errors) > 0, 'expected filename mismatch error')
            self.assertIn('filename mismatch', errors[0])


if __name__ == '__main__':
    unittest.main()
