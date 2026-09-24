"""Strict, offline configuration validation. No build or publication side effects."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit


class ConfigError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ConfigError(message)


def fields(value, expected, context):
    require(isinstance(value, dict), context + " must be an object")
    require(set(value) == set(expected.split()), context + ": unexpected or missing fields")


def matches(value, pattern):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def string_list(value, context, allowed=None):
    require(isinstance(value, list), context + " must be a list")
    require(all(isinstance(item, str) and item for item in value), context + ": invalid entry")
    require(len(value) == len(set(value)), context + ": duplicate entry")
    if allowed is not None:
        require(set(value) <= set(allowed), context + ": unknown entry")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key: " + key)
        result[key] = value
    return result


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def load(root):
    root = Path(root).resolve()
    repo = read_json(root / "repository.json")
    fields(repo, "schema_version name base_url channels apk_tools packages targets", "repository")
    require(type(repo["schema_version"]) is int and repo["schema_version"] == 1, "unsupported repository schema")
    require(repo["name"] == "apk-repository", "repository name must be apk-repository")
    require(isinstance(repo["base_url"], str), "base_url must be a string")
    url = urlsplit(repo["base_url"])
    require(url.scheme == "https" and url.hostname and not url.username and not url.password
            and not url.query and not url.fragment and not repo["base_url"].endswith("/"),
            "base_url must be an HTTPS URL without credentials, query, fragment or trailing slash")
    string_list(repo["channels"], "channels", ["stable", "latest"])
    require(set(repo["channels"]) == {"stable", "latest"}, "both channels are required")
    tool = repo["apk_tools"]
    fields(tool, "version source commit", "apk_tools")
    require(matches(tool["version"], r"3\.\d+\.\d+"), "apk_tools must pin a v3 release")
    require(tool["source"] == "https://gitlab.alpinelinux.org/alpine/apk-tools.git", "unexpected APK Tools source")
    require(matches(tool["commit"], r"[0-9a-f]{40}"), "apk_tools must pin a full commit")
    string_list(repo["packages"], "packages")
    require(isinstance(repo["targets"], list) and repo["targets"], "targets must be a nonempty list")
    arches = set()
    for target in repo["targets"]:
        fields(target, "arch abi", "target")
        require(matches(target["arch"], r"[a-z0-9][a-z0-9_-]*"), "target must use an exact architecture")
        require(target["arch"] not in arches, "duplicate target architecture")
        arches.add(target["arch"])
        require(target["abi"] is None or matches(target["abi"], r"[a-zA-Z0-9][a-zA-Z0-9_.-]*"), "invalid target ABI")
    # Current URL layout has no ABI component: never mix different ABI profiles.
    require(len({target["abi"] for target in repo["targets"] if target["abi"] is not None}) <= 1,
            "multiple ABI profiles require separate feeds")
    packages = []
    for name in repo["packages"]:
        require(matches(name, r"[a-z0-9][a-z0-9+.-]*"), "invalid package name: " + name)
        path = root / "packages" / name / "package.json"
        require(root in path.resolve().parents, "package path escapes repository")
        pkg = read_json(path)
        fields(pkg, "schema_version name enabled source method channels license license_reviewed revision dependencies conflicts architectures", name)
        require(type(pkg["schema_version"]) is int and pkg["schema_version"] == 1, name + ": unsupported schema")
        require(pkg["name"] == name, name + ": package name mismatch")
        require(type(pkg["enabled"]) is bool and type(pkg["license_reviewed"]) is bool, name + ": flags must be boolean")
        require(matches(pkg["source"], r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"), name + ": expected owner/repository")
        require(pkg["method"] == "sync-apk", name + ": only upstream APK synchronization is supported")
        require(pkg["revision"] is None or (type(pkg["revision"]) is int and pkg["revision"] >= 0),
                name + ": revision must be nonnegative or null")
        require(isinstance(pkg["license"], str) and pkg["license"], name + ": license required")
        string_list(pkg["channels"], name + ": channels", repo["channels"])
        require(pkg["channels"], name + ": at least one channel required")
        for key in ("dependencies", "conflicts"):
            string_list(pkg[key], name + ": " + key)
        mappings = pkg["architectures"]
        require(isinstance(mappings, dict) and mappings, name + ": architecture mappings required")
        require(set(mappings) <= arches, name + ": unknown architecture")
        for arch, mapping in mappings.items():
            fields(mapping, "asset_pattern package_arch", name + ": " + arch)
            require(mapping["package_arch"] in (arch, "noarch"), name + ": incompatible package architecture")
            pattern = mapping["asset_pattern"]
            require(pattern is None or (isinstance(pattern, str) and pattern.endswith(".apk")
                    and pattern.count("{version}") == 1
                    and pattern.count("{revision}") <= (1 if pkg["revision"] is None else 0)
                    and matches(pattern.replace("{version}", "1.0.0").replace("{revision}", "1"),
                                r"[A-Za-z0-9][A-Za-z0-9_.+-]*")),
                    name + ": asset pattern must be a filename with {version} and a compatible revision placeholder")
        if pkg["enabled"]:
            require(pkg["license_reviewed"], name + ": license review required before enabling")
            require(all(m["asset_pattern"] for m in mappings.values()), name + ": asset pattern required before enabling")
            require(all(t["abi"] is not None for t in repo["targets"] if t["arch"] in mappings), name + ": target ABI required before enabling")
        packages.append(pkg)
    for channel in repo["channels"]:
        for target in repo["targets"]:
            arch = target["arch"]
            require(any(pkg["enabled"] and channel in pkg["channels"] and arch in pkg["architectures"]
                        for pkg in packages), f"empty feed: {channel}/{arch}")
    return repo, packages


def plan(repo, packages, channel, include_disabled=False):
    require(channel in repo["channels"], "unknown channel: " + channel)
    jobs = []
    for pkg in packages:
        if channel not in pkg["channels"] or (not pkg["enabled"] and not include_disabled):
            continue
        for target in repo["targets"]:
            arch = target["arch"]
            if arch not in pkg["architectures"]:
                continue
            mapping = pkg["architectures"][arch]
            blockers = []
            for ok, reason in (
                (pkg["enabled"], "package-disabled"),
                (pkg["license_reviewed"], "license-review-required"),
                (target["abi"] is not None, "target-abi-required"),
                (mapping["asset_pattern"] is not None, "asset-pattern-required"),
            ):
                if not ok:
                    blockers.append(reason)
            jobs.append({"package": pkg["name"], "source": pkg["source"], "method": pkg["method"],
                         "arch": arch, "abi": target["abi"], "package_arch": mapping["package_arch"],
                         "asset_pattern": mapping["asset_pattern"], "blockers": blockers,
                         "index_url": "{}/apk/{}/{}/packages.adb".format(repo["base_url"], channel, arch)})
    return {"schema_version": 1, "channel": channel, "apk_tools": repo["apk_tools"],
            "planning_only": True, "jobs": jobs}
