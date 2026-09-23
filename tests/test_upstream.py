import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apk_repository.config import ConfigError
from apk_repository.upstream import (
    download,
    select_asset,
    select_release,
    version_parts,
)


class UpstreamTests(unittest.TestCase):
    def release(self, tag, prerelease, published, draft=False):
        return {"id": int(published), "tag_name": tag, "prerelease": prerelease,
                "published_at": published, "draft": draft}

    def test_channels_are_strictly_separated(self):
        releases = [self.release("v1.2.0", False, "2"), self.release("v1.3.0-beta.1", True, "1"),
                    self.release("v1.4.0", False, "3", True)]
        self.assertEqual(select_release(releases, "stable")["tag_name"], "v1.2.0")
        self.assertEqual(select_release(releases, "prerelease")["tag_name"], "v1.3.0-beta.1")

    def test_only_latest_eligible_release_selected(self):
        releases = [self.release("v1.1.0", False, "1"), self.release("v1.2.0", False, "2")]
        self.assertEqual(select_release(releases, "stable")["tag_name"], "v1.2.0")

    def test_missing_prerelease_does_not_silently_select_stable(self):
        with self.assertRaises(ConfigError):
            select_release([self.release("v1.2.0", False, "1")], "prerelease")

    def test_apk_prerelease_version_mapping(self):
        self.assertEqual(version_parts("v1.14.0-beta.8"), ("1.14.0-beta.8", "1.14.0_beta8"))
        self.assertEqual(version_parts("1.14.0-rc.3"), ("1.14.0-rc.3", "1.14.0_rc3"))
        with self.assertRaises(ConfigError):
            version_parts("v1.2.3;echo bad")

    def asset_release(self):
        return {"tag_name": "v1.2.3", "assets": [{"id": 1, "name": "tool_1.2.3_openwrt_x86_64.apk",
            "digest": "sha256:" + "a" * 64, "size": 42,
            "browser_download_url": "https://github.com/owner/tool/releases/download/v1.2.3/tool_1.2.3_openwrt_x86_64.apk"}]}

    def test_only_exact_openwrt_asset_is_selected(self):
        release = self.asset_release()
        self.assertEqual(select_asset(release, "owner/tool", "tool_{version}_openwrt_x86_64.apk")["id"], 1)
        with self.assertRaises(ConfigError):
            select_asset(release, "owner/tool", "tool_{version}_linux_x86_64.apk")

    def test_digest_and_provenance_are_required(self):
        release = self.asset_release()
        release["assets"][0]["digest"] = None
        with self.assertRaises(ConfigError):
            select_asset(release, "owner/tool", "tool_{version}_openwrt_x86_64.apk")
        release = self.asset_release()
        release["assets"][0]["browser_download_url"] = "https://evil.example/tool.apk"
        with self.assertRaises(ConfigError):
            select_asset(release, "owner/tool", "tool_{version}_openwrt_x86_64.apk")

    def test_failed_download_never_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset.apk"
            path.write_bytes(b"original")
            with (
                patch("apk_repository.upstream.open_url", return_value=io.BytesIO(b"wrong")),
                self.assertRaises(ConfigError),
            ):
                download("https://example.test/file", path, hashlib.sha256(b"correct").hexdigest())
            self.assertEqual(path.read_bytes(), b"original")
            self.assertFalse(path.with_name("asset.apk.partial").exists())


if __name__ == "__main__":
    unittest.main()
