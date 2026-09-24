"""Assemble fresh feeds from prebuilt upstream OpenWrt APKs, without private keys."""

import datetime
import json
import os
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from .apk import check_tool, index, metadata, validate_package
from .config import load, require
from .upstream import download, package_version, resolve_release, select_asset, sha256


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def source_license(archive, declared_license):
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        require(len(members) < 100000, "source archive has too many entries")
        matches = [m for m in members if len(PurePosixPath(m.name).parts) == 2
                   and PurePosixPath(m.name).name == "LICENSE"]
        require(len(matches) <= 1, "source archive has duplicate top-level LICENSE files")
        if matches:
            member = matches[0]
            require(member.isfile() and not member.islnk() and not member.issym()
                    and 0 < member.size < 1024 * 1024, "invalid LICENSE file")
            with source.extractfile(member) as stream:
                return stream.read()
        makefiles = [m for m in members if len(PurePosixPath(m.name).parts) == 2
                     and PurePosixPath(m.name).name == "Makefile"]
        require(len(makefiles) == 1, "source archive has no LICENSE or single top-level Makefile")
        member = makefiles[0]
        require(member.isfile() and not member.islnk() and not member.issym()
                and 0 < member.size < 1024 * 1024, "invalid Makefile")
        with source.extractfile(member) as stream:
            makefile = stream.read().decode("utf-8")
        declaration = "PKG_LICENSE:=" + declared_license
        require(sum(line.strip() == declaration for line in makefile.splitlines()) == 1,
                "upstream Makefile license does not match package configuration")
        return ("Upstream Makefile declares " + declaration + "\n"
                + "License text: https://spdx.org/licenses/" + declared_license + ".html\n").encode("utf-8")


def build(root, apk, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    repo, packages = load(root)
    check_tool(apk, repo["apk_tools"]["version"])
    require(not output.exists(), "output already exists; use a fresh build directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="apk-build-", dir=output.parent) as temporary:
        work = Path(temporary)
        site = work / "site"
        site.mkdir()
        manifest = {"schema_version": 1, "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
                    "apk_tools": repo["apk_tools"], "base_url": repo["base_url"],
                    "workflow_url": os.environ.get("GITHUB_SERVER_URL", "https://github.com") + "/" + os.environ.get("GITHUB_REPOSITORY", "local") + "/actions/runs/" + os.environ.get("GITHUB_RUN_ID", "local"),
                    "packages": []}
        resolutions = {}
        for channel in repo["channels"]:
            for pkg in packages:
                if not pkg["enabled"] or channel not in pkg["channels"]:
                    continue
                key = (pkg["source"], channel)
                if key not in resolutions:
                    resolutions[key] = resolve_release(pkg["source"], channel)
                release, commit = resolutions[key]
                source_rel = "sources/{}/{}.tar.gz".format(pkg["name"], commit)
                source_path = site / source_rel
                if not source_path.exists():
                    download("https://codeload.github.com/{}/tar.gz/{}".format(pkg["source"], commit), source_path)
                    (source_path.parent / (commit + ".LICENSE")).write_bytes(source_license(source_path, pkg["license"]))
                for target in repo["targets"]:
                    arch = target["arch"]
                    if arch not in pkg["architectures"]:
                        continue
                    mapping = pkg["architectures"][arch]
                    asset = select_asset(release, pkg["source"], mapping["asset_pattern"])
                    digest = asset["digest"].split(":", 1)[1]
                    asset_path = download(asset["browser_download_url"], work / "downloads" / digest,
                                          digest, asset["size"])
                    observed = (metadata(apk, asset_path)["info"].get("version")
                                if pkg["revision"] is None and "{revision}" not in mapping["asset_pattern"] else None)
                    version = package_version(release["tag_name"], mapping["asset_pattern"],
                                              asset["name"], pkg["revision"], observed)
                    feed = site / "apk" / channel / arch
                    feed.mkdir(parents=True, exist_ok=True)
                    destination = feed / (pkg["name"] + "-" + version + ".apk")
                    shutil.copyfile(asset_path, destination)
                    validate_package(apk, destination, pkg, version, mapping["package_arch"])
                    manifest["packages"].append({
                        "name": pkg["name"], "version": version, "channel": channel, "arch": arch,
                        "abi": target["abi"], "package_arch": mapping["package_arch"],
                        "file": destination.relative_to(site).as_posix(), "source": pkg["source"],
                        "source_commit": commit, "source_archive": source_rel, "source_sha256": sha256(source_path),
                        "release_id": release["id"], "release_tag": release["tag_name"],
                        "upstream_prerelease": release["prerelease"], "asset_id": asset["id"],
                        "asset_name": asset["name"], "asset_sha256": digest, "sha256": sha256(destination),
                    })
            for target in repo["targets"]:
                index(apk, site / "apk" / channel / target["arch"])
        require(manifest["packages"], "no enabled packages")
        write_json(site / "manifest.json", manifest)
        # Publish only a complete fresh tree. No old APKs or source archives are carried forward.
        site.rename(output)
