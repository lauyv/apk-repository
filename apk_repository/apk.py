"""APK v3 helpers. All commands use argv, never interpolated shell code."""

import json
import re
import subprocess
from pathlib import Path

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
    for field, expected in (("name", package["name"]), ("version", version), ("arch", arch)):
        require(info.get(field) == expected, f"unexpected {field} in {path}")
    return info


def index(apk, directory, key):
    packages = sorted(Path(directory).glob("*.apk"))
    require(packages, "refusing an empty feed")
    args = [apk, "mkndx", "--allow-untrusted", "--output", Path(directory) / "packages.adb",
            "--pkgname-spec", "${name}-${version}.apk"]
    args += ["--sign-key", key]
    run(args + packages)
