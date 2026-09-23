"""APK v3 helpers. All commands use argv, never interpolated shell code."""

import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath

from .config import require


def run(argv, **kwargs):
    return subprocess.run([str(arg) for arg in argv], check=True, text=True, **kwargs)


def check_tool(apk, expected):
    result = run([apk, "--version"], capture_output=True)
    require(re.search(r"\bapk-tools " + re.escape(expected) + r"(?:,|\s|$)", result.stdout), "wrong APK Tools version")


def metadata(apk, path):
    value = json.loads(run([apk, "adbdump", "--format", "json", path], capture_output=True).stdout)
    require(isinstance(value, dict) and isinstance(value.get("info"), dict), "expected an APK v3 package")
    return value


def validate_package(apk, path, package, version, arch, signed_keys=None):
    args = [apk, "verify"]
    args += ["--keys-dir", signed_keys] if signed_keys else ["--allow-untrusted"]
    run(args + [path])
    document = metadata(apk, path)
    info = document["info"]
    for field, expected in (("name", package["name"]), ("version", version), ("arch", arch), ("license", package["license"])):
        require(info.get(field) == expected, f"unexpected {field} in {path}")
    expected_depends = package["dependencies"] + ["!" + name for name in package["conflicts"]]
    require(sorted(info.get("depends", [])) == sorted(expected_depends), "package dependency mismatch: " + package["name"])
    require(not info.get("install-if") and not info.get("replaces") and not info.get("provides"), "unexpected package replacement or auto-install metadata")
    for directory in document.get("paths", []):
        name = directory.get("name", "")
        require(not name.startswith("/") and ".." not in PurePosixPath(name).parts, "unsafe APK directory")
        for item in directory.get("files", []):
            require(re.fullmatch(r"[^/]+", item["name"]) and item["name"] not in (".", ".."), "unsafe APK filename")
            acl = item.get("acl", {})
            require(acl.get("user", "root") == "root" and acl.get("group", "root") == "root", "non-root APK ownership")
    return info


def make_package(apk, root, destination, info, scripts=None):
    args = [apk, "mkpkg", "--compat", "3.0.0", "--files", root, "--output", destination]
    for key, value in info.items():
        args += ["--info", f"{key}:{value}"]
    for kind, path in (scripts or {}).items():
        args += ["--script", f"{kind}:{path}"]
    # The pipeline runs under fakeroot, preserving root ownership in package metadata.
    require(os.geteuid() == 0 or os.environ.get("FAKEROOTKEY"), "run packaging under fakeroot")
    run(args)


def index(apk, directory, key=None):
    packages = sorted(Path(directory).glob("*.apk"))
    require(packages, "refusing an empty feed")
    args = [apk, "mkndx", "--allow-untrusted", "--output", Path(directory) / "packages.adb",
            "--pkgname-spec", "${name}-${version}.apk"]
    if key:
        args += ["--sign-key", key]
    run(args + packages)
