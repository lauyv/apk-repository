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
        for target in self.repo["targets"]:
            target["abi"] = None
        for pkg in self.packages:
            pkg["enabled"] = False
            pkg["license_reviewed"] = False
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

    def test_default_plan_never_includes_disabled_packages(self):
        self.assertEqual(plan(self.repo, self.packages, "stable")["jobs"], [])

    def test_candidate_plan_exposes_blockers_and_generic_feed_url(self):
        result = plan(self.repo, self.packages, "latest", True)
        self.assertTrue(result["planning_only"])
        self.assertEqual(len(result["jobs"]), sum(len(pkg["architectures"]) for pkg in self.packages))
        for job in result["jobs"]:
            self.assertIn("target-abi-required", job["blockers"])
            self.assertIn("license-review-required", job["blockers"])
            self.assertEqual(job["index_url"], f"https://lauyv.github.io/apk-repository/apk/latest/{job['arch']}/packages.adb")

    def test_new_package_needs_no_planner_changes(self):
        pkg = copy.deepcopy(self.packages[1])
        pkg.update(name="example-tool", source="example/tool", enabled=True, license_reviewed=True,
                   channels=["stable", "latest"])
        pkg["architectures"] = {"x86_64": pkg["architectures"]["x86_64"]}
        pkg["architectures"]["x86_64"].update(asset_pattern="tool-{version}-x86_64.apk")
        self.repo["targets"] = [{"arch": "x86_64", "abi": "test-abi"}]
        for configured in self.packages:
            configured["architectures"] = {"x86_64": configured["architectures"]["x86_64"]}
        self.repo["packages"].append(pkg["name"])
        self.packages.append(pkg)
        self.save()
        repo, packages = load(self.root)
        jobs = plan(repo, packages, "stable")["jobs"]
        self.assertEqual([j["package"] for j in jobs], ["example-tool"])
        self.assertEqual(jobs[0]["blockers"], [])
        self.assertEqual(len(plan(repo, packages, "latest")["jobs"]), 1)

    def test_empty_channel_architecture_feed_is_rejected(self):
        self.packages[0].update(enabled=True, license_reviewed=True, channels=["stable"])
        for target in self.repo["targets"]:
            target["abi"] = "openwrt-25.12"
        self.reject("empty feed: latest/")

    def test_path_traversal(self):
        self.repo["packages"] = ["../outside"]
        self.reject("invalid package name")

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

    def test_enabling_unreviewed_package_rejected(self):
        self.packages[1]["enabled"] = True
        self.reject("license review required")

    def test_enabling_without_asset_pattern_rejected(self):
        self.packages[1].update(enabled=True, license_reviewed=True)
        self.packages[1]["architectures"]["x86_64"]["asset_pattern"] = None
        self.reject("asset pattern required")

    def test_enabling_without_abi_rejected(self):
        self.packages[1].update(enabled=True, license_reviewed=True)
        self.reject("target ABI required")

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
