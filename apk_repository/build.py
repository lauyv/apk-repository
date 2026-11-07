"""Assemble fresh feeds from prebuilt upstream OpenWrt APKs, without private keys."""

import datetime
import json
import os
import re
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .apk import check_tool, metadata, validate_package
from .config import load, require
from .upstream import (
    download,
    package_version,
    resolve_channels,
    resolve_release,
    select_asset,
    sha256,
)


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build(root, apk, output, cache=None):
    root, output = Path(root).resolve(), Path(output).resolve()
    repo, packages = load(root)
    check_tool(apk, repo["apk_tools"]["version"])
    require(not output.exists(), "output already exists; use a fresh build directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="apk-build-", dir=output.parent) as temporary:
        work = Path(temporary)
        cache_root = Path(cache).resolve() if cache else work
        downloads = cache_root / "apk-repository-downloads-v1"
        require(downloads.resolve() == downloads and not downloads.is_symlink(),
                "download cache directory must not be a link")
        downloads.mkdir(parents=True, exist_ok=True)
        used_downloads = set()
        site = work / "site"
        site.mkdir()
        manifest = {"schema_version": 1, "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
                    "apk_tools": repo["apk_tools"], "base_url": repo["base_url"],
                    "workflow_url": os.environ.get("GITHUB_SERVER_URL", "https://github.com") + "/" + os.environ.get("GITHUB_REPOSITORY", "local") + "/actions/runs/" + os.environ.get("GITHUB_RUN_ID", "local"),
                    "packages": []}
        releases = resolve_channels(packages, resolve_release)
        assets = {}
        for pkg in packages:
            for channel in pkg["channels"]:
                release, _ = releases[pkg["source"], channel]
                for target in repo["targets"]:
                    if target["arch"] in pkg["architectures"]:
                        mapping = pkg["architectures"][target["arch"]]
                        asset = select_asset(release, pkg["source"], mapping["asset_pattern"])
                        assets[pkg["name"], channel, target["arch"]] = asset
        # Download each digest once, even when channels or noarch mappings share it.
        unique = {a["digest"].split(":", 1)[1]: a for a in assets.values()}
        with ThreadPoolExecutor(max_workers=4) as pool:
            pending = {digest: pool.submit(download, a["browser_download_url"], downloads / digest,
                                          digest, a["size"]) for digest, a in unique.items()}
            downloaded = {digest: future.result() for digest, future in pending.items()}
        observed_versions = {}
        validated = set()
        for pkg in packages:
            for channel in pkg["channels"]:
                release, commit = releases[pkg["source"], channel]
                for target in repo["targets"]:
                    arch = target["arch"]
                    if arch not in pkg["architectures"]:
                        continue
                    mapping = pkg["architectures"][arch]
                    asset = assets[pkg["name"], channel, arch]
                    digest = asset["digest"].split(":", 1)[1]
                    used_downloads.add(digest)
                    asset_path = downloaded[digest]
                    require(asset_path.stat().st_size == asset["size"], "cached asset size mismatch")
                    observed = None
                    if "{revision}" not in mapping["asset_pattern"]:
                        if digest not in observed_versions:
                            observed_versions[digest] = metadata(apk, asset_path)["info"].get("version")
                        observed = observed_versions[digest]
                    version = package_version(release["tag_name"], mapping["asset_pattern"],
                                              asset["name"], observed)
                    feed = site / "apk" / channel / arch
                    feed.mkdir(parents=True, exist_ok=True)
                    destination = feed / (pkg["name"] + "-" + version + ".apk")
                    shutil.copyfile(asset_path, destination)
                    identity = (digest, pkg["name"], version, mapping["package_arch"])
                    if identity not in validated:
                        validate_package(apk, destination, pkg, version, mapping["package_arch"])
                        validated.add(identity)
                    manifest["packages"].append({
                        "name": pkg["name"], "version": version, "channel": channel, "arch": arch,
                        "abi": target["abi"], "package_arch": mapping["package_arch"],
                        "file": destination.relative_to(site).as_posix(), "source": pkg["source"],
                        "source_commit": commit,
                        "release_id": release["id"], "release_tag": release["tag_name"],
                        "upstream_prerelease": release["prerelease"], "asset_id": asset["id"],
                        "asset_name": asset["name"], "asset_sha256": digest, "sha256": sha256(destination),
                    })
        require(manifest["packages"], "no packages")
        write_json(site / "manifest.json", manifest)
        # Publish only a complete fresh tree. No old APKs or source archives are carried forward.
        site.rename(output)
        # Only prune digest-named entries in our own namespace, never the cache root.
        for path in downloads.iterdir():
            if (re.fullmatch(r"[0-9a-f]{64}", path.name) and not path.is_symlink()
                    and path.is_file() and path.name not in used_downloads):
                path.unlink()
