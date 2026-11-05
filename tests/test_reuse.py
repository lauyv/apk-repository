import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apk_repository.config import load
from apk_repository.reuse import fingerprint, previous_records, reuse_apk
from apk_repository.upstream import sha256

ROOT = Path(__file__).resolve().parents[1]


class ReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.site = Path(self.temp.name).resolve()
        self.repo, packages = load(ROOT)
        self.package = packages[0]
        self.target = self.repo["targets"][0]
        self.record = {"channel": "stable", "arch": self.target["arch"], "name": self.package["name"],
                       "version": "1.2.3-r0", "source": self.package["source"], "source_commit": "a" * 40,
                       "asset_id": 1, "asset_sha256": "b" * 64, "release_id": 2, "release_tag": "v1.2.3",
                       "upstream_prerelease": False, "file": "apk/stable/x86_64/package.apk",
                       "abi": self.target["abi"],
                       "reuse_fingerprint": fingerprint(self.repo, self.package, self.target)}
        self.path = self.site / self.record["file"]
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"signed APK fixture")
        self.old = dict(self.record, sha256=sha256(self.path))
        self.identity = (self.record["channel"], self.record["arch"], self.record["name"])
        self.destination = self.site / "fresh.apk"
        self.destination.write_bytes(b"fresh upstream APK")

    def reuse(self, old=None):
        return reuse_apk("apk", self.site, {self.identity: self.old if old is None else old},
                         self.record, self.destination, self.package, self.site / "keys")

    def test_compatible_apk_reused_after_signature_and_metadata_validation(self):
        with patch("apk_repository.reuse.validate_package") as validate:
            self.assertTrue(self.reuse())
        self.assertEqual(self.destination.read_bytes(), self.path.read_bytes())
        self.assertEqual(validate.call_args.args[-1], self.site / "keys")

    def test_asset_provenance_and_processing_changes_force_fresh_signing(self):
        for field in ("asset_sha256", "asset_id", "source_commit", "reuse_fingerprint", "abi", "version"):
            with self.subTest(field=field), patch("apk_repository.reuse.validate_package") as validate:
                old = dict(self.old, **{field: "changed"})
                self.assertFalse(self.reuse(old))
                validate.assert_not_called()
                self.assertEqual(self.destination.read_bytes(), b"fresh upstream APK")

    def test_old_manifest_without_fingerprint_is_not_reused(self):
        old = dict(self.old)
        del old["reuse_fingerprint"]
        self.assertFalse(self.reuse(old))

    def test_corrupt_or_missing_cached_apk_falls_back_without_replacing_input(self):
        self.path.write_bytes(b"corrupt")
        self.assertFalse(self.reuse())
        self.path.unlink()
        self.assertFalse(self.reuse())
        self.assertEqual(self.destination.read_bytes(), b"fresh upstream APK")

    def test_signature_failure_falls_back_without_replacing_input(self):
        with patch("apk_repository.reuse.validate_package", side_effect=subprocess.CalledProcessError(1, "apk")):
            self.assertFalse(self.reuse())
        self.assertEqual(self.destination.read_bytes(), b"fresh upstream APK")

    def test_interrupted_copy_preserves_original_and_cleans_partial_file(self):
        def interrupted_copy(source, destination):
            Path(destination).write_bytes(b"partial cached APK")
            raise OSError("interrupted copy")

        with patch("apk_repository.reuse.shutil.copyfile", side_effect=interrupted_copy):
            self.assertFalse(self.reuse())
        self.assertEqual(self.destination.read_bytes(), b"fresh upstream APK")
        self.assertEqual(list(self.site.glob("apk-reuse-*")), [])

    def test_failed_atomic_replace_preserves_original_and_cleans_staging(self):
        with (patch("apk_repository.reuse.validate_package"),
              patch("pathlib.Path.replace", side_effect=OSError("replace failed"))):
            self.assertFalse(self.reuse())
        self.assertEqual(self.destination.read_bytes(), b"fresh upstream APK")
        self.assertEqual(list(self.site.glob("apk-reuse-*")), [])

    def test_corruption_during_copy_is_rejected_before_replacing_original(self):
        def corrupt_copy(source, destination):
            Path(destination).write_bytes(b"corrupt copy")

        with (patch("apk_repository.reuse.shutil.copyfile", side_effect=corrupt_copy),
              patch("apk_repository.reuse.validate_package") as validate):
            self.assertFalse(self.reuse())
            validate.assert_not_called()
        self.assertEqual(self.destination.read_bytes(), b"fresh upstream APK")
        self.assertEqual(list(self.site.glob("apk-reuse-*")), [])

    def test_missing_previous_cache(self):
        self.assertEqual(previous_records(self.site / "missing", "public", self.repo), {})

    def test_package_config_and_tools_invalidate_fingerprint(self):
        expected = self.record["reuse_fingerprint"]
        package = copy.deepcopy(self.package)
        package["architectures"][self.target["arch"]]["asset_pattern"] = "changed-{version}.apk"
        self.assertNotEqual(fingerprint(self.repo, package, self.target), expected)
        repo = copy.deepcopy(self.repo)
        repo["apk_tools"]["version"] = "3.0.6"
        self.assertNotEqual(fingerprint(repo, self.package, self.target), expected)

    def test_processing_code_invalidates_fingerprint(self):
        with patch("apk_repository.reuse.sha256", return_value="c" * 64):
            self.assertNotEqual(fingerprint(self.repo, self.package, self.target),
                                self.record["reuse_fingerprint"])

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL required for real signature verification")
    def test_cached_manifest_authenticated_by_current_key(self):
        key = self.site / "test.key"
        public = self.site / "test.pub"

        def openssl(*args):
            subprocess.run(["openssl", *map(str, args)], check=True, capture_output=True)

        openssl("genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", key)
        openssl("pkey", "-in", key, "-pubout", "-out", public)
        manifest = self.site / "manifest.json"
        manifest.write_text(json.dumps({"schema_version": 1, "apk_tools": self.repo["apk_tools"],
                                        "base_url": self.repo["base_url"], "packages": [self.old]}))
        checksums = self.site / "SHA256SUMS"
        checksums.write_text(sha256(manifest) + "  manifest.json\n")
        openssl("dgst", "-sha256", "-sign", key, "-out", self.site / "SHA256SUMS.sig", checksums)
        self.assertEqual(previous_records(self.site, public, self.repo), {self.identity: self.old})
        # A key rotation invalidates the cache even if all stored bytes are intact.
        openssl("genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", key)
        openssl("pkey", "-in", key, "-pubout", "-out", public)
        self.assertEqual(previous_records(self.site, public, self.repo), {})
        # Re-sign the checksums, then tamper with the authenticated manifest.
        openssl("dgst", "-sha256", "-sign", key, "-out", self.site / "SHA256SUMS.sig", checksums)
        manifest.write_text(manifest.read_text() + " ")
        self.assertEqual(previous_records(self.site, public, self.repo), {})
