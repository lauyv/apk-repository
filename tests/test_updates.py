import io
import itertools
import json
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from apk_repository.updates import check_updates

ROOT = Path(__file__).resolve().parents[1]


class UpdateTests(unittest.TestCase):
    def check(self, published=None, error=None, digest="a" * 64):
        def release(source, channel, fallback=False):
            return {"id": 1, "tag_name": "v1.0.0", "prerelease": channel == "prerelease"}, "b" * 40

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("apk_repository.updates.resolve_release", side_effect=release),
            patch("apk_repository.updates.select_asset", return_value={"id": 2, "digest": "sha256:" + digest}),
            patch("apk_repository.updates.open_url", side_effect=error,
                  return_value=io.BytesIO(json.dumps(published).encode())),
        ):
            return check_updates(ROOT)

    def manifest(self):
        records = []
        for channel, arch in itertools.product(("stable", "prerelease"), ("x86_64", "aarch64_generic")):
            for name, owner, revision in (("sing-box", "SagerNet", 0), ("luci-app-sing-box", "lauyv", 1)):
                records.append({"channel": channel, "arch": arch, "name": name,
                    "version": f"1.0.0-r{revision}", "source": f"{owner}/{name}",
                    "source_commit": "b" * 40, "release_id": 1, "release_tag": "v1.0.0",
                    "upstream_prerelease": channel == "prerelease", "asset_id": 2,
                    "asset_sha256": "a" * 64})
        return {"schema_version": 1, "packages": records}

    def test_unchanged_skips_publication(self):
        self.assertFalse(self.check(self.manifest()))

    def test_replaced_asset_requires_publication(self):
        self.assertTrue(self.check(self.manifest(), digest="c" * 64))

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
