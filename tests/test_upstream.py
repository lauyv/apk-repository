import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apk_repository.config import ConfigError
from apk_repository.upstream import (
    api_json,
    download,
    resolve_release,
    resolve_tag_commit,
    select_asset,
    select_release,
    version_parts,
)


class UpstreamTests(unittest.TestCase):
    def release(self, tag, prerelease, published, draft=False):
        return {"id": int(published), "tag_name": tag, "prerelease": prerelease,
                "published_at": published, "draft": draft}

    def test_stable_excludes_prereleases(self):
        releases = [self.release("v1.2.0", False, "2"), self.release("v1.3.0-beta.1", True, "1"),
                    self.release("v1.4.0", False, "3", True)]
        self.assertEqual(select_release(releases, "stable")["tag_name"], "v1.2.0")

    def test_only_latest_eligible_release_selected(self):
        releases = [self.release("v1.1.0", False, "1"), self.release("v1.2.0", False, "2")]
        self.assertEqual(select_release(releases, "stable")["tag_name"], "v1.2.0")

    def test_latest_ignores_release_channel_and_drafts(self):
        releases = [self.release("v1.2.0", False, "2"), self.release("v1.3.0-beta.1", True, "3"),
                    self.release("v1.4.0", False, "4", True)]
        self.assertEqual(select_release(releases, "latest")["tag_name"], "v1.3.0-beta.1")
        releases.append(self.release("v1.3.0", False, "5"))
        self.assertEqual(select_release(releases, "latest")["tag_name"], "v1.3.0")

    def test_latest_scans_all_release_pages(self):
        older = self.release("v1.2.0", False, "2")
        newer = self.release("v1.3.0-beta.1", True, "3")
        with patch("apk_repository.upstream.api_json", side_effect=[
            [older] * 10, [newer], {"object": {"type": "commit", "sha": "a" * 40}},
        ]) as api:
            release, commit = resolve_release("owner/tool", "latest")
        self.assertEqual((release["tag_name"], commit), ("v1.3.0-beta.1", "a" * 40))
        self.assertEqual(api.call_args_list[1].args[0], "repos/owner/tool/releases?per_page=10&page=2")

    def test_annotated_tag_resolves_to_commit(self):
        with patch("apk_repository.upstream.api_json", side_effect=[
            {"object": {"type": "tag", "sha": "a" * 40}},
            {"object": {"type": "commit", "sha": "b" * 40}},
        ]) as api:
            self.assertEqual(resolve_tag_commit("owner/tool", "v1.0.0"), "b" * 40)
        self.assertEqual(api.call_args_list[1].args[0], "repos/owner/tool/git/tags/" + "a" * 40)

    def test_truncated_api_json_is_retried(self):
        with (patch("apk_repository.upstream.open_url", side_effect=[
            io.BytesIO(b'{"incomplete":"'), io.BytesIO(b'{"ok":true}'),
        ]) as opened, patch("apk_repository.upstream.time.sleep")):
            self.assertEqual(api_json("repos/owner/tool/releases"), {"ok": True})
        self.assertEqual(opened.call_count, 2)

    def test_removed_prerelease_channel_is_rejected(self):
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
