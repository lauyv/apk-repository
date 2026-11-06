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
        for pkg, target in itertools.product(self.packages, self.repo["targets"]):
            arch = target["arch"]
            tag = "v1.3.0-beta.1"
            pattern = pkg["architectures"][arch]["asset_pattern"]
            revision = 5
            asset_name = pattern.replace("{version}", tag.removeprefix("v")).replace("{apk_version}", version_parts(tag)[1]).replace("{revision}", str(revision))
            observed = "1.3.0_beta1-r0" if pkg["name"] == "sing-box" else None
            version = package_version(tag, pattern, asset_name, observed)
            relative = "apk/{}/{}/{}-{}.apk".format("latest", arch, pkg["name"], version)
            path = self.site / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture: metadata mocked")
            source_rel = "sources/{}/source.tar.gz".format(pkg["name"])
            source = self.site / source_rel
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"fixture source")
            self.manifest["packages"].append({"name": pkg["name"], "channel": "latest", "arch": arch,
                "abi": "openwrt-25.12", "source": pkg["source"], "release_tag": tag,
                "upstream_prerelease": True, "version": version, "file": relative,
                "source_archive": source_rel, "source_sha256": sha256(source),
                "asset_name": asset_name, "asset_sha256": sha256(path), "sha256": sha256(path),
                "source_commit": "a" * 40, "source_assets": self.sources(pkg, tag)})

    def sources(self, pkg, tag):
        sources = []
        for i, pattern in enumerate(pkg.get("source_assets", [])):
            name = pattern.replace("{version}", version_parts(tag)[0]).replace("*", "v1.0.0")
            relative = f"sources/{pkg['name']}/{'a' * 40}/{name}"
            path = self.site / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"bundled source")
            sources.append({"asset_id": i + 20, "asset_name": name, "file": relative, "sha256": sha256(path)})
        return sources

    def check(self):
        with patch("apk_repository.publish.validate_package"):
            return check_records(self.repo, self.packages, self.manifest, self.site, "apk")

    def test_complete_latest_only_set(self):
        self.assertEqual(len(self.check()), sum(
            len(pkg["architectures"]) for pkg in self.packages))

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL required")
    def test_sign_and_verify_current_site_without_bootstrap_or_old_cache(self):
        # Exercise the complete site pipeline with real checksum signatures.
        # APK commands are mocked because APK Tools run on Linux CI.
        (self.site / "manifest.json").write_text(json.dumps(self.manifest))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            key, output = directory / "test.key", directory / "public"
            subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt",
                            "rsa_keygen_bits:2048", "-out", str(key)], check=True, capture_output=True)
            key.chmod(0o600)

            def run(argv, **kwargs):
                if argv[0] != "apk":
                    return subprocess.run(list(map(str, argv)), check=True, text=True, **kwargs)
                path = Path(argv[-1])
                if argv[1] == "adbsign":
                    path.write_bytes(path.read_bytes() + b"signature")
                stdout = path.read_text() if argv[1] == "adbdump" else ""
                return subprocess.CompletedProcess(argv, 0, stdout=stdout)

            def index(apk, feed, private_key):
                entries = [{"name": r["name"], "version": r["version"]}
                           for r in self.manifest["packages"] if r["arch"] == feed.name]
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
                self.assertEqual(len(list(output.rglob("*.apk"))), 8)
                self.assertEqual(len(manifest["indexes"]), 2)
                (output / manifest["packages"][0]["file"]).write_bytes(b"tampered")
                with self.assertRaisesRegex(ConfigError, "checksum mismatch"):
                    verify(ROOT, "apk", output)

    def test_latest_accepts_a_stable_upstream_release(self):
        for record in self.manifest["packages"]:
            if record["channel"] == "latest":
                record["release_tag"] = "v1.2.3"
                pkg = next(pkg for pkg in self.packages if pkg["name"] == record["name"])
                pattern = pkg["architectures"][record["arch"]]["asset_pattern"]
                record["asset_name"] = pattern.replace("{version}", "1.2.3").replace("{apk_version}", "1.2.3").replace("{revision}", "4")
                observed = "1.2.3-r0" if pkg["name"] == "sing-box" else None
                record["version"] = package_version("v1.2.3", pattern, record["asset_name"], observed)
                record["source_assets"] = self.sources(pkg, "v1.2.3")
                record["upstream_prerelease"] = False
                record["file"] = "apk/{}/{}/{}-{}.apk".format(
                    record["channel"], record["arch"], record["name"], record["version"])
                path = self.site / record["file"]
                path.write_bytes(b"fixture: metadata mocked")
                record["asset_sha256"] = record["sha256"] = sha256(path)
        self.assertEqual(len(self.check()), sum(
            len(pkg["architectures"]) for pkg in self.packages))

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

    def test_missing_or_modified_bundled_sources_block_publication(self):
        record = next(r for r in self.manifest["packages"] if r["name"] == "luci-app-honk")
        sources = record["source_assets"]
        record["source_assets"] = []
        with self.assertRaisesRegex(ConfigError, "source assets"):
            self.check()
        record["source_assets"] = sources
        (self.site / sources[0]["file"]).write_bytes(b"tampered")
        with self.assertRaisesRegex(ConfigError, "source asset digest"):
            self.check()

    def test_revisioned_honk_release_passes_signer_record_checks(self):
        for record in self.manifest["packages"]:
            if record["name"] == "luci-app-honk":
                record["release_tag"] += "-r5"
        self.check()

    def test_signer_rechecks_bundled_sources_independently(self):
        record = next(r for r in self.manifest["packages"] if r["name"] == "luci-app-honk")
        record.update(release_id=7, asset_id=9)
        manifest = dict(self.manifest, packages=[record])
        release = {"id": 7, "prerelease": True}
        asset = {"id": 9, "name": record["asset_name"], "digest": "sha256:" + record["asset_sha256"]}
        sources = [{"id": a["asset_id"], "name": a["asset_name"], "digest": "sha256:" + a["sha256"]}
                   for a in record["source_assets"]]
        with (patch("apk_repository.publish.resolve_release", return_value=(release, "a" * 40)),
              patch("apk_repository.publish.select_asset", return_value=asset),
              patch("apk_repository.publish.select_source_assets", return_value=sources)):
            check_upstream_assets(self.packages, manifest, self.site)
            source = record["source_assets"][0]
            (self.site / source["file"]).write_bytes(b"tampered")
            source["sha256"] = sha256(self.site / source["file"])
            with self.assertRaisesRegex(ConfigError, "source asset does not match GitHub"):
                check_upstream_assets(self.packages, manifest, self.site)

    def test_signer_rechecks_asset_digest_independently(self):
        record = self.manifest["packages"][0]
        record.update(release_id=7, source_commit="a" * 40, asset_id=9)
        self.manifest["packages"] = [record]
        release = {"id": 7, "prerelease": record["upstream_prerelease"]}
        asset = {"id": 9, "name": record["asset_name"], "digest": "sha256:" + record["asset_sha256"]}
        with (patch("apk_repository.publish.resolve_release", return_value=(release, "a" * 40)) as resolved,
              patch("apk_repository.publish.select_asset", return_value=asset)):
            check_upstream_assets(self.packages, self.manifest, self.site)
            resolved.assert_called_once_with(record["source"], record["release_tag"])
            (self.site / record["file"]).write_bytes(b"tampered APK")
            record["sha256"] = record["asset_sha256"] = sha256(self.site / record["file"])
            with self.assertRaisesRegex(ConfigError, "does not match GitHub"):
                check_upstream_assets(self.packages, self.manifest, self.site)


class RenderSiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.site = Path(self.temp.name).resolve()
        self.repo = {"base_url": "https://example.invalid",
                     "targets": [{"arch": "x86_64", "abi": "openwrt-25.12"},
                                 {"arch": "aarch64_generic", "abi": "openwrt-25.12"}]}
        self.manifest = {"key_sha256": "ab" * 32, "packages": [
            {"channel": "latest", "arch": "x86_64", "name": "sing-box", "version": "1.2.3-r0",
             "file": "apk/latest/x86_64/sing-box-1.2.3-r0.apk",
             "source_archive": "sources/sing-box/source.tar.gz"},
        ]}
        render_site(self.repo, self.site, self.manifest)
        self.page = (self.site / "index.html").read_text()

    def test_packages_are_direct_links_grouped_by_architecture(self):
        self.assertLess(self.page.index("<h2>x86_64</h2>"), self.page.index("sing-box-1.2.3-r0.apk"))
        self.assertIn('href="apk/latest/x86_64/sing-box-1.2.3-r0.apk">sing-box-1.2.3-r0.apk</a>', self.page)
        self.assertIn('href="apk/latest/x86_64/packages.adb"', self.page)
        self.assertNotIn("<details", self.page)
        self.assertNotIn("<script", self.page)
        self.assertNotIn("stable", self.page)

    def test_empty_architecture_still_lists_feed(self):
        self.assertIn("<h2>aarch64_generic</h2>", self.page)
        self.assertIn('href="apk/latest/aarch64_generic/packages.adb"', self.page)

    def test_bundled_sources_have_download_links(self):
        self.manifest["packages"][0]["source_assets"] = [
            {"asset_name": "honk-source-v1.0.0.tar.gz", "file": "sources/honk/core.tar.gz"},
            {"asset_name": "doona-source-v1.0.0.tar.gz", "file": "sources/honk/ui.tar.gz"}]
        render_site(self.repo, self.site, self.manifest)
        page = (self.site / "index.html").read_text()
        self.assertIn('href="sources/honk/core.tar.gz">honk-source-v1.0.0.tar.gz</a>', page)
        self.assertIn('href="sources/honk/ui.tar.gz">doona-source-v1.0.0.tar.gz</a>', page)

    def test_filenames_and_links_are_escaped(self):
        self.manifest["packages"].append({
            "channel": "latest", "arch": "x86_64", "name": "<script>alert(1)</script>",
            "version": "1-r0", "file": 'apk/<img src=x>.apk" onmouseover="1',
            "source_archive": "sources/a.tar.gz"})
        render_site(self.repo, self.site, self.manifest)
        page = (self.site / "index.html").read_text()
        self.assertNotIn("<script>", page)
        self.assertNotIn("<img", page)
        self.assertIn("&lt;img src=x&gt;", page)
        self.assertIn("&quot;", page)


if __name__ == "__main__":
    unittest.main()
