import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apk_repository.build import build
from apk_repository.config import load

ROOT = Path(__file__).resolve().parents[1]


class BuildCacheTests(unittest.TestCase):
    def test_honk_build_publishes_architecture_apks_and_bundled_sources(self):
        repo, packages = load(ROOT)
        package = next(p for p in packages if p["name"] == "luci-app-honk")
        tag = "v0.1.0-beta.4-r1"
        release = {"id": 1, "tag_name": tag, "prerelease": True, "assets": []}
        content_by_url = {}
        names = [f"luci-app-honk-0.1.0_beta4-r1-{t['arch']}.apk" for t in repo["targets"]]
        names += ["honk-source-v0.1.0-beta.4.tar.gz", "doona-source-v1.0.0.tar.gz"]
        for i, name in enumerate(names):
            content = ("fixture " + name).encode()
            url = f"https://github.com/{package['source']}/releases/download/{tag}/{name}"
            content_by_url[url] = content
            release["assets"].append({"id": i + 1, "name": name, "size": len(content),
                                      "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
                                      "browser_download_url": url})

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with (patch("apk_repository.build.load", return_value=(repo, [package])),
                  patch("apk_repository.build.check_tool"),
                  patch("apk_repository.build.resolve_release", return_value=(release, "a" * 40)),
                  patch("apk_repository.build.validate_package") as validate,
                  patch("apk_repository.upstream.open_url", side_effect=lambda url: io.BytesIO(
                      content_by_url.get(url, b"LuCI source"))) as network):
                for attempt in ("first", "second"):
                    build(ROOT, "apk", directory / attempt, directory / "cache")
            records = json.loads((directory / "second/manifest.json").read_text())["packages"]
            self.assertEqual(len(records), 2)
            self.assertEqual(validate.call_count, 4)
            self.assertEqual({call.args[-1] for call in validate.call_args_list}, {"x86_64", "aarch64_generic"})
            for record in records:
                self.assertEqual(record["version"], "0.1.0_beta4-r1")
                self.assertEqual(record["release_tag"], tag)
                self.assertEqual(len(record["source_assets"]), 2)
                for source in record["source_assets"]:
                    path = directory / "second" / source["file"]
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), source["sha256"])
            self.assertEqual(sum(call.args[0] in content_by_url for call in network.call_args_list), 4)

    def test_second_build_reuses_verified_downloads_in_fresh_tree(self):
        self.check_cached_build("luci-app-cake-tiny", 3)

    def test_cached_apk_keeps_revision_derived_from_metadata(self):
        self.check_cached_build("sing-box", 2)

    def check_cached_build(self, package_name, revision):
        repo, packages = load(ROOT)
        repo["targets"] = [repo["targets"][0]]
        package = next(package for package in packages if package["name"] == package_name)
        apk_bytes = b"upstream fixture APK"
        digest = hashlib.sha256(apk_bytes).hexdigest()
        release = {"id": 1, "tag_name": "v1.2.3", "prerelease": False}
        pattern = package["architectures"][repo["targets"][0]["arch"]]["asset_pattern"]
        asset = {"id": 2, "name": pattern.format(version="1.2.3", revision=revision), "digest": "sha256:" + digest,
                 "size": len(apk_bytes), "browser_download_url": "https://example.test/fixture.apk"}

        def opened(url):
            return io.BytesIO(apk_bytes if url == asset["browser_download_url"] else b"source archive fixture")

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            cache = directory / "cache"
            cache.mkdir()
            (cache / "unrelated-notes.txt").write_bytes(b"keep notes")
            (cache / ("c" * 64)).write_bytes(b"another application's cache")
            downloads = cache / "apk-repository-downloads-v1"
            downloads.mkdir()
            obsolete = downloads / ("d" * 64)
            obsolete.write_bytes(b"old APK")
            (downloads / "README.txt").write_bytes(b"keep cache notes")
            with (
                patch("apk_repository.build.load", return_value=(repo, [package])),
                patch("apk_repository.build.check_tool"),
                patch("apk_repository.build.resolve_release", return_value=(release, "a" * 40)),
                patch("apk_repository.build.select_asset", return_value=asset),
                patch("apk_repository.build.metadata", return_value={"info": {"version": f"1.2.3-r{revision}"}}),
                patch("apk_repository.build.validate_package") as validate,
                patch("apk_repository.upstream.open_url", side_effect=opened) as network,
            ):
                first, second = directory / "first", directory / "second"
                build(ROOT, "apk", first, cache)
                # A stale file from the previous site must not be copied into the new one.
                (first / "old.apk").write_bytes(b"stale")
                build(ROOT, "apk", second, cache)
            apk_requests = [call for call in network.call_args_list if call.args[0] == asset["browser_download_url"]]
            self.assertEqual(len(apk_requests), 1)
            self.assertEqual(validate.call_count, 2)  # Latest is validated on both runs.
            self.assertFalse((second / "old.apk").exists())
            self.assertEqual((cache / "unrelated-notes.txt").read_bytes(), b"keep notes")
            self.assertEqual((cache / ("c" * 64)).read_bytes(), b"another application's cache")
            self.assertEqual((downloads / "README.txt").read_bytes(), b"keep cache notes")
            self.assertFalse(obsolete.exists())
            self.assertEqual({p.name for p in downloads.iterdir()}, {digest, "README.txt"})
            manifest = json.loads((second / "manifest.json").read_text())
            self.assertEqual(len(manifest["packages"]), 1)
            self.assertEqual({record["version"] for record in manifest["packages"]}, {f"1.2.3-r{revision}"})
            self.assertFalse(list(second.rglob("packages.adb")))
