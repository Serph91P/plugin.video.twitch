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
    validate_manifest_references,
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
        return Path(tmpdir) / 'plugin.video.twitch-3.1.11.zip'

    def test_build_and_validate_package(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = self._expected_output(tmpdir)
            info = build_package(ROOT, output)
            self.assertEqual(info['addon_id'], 'plugin.video.twitch')
            self.assertEqual(info['addon_version'], '3.1.11')
            self.assertTrue(info['member_count'] > 50)
            self.assertTrue(output.exists())

            errors = validate_package(output, ROOT)
            self.assertEqual(errors, [], f'validation errors: {errors}')

    def test_package_filename_matches_identity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = self._expected_output(tmpdir)
            info = build_package(ROOT, output)
            self.assertEqual(info['filename'], 'plugin.video.twitch-3.1.11.zip')

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
                with zf.open('plugin.video.twitch/addon.xml') as f:
                    text = f.read().decode('utf-8')
                aid, ver, _ = _parse_addon_identity(text)
                self.assertEqual(aid, 'plugin.video.twitch')
                self.assertEqual(ver, '3.1.11')

    def test_validate_package_rejects_wrong_filename(self):
        """validate_package rejects dist/addon.zip because the identity filename is plugin.video.twitch-3.1.11.zip."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'addon.zip'
            build_package(ROOT, output)
            errors = validate_package(output, ROOT)
            self.assertTrue(len(errors) > 0, 'expected filename mismatch error')
            self.assertIn('filename mismatch', errors[0])


class RootedTopologyTests(unittest.TestCase):
    """Every ZIP member must live under the single plugin.video.twitch/ top-level directory."""

    def test_all_members_prefixed_with_addon_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'out.zip'
            build_package(ROOT, output)
            with zipfile.ZipFile(output) as zf:
                for name in zf.namelist():
                    self.assertTrue(
                        name.startswith('plugin.video.twitch/'),
                        f'{name} is not under plugin.video.twitch/ prefix',
                    )

    def test_top_level_directory_count_is_exactly_one(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'out.zip'
            build_package(ROOT, output)
            with zipfile.ZipFile(output) as zf:
                top_dirs = set()
                for name in zf.namelist():
                    parts = name.split('/')
                    if len(parts) > 1:
                        top_dirs.add(parts[0])
                self.assertEqual(
                    top_dirs,
                    {'plugin.video.twitch'},
                    f'expected exactly one top-level dir, got {top_dirs}',
                )


class CompressionTests(unittest.TestCase):
    """ZIP members must be compressed (ZIP_DEFLATED), not stored flat."""

    def test_zip_uses_compression(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'out.zip'
            build_package(ROOT, output)
            with zipfile.ZipFile(output) as zf:
                for info in zf.infolist():
                    self.assertIn(
                        info.compress_type,
                        (zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA),
                        f'{info.filename} is not compressed (compress_type={info.compress_type})',
                    )

    def test_compressed_is_smaller_than_stored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            compressed = Path(tmpdir) / 'compressed.zip'
            stored = Path(tmpdir) / 'stored.zip'
            build_package(ROOT, compressed)
            from tools.build_package import collect_runtime_members, _parse_addon_identity
            addon_xml_text = (ROOT / 'addon.xml').read_text(encoding='utf-8')
            addon_id, _, _ = _parse_addon_identity(addon_xml_text)
            members = collect_runtime_members(ROOT)
            prefix = addon_id + '/'
            with zipfile.ZipFile(stored, 'w', zipfile.ZIP_STORED) as zf:
                for member in members:
                    full = ROOT / member
                    data = full.read_bytes()
                    info = zipfile.ZipInfo(prefix + member)
                    info.date_time = (1980, 1, 1, 0, 0, 0)
                    info.compress_type = zipfile.ZIP_STORED
                    zf.writestr(info, data)
            self.assertLess(
                compressed.stat().st_size,
                stored.stat().st_size,
                'compressed ZIP should be smaller than stored ZIP',
            )


class SizeRegressionTests(unittest.TestCase):
    """Defensible size regression threshold, not hard-coded byte identity."""

    MAX_SIZE_KB = 2500

    def test_package_size_within_bound(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'out.zip'
            build_package(ROOT, output)
            size_kb = output.stat().st_size / 1024
            self.assertLess(
                size_kb,
                self.MAX_SIZE_KB,
                f'package size {size_kb:.1f} KB exceeds {self.MAX_SIZE_KB} KB bound',
            )

    def test_compressed_members_have_reduced_size(self):
        """At least one member must be meaningfully smaller when compressed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'out.zip'
            build_package(ROOT, output)
            with zipfile.ZipFile(output) as zf:
                compressed_savings = 0
                for info in zf.infolist():
                    if info.compress_type != zipfile.ZIP_STORED:
                        if info.file_size > 0:
                            ratio = 1.0 - (info.compress_size / info.file_size)
                            if ratio > 0.1:
                                compressed_savings += 1
                self.assertGreater(
                    compressed_savings, 0,
                    'no members showed meaningful compression savings',
                )


class ManifestReferenceTests(unittest.TestCase):
    """Parse addon.xml and reject each missing manifest-referenced local asset."""

    def test_valid_package_passes_manifest_check(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'out.zip'
            build_package(ROOT, output)
            errors = validate_manifest_references(output, ROOT)
            self.assertEqual(errors, [], f'manifest errors: {errors}')

    def test_missing_local_library_detected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'out.zip'
            build_package(ROOT, output)
            import zipfile as _zf
            with _zf.ZipFile(output, 'r') as zf:
                names = [n for n in zf.namelist()
                         if not n.endswith('addon_runner.py')]
                with _zf.ZipFile(Path(tmpdir) / 'modified.zip', 'w',
                                  _zf.ZIP_DEFLATED) as out_zf:
                    for name in names:
                        out_zf.writestr(name, zf.read(name))
            errors = validate_manifest_references(
                Path(tmpdir) / 'modified.zip', ROOT)
            self.assertTrue(len(errors) > 0, 'expected error for missing local library')
            self.assertTrue(any('addon_runner.py' in e for e in errors))

    def test_missing_icon_detected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'out.zip'
            build_package(ROOT, output)
            import zipfile as _zf
            with _zf.ZipFile(output, 'r') as zf:
                names = [n for n in zf.namelist()
                         if 'icon.png' not in n]
                with _zf.ZipFile(Path(tmpdir) / 'modified.zip', 'w',
                                  _zf.ZIP_DEFLATED) as out_zf:
                    for name in names:
                        out_zf.writestr(name, zf.read(name))
            errors = validate_manifest_references(
                Path(tmpdir) / 'modified.zip', ROOT)
            self.assertTrue(len(errors) > 0, 'expected error for missing icon')
            self.assertTrue(any('icon.png' in e for e in errors))

    def test_external_resources_are_allowed(self):
        """Addon dependency imports are external and must not cause errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'out.zip'
            build_package(ROOT, output)
            errors = validate_manifest_references(output, ROOT)
            self.assertEqual(errors, [],
                             f'external references should be allowed: {errors}')

    def test_manifest_references_under_root(self):
        """All local references must resolve under the rooted ZIP topology."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'out.zip'
            build_package(ROOT, output)
            errors = validate_manifest_references(output, ROOT)
            self.assertEqual(errors, [])

    def test_embedded_manifest_missing_reference_rejected(self):
        """Embedded addon.xml referencing a missing local module must be rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'plugin.video.twitch-3.1.11.zip'
            build_package(ROOT, output)
            import zipfile as _zf
            # Read all data from original zip first
            with _zf.ZipFile(output, 'r') as zf:
                names = list(zf.namelist())
                # Read all files into memory
                file_data = {name: zf.read(name) for name in names}
                # Find and read the original addon.xml
                original_addon_xml = None
                for name in names:
                    if name.endswith('addon.xml'):
                        original_addon_xml = file_data[name].decode('utf-8')
                        break
            # Now create the modified zip (overwrite the original)
            with _zf.ZipFile(output, 'w',
                              _zf.ZIP_DEFLATED) as out_zf:
                for name in names:
                    if name.endswith('addon.xml'):
                        # Add a reference to a missing module
                        modified_xml = original_addon_xml.replace(
                            '<extension point="xbmc.service" library="resources/lib/service_runner.py"/>',
                            '<extension point="xbmc.service" library="resources/lib/service_runner.py"/>\n'
                            '    <extension point="xbmc.python.pluginsource" library="resources/lib/missing_module.py">\n'
                            '        <provides>video</provides>\n'
                            '    </extension>'
                        )
                        out_zf.writestr(name, modified_xml.encode('utf-8'))
                    else:
                        out_zf.writestr(name, file_data[name])
            # validate_package should reject this because the embedded manifest
            # references a missing local module
            errors = validate_package(output, ROOT)
            self.assertTrue(len(errors) > 0, 'expected error for missing embedded manifest reference')
            self.assertTrue(any('missing_module.py' in e for e in errors),
                           f'expected missing_module.py reference error: {errors}')


if __name__ == '__main__':
    unittest.main()
