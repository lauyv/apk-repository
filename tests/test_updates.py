import io
import itertools
import json
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from apk_repository.config import load
from apk_repository.updates import check_updates

ROOT = Path(__file__).resolve().parents[1]


class UpdateTests(unittest.TestCase):
    def sources(self, patterns, digest="a" * 64):
        return [{"id": i + 20, "name": p.replace("{tag}", "v1.0.0").replace("*", "v1.0.0"),
                 "digest": "sha256:" + digest} for i, p in enumerate(patterns)]

    def check(self, published=None, error=None, digest="a" * 64):
        def release(source, channel):
            return {"id": 1, "tag_name": "v1.0.0", "prerelease": False}, "b" * 40

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("apk_repository.updates.resolve_release", side_effect=release),
            patch("apk_repository.updates.select_asset", side_effect=lambda release, source, pattern: {
                "id": 2, "name": pattern.replace("{version}", "1.0.0").replace("{revision}", "4"),
                "digest": "sha256:" + digest}),
            patch("apk_repository.updates.select_source_assets",
                  side_effect=lambda release, source, patterns: self.sources(patterns, digest)),
            patch("apk_repository.updates.open_url", side_effect=error,
                  return_value=io.BytesIO(json.dumps(published).encode())),
        ):
            return check_updates(ROOT)

    def manifest(self):
        repo, packages = load(ROOT)
        records = []
        for channel, target, pkg in itertools.product(repo["channels"], repo["targets"], packages):
            arch = target["arch"]
            if pkg["enabled"] and channel in pkg["channels"] and arch in pkg["architectures"]:
                pattern = pkg["architectures"][arch]["asset_pattern"]
                records.append({"channel": channel, "arch": arch, "name": pkg["name"],
                    "source": pkg["source"],
                    "source_commit": "b" * 40, "release_id": 1, "release_tag": "v1.0.0",
                    "upstream_prerelease": False, "asset_id": 2,
                    "asset_name": pattern.replace("{version}", "1.0.0").replace("{revision}", "4"),
                    "asset_sha256": "a" * 64,
                    "source_assets": [{"asset_id": a["id"], "asset_name": a["name"],
                                       "sha256": a["digest"].split(":")[1]}
                                      for a in self.sources(pkg.get("source_assets", []))]})
        return {"schema_version": 1, "packages": records}

    def test_unchanged_skips_publication(self):
        self.assertFalse(self.check(self.manifest()))

    def test_replaced_asset_requires_publication(self):
        self.assertTrue(self.check(self.manifest(), digest="c" * 64))

    def test_renamed_asset_requires_publication(self):
        published = self.manifest()
        published["packages"][0]["asset_name"] = "old-name.apk"
        self.assertTrue(self.check(published))

    def test_missing_or_replaced_bundled_sources_require_publication(self):
        for field in ("source_assets", "sha256", "asset_id"):
            published = self.manifest()
            record = next(r for r in published["packages"] if r["name"] == "luci-app-honk")
            if field == "source_assets":
                del record[field]
            else:
                record["source_assets"][0][field] = "changed"
            self.assertTrue(self.check(published))

    def test_new_architecture_requires_publication(self):
        published = self.manifest()
        published["packages"] = [record for record in published["packages"] if record["arch"] == "x86_64"]
        self.assertTrue(self.check(published))

    def test_missing_site_requires_first_publication(self):
        self.assertTrue(self.check(error=urllib.error.HTTPError("https://example.com", 404, "missing", {}, None)))

    def test_network_failure_is_not_treated_as_missing_site(self):
        with self.assertRaises(urllib.error.URLError):
            self.check(error=urllib.error.URLError("offline"))

    def test_removed_package_requires_publication(self):
        published = self.manifest()
        published["packages"].append(dict(published["packages"][0], name="removed-package"))
        self.assertTrue(self.check(published))
