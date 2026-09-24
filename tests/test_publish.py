import copy
import itertools
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
)
from apk_repository.upstream import package_version, sha256

ROOT = Path(__file__).resolve().parents[1]


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.site = Path(self.temp.name).resolve()
        self.repo, self.packages = load(ROOT)
        self.manifest = {"schema_version": 1, "apk_tools": self.repo["apk_tools"],
                         "base_url": self.repo["base_url"], "packages": []}
        for channel in self.repo["channels"]:
            prerelease = channel != "stable"
            for pkg, target in itertools.product(self.packages, self.repo["targets"]):
                arch = target["arch"]
                tag = "v1.3.0-beta.1" if prerelease else "v1.2.3"
                pattern = pkg["architectures"][arch]["asset_pattern"]
                revision = 5 if prerelease else 4
                asset_name = pattern.replace("{version}", tag.removeprefix("v")).replace("{revision}", str(revision))
                observed = ("1.3.0_beta1-r0" if prerelease else "1.2.3-r0") if pkg["name"] == "sing-box" else None
                version = package_version(tag, pattern, asset_name, pkg["revision"], observed)
                relative = "apk/{}/{}/{}-{}.apk".format(channel, arch, pkg["name"], version)
                path = self.site / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture: metadata mocked")
                source_rel = "sources/{}/source.tar.gz".format(pkg["name"])
                source = self.site / source_rel
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b"fixture source")
                self.manifest["packages"].append({"name": pkg["name"], "channel": channel, "arch": arch,
                    "abi": "openwrt-25.12", "source": pkg["source"], "release_tag": tag,
                    "upstream_prerelease": prerelease, "version": version, "file": relative,
                    "source_archive": source_rel, "source_sha256": sha256(source),
                    "asset_name": asset_name, "asset_sha256": sha256(path), "sha256": sha256(path)})

    def check(self):
        with patch("apk_repository.publish.validate_package"):
            return check_records(self.repo, self.packages, self.manifest, self.site, "apk")

    def test_complete_latest_only_set(self):
        self.assertEqual(len(self.check()), len(self.repo["channels"]) * sum(
            len(pkg["architectures"]) for pkg in self.packages))

    def test_latest_accepts_a_stable_upstream_release(self):
        for record in self.manifest["packages"]:
            if record["channel"] == "latest":
                record["release_tag"] = "v1.2.3"
                pkg = next(pkg for pkg in self.packages if pkg["name"] == record["name"])
                pattern = pkg["architectures"][record["arch"]]["asset_pattern"]
                record["asset_name"] = pattern.replace("{version}", "1.2.3").replace("{revision}", "4")
                observed = "1.2.3-r0" if pkg["name"] == "sing-box" else None
                record["version"] = package_version("v1.2.3", pattern, record["asset_name"], pkg["revision"], observed)
                record["upstream_prerelease"] = False
                record["file"] = "apk/{}/{}/{}-{}.apk".format(
                    record["channel"], record["arch"], record["name"], record["version"])
                path = self.site / record["file"]
                path.write_bytes(b"fixture: metadata mocked")
                record["asset_sha256"] = record["sha256"] = sha256(path)
        self.assertEqual(len(self.check()), len(self.repo["channels"]) * sum(
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

    def test_signer_rechecks_asset_digest_independently(self):
        record = self.manifest["packages"][0]
        record.update(release_id=7, source_commit="a" * 40, asset_id=9)
        self.manifest["packages"] = [record]
        release = {"id": 7, "prerelease": record["upstream_prerelease"]}
        asset = {"id": 9, "name": record["asset_name"], "digest": "sha256:" + record["asset_sha256"]}
        with (patch("apk_repository.publish.resolve_release", return_value=(release, "a" * 40)) as resolved,
              patch("apk_repository.publish.select_asset", return_value=asset)):
            check_upstream_assets(self.packages, self.manifest, self.site)
            resolved.assert_called_once_with(record["source"], record["channel"], record["release_tag"])
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
        self.manifest = {"key_sha256": "ab" * 32, "bootstrap": [], "packages": [
            {"channel": "stable", "arch": "x86_64", "name": "sing-box", "version": "1.2.3-r0",
             "file": "apk/stable/x86_64/sing-box-1.2.3-r0.apk",
             "source_archive": "sources/sing-box/source.tar.gz"},
            {"channel": "stable", "arch": "aarch64_generic", "name": "luci-app-sing-box", "version": "1.2.3-r0",
             "file": "apk/stable/aarch64_generic/luci-app-sing-box-1.2.3-r0.apk",
             "source_archive": "sources/luci-app-sing-box/source.tar.gz"},
        ]}
        render_site(self.repo, self.site, self.manifest)
        self.page = (self.site / "index.html").read_text()

    def test_packages_are_nested_under_channel_and_arch_folders(self):
        stable = self.page.index(">stable</span>")
        x86 = self.page.index(">x86_64</span>")
        latest = self.page.index(">latest</span>")
        package = self.page.index("sing-box-1.2.3-r0.apk")
        self.assertTrue(self.page.count("<details") == self.page.count("</details>") == 6)
        self.assertLess(stable, x86)
        self.assertLess(package, latest)
        self.assertIn('href="apk/stable/x86_64/packages.adb"', self.page)
        self.assertIn('href="apk/latest/aarch64_generic/manifest.json"', self.page)
        self.assertNotIn("<table", self.page)

    def test_empty_folder_is_still_listed_with_its_feed(self):
        self.assertIn("暂无软件包", self.page)
        self.assertIn('href="apk/latest/x86_64/packages.adb"', self.page)

    def test_folder_labels_and_links_are_escaped(self):
        self.manifest["packages"].append({
            "channel": "stable", "arch": "x86_64", "name": "<script>alert(1)</script>",
            "version": "1-r0", "file": 'apk/a.apk" onmouseover="1',
            "source_archive": "sources/a.tar.gz"})
        render_site(self.repo, self.site, self.manifest)
        page = (self.site / "index.html").read_text()
        self.assertNotIn("<script>", page)
        self.assertNotIn("<img", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("&quot;", page)


if __name__ == "__main__":
    unittest.main()
