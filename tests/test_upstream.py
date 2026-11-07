import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from apk_repository.config import ConfigError
from apk_repository.upstream import (
    api_json,
    download,
    package_version,
    resolve_channels,
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

    def test_latest_ignores_release_channel_and_drafts(self):
        releases = [self.release("v1.4.0", False, "4", True), self.release("v1.3.0-beta.1", True, "3"),
                    self.release("v1.2.0", False, "2")]
        self.assertEqual(select_release(releases)["tag_name"], "v1.3.0-beta.1")
        releases.insert(0, self.release("v1.3.0", False, "5"))
        self.assertEqual(select_release(releases)["tag_name"], "v1.3.0")

    def test_latest_reads_only_current_release_page(self):
        older = self.release("v1.2.0", False, "2")
        newer = self.release("v1.3.0-beta.1", True, "3")
        with patch("apk_repository.upstream.api_json", side_effect=[
            [newer, older], {"object": {"type": "commit", "sha": "a" * 40}},
        ]) as api:
            release, commit = resolve_release("owner/tool")
        self.assertEqual((release["tag_name"], commit), ("v1.3.0-beta.1", "a" * 40))
        self.assertEqual(api.call_args_list[0].args[0], "repos/owner/tool/releases?per_page=10")
        self.assertEqual(api.call_count, 2)

    def test_stable_uses_official_latest_release_endpoint(self):
        stable = self.release("v1.2.0", False, "2")
        with patch("apk_repository.upstream.api_json", side_effect=[
            stable, {"object": {"type": "commit", "sha": "a" * 40}},
        ]) as api:
            release, _ = resolve_release("owner/tool", channel="stable")
        self.assertEqual(release["tag_name"], "v1.2.0")
        self.assertEqual(api.call_args_list[0].args[0], "repos/owner/tool/releases/latest")

    def test_stable_rejects_prerelease_flag_and_tag(self):
        for tag, prerelease in (("v1.3.0", True), ("v1.3.0-beta.1", False)):
            with self.subTest(tag=tag), self.assertRaises(ConfigError):
                select_release([self.release(tag, prerelease, "3")], channel="stable")

    def test_channel_queries_are_parallel_and_deduplicated(self):
        barrier = Barrier(2)
        calls = []

        def resolve(source, channel):
            calls.append((source, channel))
            barrier.wait(timeout=5)
            return channel

        packages = [{"source": "owner/tool", "channels": ["stable", "latest"]}] * 2
        result = resolve_channels(packages, resolve)
        self.assertEqual(set(calls), {("owner/tool", "stable"), ("owner/tool", "latest")})
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["owner/tool", "stable"], "stable")

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

    def test_apk_prerelease_version_mapping(self):
        self.assertEqual(version_parts("v1.14.0-beta.8"), ("1.14.0-beta.8", "1.14.0_beta8"))
        self.assertEqual(version_parts("1.14.0-rc.3"), ("1.14.0-rc.3", "1.14.0_rc3"))
        with self.assertRaises(ConfigError):
            version_parts("v1.2.3;echo bad")

    def test_revisioned_release_versions(self):
        for tag, upstream, apk in (("v0.1.0-beta.5-r3", "0.1.0-beta.5", "0.1.0_beta5"),
                                   ("1.2.3-r0", "1.2.3", "1.2.3")):
            self.assertEqual(version_parts(tag), (upstream, apk))
            self.assertEqual(select_release([self.release(tag, True, "1")])["tag_name"], tag)
        for tag in ("v1.2.3-r01", "v1.2.3-r-1", "v1.2.3-r", "v1.2.3-r2-extra"):
            with self.assertRaises(ConfigError):
                version_parts(tag)

    def test_revisioned_tag_must_match_apk_revision(self):
        tag = "v1.2.3-beta.4-r3"
        pattern = "tool-{apk_version}-r{revision}.apk"
        self.assertEqual(package_version(tag, pattern, "tool-1.2.3_beta4-r3.apk"), "1.2.3_beta4-r3")
        with self.assertRaisesRegex(ConfigError, "revision does not match"):
            package_version(tag, pattern, "tool-1.2.3_beta4-r2.apk")
        with self.assertRaisesRegex(ConfigError, "revision does not match"):
            package_version(tag, "tool-{version}.apk", "tool-1.2.3-beta.4.apk", "1.2.3_beta4-r2")

    def asset_release(self):
        return {"tag_name": "v1.2.3", "assets": [{"id": 1, "name": "tool_1.2.3_openwrt_x86_64.apk",
            "digest": "sha256:" + "a" * 64, "size": 42,
            "browser_download_url": "https://github.com/owner/tool/releases/download/v1.2.3/tool_1.2.3_openwrt_x86_64.apk"}]}

    def test_only_exact_openwrt_asset_is_selected(self):
        release = self.asset_release()
        self.assertEqual(select_asset(release, "owner/tool", "tool_{version}_openwrt_x86_64.apk")["id"], 1)
        with self.assertRaises(ConfigError):
            select_asset(release, "owner/tool", "tool_{version}_linux_x86_64.apk")

    def test_revision_placeholder_selects_one_asset_and_derives_version(self):
        release = self.asset_release()
        release["assets"][0]["name"] = "tool_1.2.3-r5.apk"
        release["assets"][0]["browser_download_url"] = (
            "https://github.com/owner/tool/releases/download/v1.2.3/tool_1.2.3-r5.apk")
        pattern = "tool_{version}-r{revision}.apk"
        asset = select_asset(release, "owner/tool", pattern)
        self.assertEqual(package_version("v1.2.3", pattern, asset["name"]), "1.2.3-r5")
        release["assets"].append(dict(asset, name="tool_1.2.3-r4.apk"))
        with self.assertRaisesRegex(ConfigError, "exactly one asset"):
            select_asset(release, "owner/tool", pattern)

    def test_revision_can_come_from_apk_metadata_without_filename_suffix(self):
        pattern = "tool_{version}_openwrt_x86_64.apk"
        name = "tool_1.2.3_openwrt_x86_64.apk"
        self.assertEqual(package_version("v1.2.3", pattern, name, "1.2.3-r0"), "1.2.3-r0")
        with self.assertRaisesRegex(ConfigError, "does not match Release tag"):
            package_version("v1.2.3", pattern, name, "1.2.4-r0")

    def test_honk_architecture_assets_use_apk_prerelease_version(self):
        for suffix in ("alpha", "beta", "rc"):
            for arch in ("aarch64_generic", "x86_64"):
                release = self.asset_release()
                release["tag_name"] = f"v1.2.3-{suffix}.4-r2"
                asset = release["assets"][0]
                asset["name"] = f"luci-app-honk-1.2.3_{suffix}4-r2-{arch}.apk"
                asset["browser_download_url"] = (
                    f"https://github.com/owner/tool/releases/download/{release['tag_name']}/{asset['name']}")
                pattern = f"luci-app-honk-{{apk_version}}-r{{revision}}-{arch}.apk"
                self.assertEqual(select_asset(release, "owner/tool", pattern), asset)
                self.assertEqual(package_version(release["tag_name"], pattern, asset["name"]),
                                 f"1.2.3_{suffix}4-r2")
                with self.assertRaisesRegex(ConfigError, "metadata version mismatch"):
                    package_version(release["tag_name"], pattern, asset["name"], "1.2.3-r2")

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

    def test_verified_cached_asset_skips_network_and_corruption_is_redownloaded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset.apk"
            content = b"upstream APK"
            digest = hashlib.sha256(content).hexdigest()
            path.write_bytes(content)
            with patch("apk_repository.upstream.open_url") as opened:
                self.assertEqual(download("https://example.test/file", path, digest, len(content)), path)
                opened.assert_not_called()
            path.write_bytes(b"corrupt")
            with patch("apk_repository.upstream.open_url", return_value=io.BytesIO(content)) as opened:
                download("https://example.test/file", path, digest, len(content))
                opened.assert_called_once()
            self.assertEqual(path.read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
