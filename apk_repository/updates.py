"""Compare upstream release metadata with the currently published site."""

import json
import os
import urllib.error

from .config import load, require
from .upstream import open_url, resolve_release, select_asset, version_parts

FIELDS = ("channel", "arch", "name", "version", "source", "source_commit",
          "release_id", "release_tag", "upstream_prerelease", "asset_id", "asset_sha256")


def identities(records):
    return sorted(tuple(record[field] for field in FIELDS) for record in records)


def check_updates(root):
    repo, packages = load(root)
    current = None
    try:
        with open_url(repo["base_url"] + "/manifest.json") as response:
            content = response.read(16 * 1024 * 1024 + 1)
        require(len(content) <= 16 * 1024 * 1024, "published manifest too large")
        current = json.loads(content)
        require(current.get("schema_version") == 1, "unsupported published manifest")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise

    records = []
    resolutions = {}
    for channel in repo["channels"]:
        for pkg in packages:
            if not pkg["enabled"] or channel not in pkg["channels"]:
                continue
            key = (pkg["source"], channel, pkg["prerelease_fallback"])
            if key not in resolutions:
                resolutions[key] = resolve_release(pkg["source"], channel, fallback=pkg["prerelease_fallback"])
            release, commit = resolutions[key]
            _, version = version_parts(release["tag_name"])
            for target in repo["targets"]:
                arch = target["arch"]
                if arch not in pkg["architectures"]:
                    continue
                asset = select_asset(release, pkg["source"], pkg["architectures"][arch]["asset_pattern"])
                records.append({"channel": channel, "arch": arch, "name": pkg["name"],
                    "version": version + "-r" + str(pkg["revision"]), "source": pkg["source"],
                    "source_commit": commit, "release_id": release["id"],
                    "release_tag": release["tag_name"], "upstream_prerelease": release["prerelease"],
                    "asset_id": asset["id"], "asset_sha256": asset["digest"].split(":", 1)[1]})
    require(records, "no enabled packages")
    changed = current is None or identities(records) != identities(current["packages"])
    message = "Upstream changes detected; publication required." if changed else "No upstream changes; publication skipped."
    print(message)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("changed=" + str(changed).lower() + "\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write(message + "\n")
    return changed
