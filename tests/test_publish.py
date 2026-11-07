import copy
import itertools
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apk_repository.config import ConfigError, load
from apk_repository.publish import (
    check_records,
    check_upstream_assets,
    child,
    render_site,
    sign,
    verify,
)
from apk_repository.upstream import package_version, sha256, version_parts

ROOT = Path(__file__).resolve().parents[1]


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.site = Path(self.temp.name).resolve()
        self.repo, self.packages = load(ROOT)
        self.manifest = {"schema_version": 1, "apk_tools": self.repo["apk_tools"],
                         "base_url": self.repo["base_url"], "packages": []}
        for pkg, target, channel in itertools.product(self.packages, self.repo["targets"], self.repo["channels"]):
            if channel not in pkg["channels"]:
                continue
            arch = target["arch"]
            tag = "v1.3.0-beta.1" if channel == "latest" else "v1.3.0"
            pattern = pkg["architectures"][arch]["asset_pattern"]
            revision = 5
            asset_name = pattern.replace("{version}", tag.removeprefix("v")).replace("{apk_version}", version_parts(tag)[1]).replace("{revision}", str(revision))
            observed = ("1.3.0_beta1-r0" if channel == "latest" else "1.3.0-r0") if pkg["name"] == "sing-box" else None
            version = package_version(tag, pattern, asset_name, observed)
            relative = "apk/{}/{}/{}-{}.apk".format(channel, arch, pkg["name"], version)
            path = self.site / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture: metadata mocked")
            self.manifest["packages"].append({"name": pkg["name"], "channel": channel, "arch": arch,
                "abi": "openwrt-25.12", "source": pkg["source"], "release_tag": tag,
                "upstream_prerelease": channel == "latest", "version": version, "file": relative,
                "asset_name": asset_name, "asset_sha256": sha256(path), "sha256": sha256(path),
                "source_commit": "a" * 40})

    def check(self):
        with patch("apk_repository.publish.validate_package"):
            return check_records(self.repo, self.packages, self.manifest, self.site, "apk")

    def test_complete_latest_only_set(self):
        self.assertEqual(len(self.check()), sum(
            len(pkg["architectures"]) * len(pkg["channels"]) for pkg in self.packages))

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL required")
    def test_sign_and_verify_current_site_without_bootstrap_or_old_cache(self):
        # Exercise the complete site pipeline with real checksum signatures.
        # APK commands are mocked because APK Tools run on Linux CI.
        (self.site / "manifest.json").write_text(json.dumps(self.manifest))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            key, output = directory / "test.key", directory / "public"
            subprocess.run(["openssl", "genpkey", "-algorithm", "EC", "-pkeyopt",
                            "ec_paramgen_curve:P-256", "-out", str(key)], check=True, capture_output=True)
            key.chmod(0o600)

            signed_paths = []

            def run(argv, **kwargs):
                if argv[0] != "apk":
                    return subprocess.run(list(map(str, argv)), check=True, text=True, **kwargs)
                path = Path(argv[-1])
                if argv[1] == "adbsign":
                    signed_paths.append(path)
                    path.write_bytes(path.read_bytes() + b"signature")
                stdout = path.read_text() if argv[1] == "adbdump" else ""
                return subprocess.CompletedProcess(argv, 0, stdout=stdout)

            def index(apk, feed, private_key):
                entries = [{"name": r["name"], "version": r["version"]}
                           for r in self.manifest["packages"] if r["arch"] == feed.name and r["channel"] == feed.parent.name]
                (feed / "packages.adb").write_text(json.dumps({"packages": entries}))

            with (patch("apk_repository.publish.check_tool"),
                  patch("apk_repository.publish.check_upstream_assets"),
                  patch("apk_repository.publish.validate_package"),
                  patch("apk_repository.publish.metadata", return_value={"info": {}}),
                  patch("apk_repository.publish.run", side_effect=run),
                  patch("apk_repository.publish.index", side_effect=index)):
                sign(ROOT, "apk", self.site, output, key)
                verify(ROOT, "apk", output)
                manifest = json.loads((output / "manifest.json").read_text())
                self.assertNotIn("bootstrap", manifest)
                self.assertEqual(len(list(output.rglob("*.apk"))), 14)
                self.assertFalse(list(output.rglob("*.tar.gz")))
                self.assertEqual(len(manifest["indexes"]), 4)
                # All fixture APKs have identical bytes: sign once, copy to all feeds.
                self.assertEqual(len(signed_paths), 1)
                (output / manifest["packages"][0]["file"]).write_bytes(b"tampered")
                with self.assertRaisesRegex(ConfigError, "checksum mismatch"):
                    verify(ROOT, "apk", output)

    def test_stable_manifest_rejects_prerelease(self):
        record = next(r for r in self.manifest["packages"] if r["channel"] == "stable")
        record["upstream_prerelease"] = True
        with self.assertRaisesRegex(ConfigError, "prerelease in stable"):
            self.check()

    def test_latest_accepts_a_stable_upstream_release(self):
        for record in self.manifest["packages"]:
            if record["channel"] == "latest":
                record["release_tag"] = "v1.2.3"
                pkg = next(pkg for pkg in self.packages if pkg["name"] == record["name"])
                pattern = pkg["architectures"][record["arch"]]["asset_pattern"]
                record["asset_name"] = pattern.replace("{version}", "1.2.3").replace("{apk_version}", "1.2.3").replace("{revision}", "4")
                observed = "1.2.3-r0" if pkg["name"] == "sing-box" else None
                record["version"] = package_version("v1.2.3", pattern, record["asset_name"], observed)
                record["upstream_prerelease"] = False
                record["file"] = "apk/{}/{}/{}-{}.apk".format(
                    record["channel"], record["arch"], record["name"], record["version"])
                path = self.site / record["file"]
                path.write_bytes(b"fixture: metadata mocked")
                record["asset_sha256"] = record["sha256"] = sha256(path)
        self.assertEqual(len(self.check()), sum(
            len(pkg["architectures"]) * len(pkg["channels"]) for pkg in self.packages))

    def test_old_or_duplicate_version_cannot_enter_feed(self):
        extra = copy.deepcopy(self.manifest["packages"][0])
        extra["version"] = "0.1.0-r0"
        self.manifest["packages"].append(extra)
        with self.assertRaisesRegex(ConfigError, "duplicate"):
            self.check()

    def test_missing_channel_package_blocks_publication(self):
        self.manifest["packages"].pop()
        with self.assertRaisesRegex(ConfigError, "missing"):
            self.check()

    def test_missing_architecture_blocks_publication(self):
        self.manifest["packages"] = [record for record in self.manifest["packages"] if record["arch"] == "x86_64"]
        with self.assertRaisesRegex(ConfigError, "missing"):
            self.check()

    def test_modified_upstream_apk_cannot_be_signed(self):
        record = self.manifest["packages"][0]
        (self.site / record["file"]).write_bytes(b"modified")
        with self.assertRaisesRegex(ConfigError, "digest mismatch"):
            self.check()

    def test_manifest_cannot_relabel_modified_bytes_as_upstream(self):
        record = self.manifest["packages"][0]
        (self.site / record["file"]).write_bytes(b"modified")
        record["sha256"] = sha256(self.site / record["file"])
        with self.assertRaisesRegex(ConfigError, "modified before signing"):
            self.check()

    def test_artifact_path_traversal_rejected(self):
        with self.assertRaises(ConfigError):
            child(self.site, "../outside")

    def test_revisioned_honk_release_passes_signer_record_checks(self):
        for record in self.manifest["packages"]:
            if record["name"] == "luci-app-honk":
                record["release_tag"] += "-r5"
        self.check()

    def test_signer_rechecks_asset_digest_independently(self):
        record = self.manifest["packages"][0]
        record.update(release_id=7, source_commit="a" * 40, asset_id=9)
        self.manifest["packages"] = [record]
        release = {"id": 7, "prerelease": record["upstream_prerelease"]}
        asset = {"id": 9, "name": record["asset_name"], "digest": "sha256:" + record["asset_sha256"]}
        with (patch("apk_repository.publish.resolve_release", return_value=(release, "a" * 40)) as resolved,
              patch("apk_repository.publish.select_asset", return_value=asset)):
            check_upstream_assets(self.packages, self.manifest, self.site)
            resolved.assert_called_once_with(record["source"], record["release_tag"], channel=record["channel"])
            (self.site / record["file"]).write_bytes(b"tampered APK")
            record["sha256"] = record["asset_sha256"] = sha256(self.site / record["file"])
            with self.assertRaisesRegex(ConfigError, "does not match GitHub"):
                check_upstream_assets(self.packages, self.manifest, self.site)


class RenderSiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.site = Path(self.temp.name).resolve()
        self.repo = {"channels": ["stable", "latest"], "base_url": "https://example.invalid",
                     "targets": [{"arch": "x86_64", "abi": "openwrt-25.12"},
                                 {"arch": "aarch64_generic", "abi": "openwrt-25.12"}]}
        self.manifest = {"key_sha256": "ab" * 32, "packages": [
            {"channel": "latest", "arch": "x86_64", "name": "sing-box", "version": "1.2.3-r0",
             "file": "apk/latest/x86_64/sing-box-1.2.3-r0.apk"},
        ]}
        render_site(self.repo, self.site, self.manifest)
        self.page = (self.site / "index.html").read_text()

    def test_packages_are_direct_links_grouped_by_architecture(self):
        self.assertLess(self.page.index("<h2>latest / x86_64</h2>"), self.page.index("sing-box-1.2.3-r0.apk"))
        self.assertIn('href="apk/latest/x86_64/sing-box-1.2.3-r0.apk">sing-box-1.2.3-r0.apk</a>', self.page)
        self.assertIn('href="apk/latest/x86_64/packages.adb"', self.page)
        self.assertNotIn("<details", self.page)
        self.assertNotIn("<script", self.page)
        self.assertIn('href="apk/stable/x86_64/packages.adb"', self.page)

    def test_empty_architecture_still_lists_feed(self):
        self.assertIn("<h2>latest / aarch64_generic</h2>", self.page)
        self.assertIn('href="apk/latest/aarch64_generic/packages.adb"', self.page)

    def test_filenames_and_links_are_escaped(self):
        self.manifest["packages"].append({
            "channel": "latest", "arch": "x86_64", "name": "<script>alert(1)</script>",
            "version": "1-r0", "file": 'apk/<img src=x>.apk" onmouseover="1'})
        render_site(self.repo, self.site, self.manifest)
        page = (self.site / "index.html").read_text()
        self.assertNotIn("<script>", page)
        self.assertNotIn("<img", page)
        self.assertIn("&lt;img src=x&gt;", page)
        self.assertIn("&quot;", page)


if __name__ == "__main__":
    unittest.main()
