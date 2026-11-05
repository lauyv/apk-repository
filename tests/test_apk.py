import unittest
from pathlib import Path
from unittest.mock import patch

from apk_repository.apk import validate_package
from apk_repository.config import ConfigError, load

ROOT = Path(__file__).resolve().parents[1]


class PackageMetadataTests(unittest.TestCase):
    def setUp(self):
        _, packages = load(ROOT)
        self.package = next(pkg for pkg in packages if pkg["name"] == "luci-app-sing-box")

    def check(self, depends, **overrides):
        info = {"name": self.package["name"], "version": "0.5.0-r1", "arch": "noarch",
                "license": "MIT", "depends": depends}
        info.update(overrides)
        with (patch("apk_repository.apk.run"),
              patch("apk_repository.apk.metadata", return_value={"info": info})):
            return validate_package("apk", "fixture.apk", self.package, "0.5.0-r1", "noarch")

    def test_upstream_dependencies_are_preserved_without_comparison(self):
        for depends in ([], ["luci-base", "sing-box"], ["luci-base", "sing-box", "flock"],
                        ["sing-box>=1.14", "new-upstream-dependency"]):
            with self.subTest(depends=depends):
                self.assertEqual(self.check(depends)["depends"], depends)

    def test_upstream_package_policy_metadata_is_preserved(self):
        overrides = {"license": "upstream-license", "install-if": ["trigger"],
                     "replaces": ["other"], "provides": ["virtual-package"]}
        info = self.check(["flock", "!other-package"], **overrides)
        for field, expected in overrides.items():
            self.assertEqual(info[field], expected)

    def test_other_metadata_checks_remain_enforced(self):
        for field, value in (("name", "wrong-package"), ("version", "0.4.7-r1"),
                             ("arch", "x86_64")):
            with (self.subTest(field=field), self.assertRaises(ConfigError)):
                self.check(["flock"], **{field: value})
