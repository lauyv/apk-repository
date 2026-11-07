import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from apk_repository.config import ConfigError, load, plan

ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo, self.packages = copy.deepcopy(load(ROOT))
        self.save()

    def save(self):
        (self.root / "repository.json").write_text(json.dumps(self.repo))
        for pkg in self.packages:
            directory = self.root / "packages" / pkg["name"]
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "package.json").write_text(json.dumps(pkg))

    def reject(self, message):
        self.save()
        with self.assertRaisesRegex(ConfigError, message):
            load(self.root)

    def test_plan_lists_all_current_packages_on_both_architectures(self):
        result = plan(self.repo, self.packages)
        self.assertEqual(result["channel"], "latest")
        self.assertEqual(len(result["jobs"]), 8)
        for job in result["jobs"]:
            self.assertEqual(job["index_url"], f"https://lauyv.github.io/apk-repository/apk/latest/{job['arch']}/packages.adb")

    def test_stable_plan_excludes_latest_only_honk(self):
        result = plan(self.repo, self.packages, "stable")
        self.assertEqual(len(result["jobs"]), 6)
        self.assertNotIn("luci-app-honk", {job["package"] for job in result["jobs"]})
        self.assertTrue(all("/apk/stable/" in job["index_url"] for job in result["jobs"]))

    def test_empty_architecture_feed_is_rejected(self):
        for pkg in self.packages:
            del pkg["architectures"]["aarch64_generic"]
        self.reject("empty feed: stable/aarch64_generic")

    def test_path_traversal(self):
        self.repo["packages"] = ["../outside"]
        self.reject("invalid package name")

    def test_unknown_channel_is_rejected(self):
        self.repo["channels"] = ["testing", "latest"]
        self.reject("both stable and latest")

    def test_asset_pattern_requires_exactly_one_version_placeholder(self):
        self.packages[0]["architectures"]["x86_64"]["asset_pattern"] = "tool-{version}-{apk_version}.apk"
        self.reject("asset pattern")

    def test_unknown_field(self):
        self.packages[0]["enabeld"] = True
        self.reject("unexpected or missing fields")

    def test_duplicate_json_keys(self):
        (self.root / "repository.json").write_text('{"name": "a", "name": "b"}')
        with self.assertRaisesRegex(ConfigError, "duplicate JSON key"):
            load(self.root)

    def test_unknown_architecture(self):
        self.packages[0]["architectures"]["aarch64_*"] = {}
        self.reject("unknown architecture")

    def test_wrong_architecture_rejected(self):
        self.packages[1]["architectures"]["x86_64"]["package_arch"] = "aarch64"
        self.reject("incompatible package architecture")

    def test_missing_asset_pattern_is_rejected(self):
        self.packages[1]["architectures"]["x86_64"]["asset_pattern"] = None
        self.reject("asset pattern")

    def test_missing_abi_is_rejected(self):
        self.repo["targets"][0]["abi"] = None
        self.reject("invalid target ABI")

    def test_multiple_abis_rejected(self):
        self.repo["targets"] = [{"arch": "x86_64", "abi": "a"}, {"arch": "aarch64_cortex_a53", "abi": "b"}]
        self.reject("multiple ABI")

    def test_asset_path_rejected(self):
        self.packages[1]["architectures"]["x86_64"]["asset_pattern"] = "../{version}.tar.gz"
        self.reject("asset pattern")

    def test_url_credentials_rejected(self):
        self.repo["base_url"] = "https://user:password@example.com/feed"
        self.reject("base_url")

    def test_cli_returns_failure_for_invalid_configuration(self):
        self.repo["packages"] = ["../invalid"]
        self.save()
        result = subprocess.run([sys.executable, "-m", "apk_repository", "--root", str(self.root), "validate"],
                                cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid package name", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
