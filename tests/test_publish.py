import copy
import itertools
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apk_repository.config import ConfigError, load
from apk_repository.publish import check_records, child
from apk_repository.upstream import sha256

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
                version = ("1.3.0_beta1" if prerelease else "1.2.3") + "-r" + str(pkg["revision"])
                relative = "apk/{}/{}/{}-{}.apk".format(channel, arch, pkg["name"], version)
                path = self.site / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture: metadata mocked")
                source_rel = "sources/{}/source.tar.gz".format(pkg["name"])
                source = self.site / source_rel
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b"fixture source")
                self.manifest["packages"].append({"name": pkg["name"], "channel": channel, "arch": arch,
                    "abi": "openwrt-25.12", "source": pkg["source"], "release_tag": "v1.3.0-beta.1" if prerelease else "v1.2.3",
                    "upstream_prerelease": prerelease, "version": version, "file": relative,
                    "source_archive": source_rel, "source_sha256": sha256(source),
                    "asset_sha256": sha256(path), "sha256": sha256(path)})

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
                record["version"] = "1.2.3-r" + str(next(
                    pkg["revision"] for pkg in self.packages if pkg["name"] == record["name"]))
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


if __name__ == "__main__":
    unittest.main()
