"""Identify compatible signed APKs and authenticate cached manifests."""

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .apk import run, validate_package
from .config import require
from .upstream import sha256


def fingerprint(repo, package, target):
    # Changes to processing code invalidate reuse, independently of cache keys.
    code = {p.name: sha256(p) for p in sorted(Path(__file__).resolve().parent.glob("*.py"))}
    value = {"code": code, "tools": repo["apk_tools"], "package": package, "target": target}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def previous_records(previous, public, repo):
    if previous is None or not Path(previous).exists():
        return {}
    from .publish import child

    previous = Path(previous).resolve()
    try:
        checksums = child(previous, "SHA256SUMS")
        run(["openssl", "dgst", "-sha256", "-verify", public, "-signature",
             child(previous, "SHA256SUMS.sig"), checksums], capture_output=True)
        manifest_path = child(previous, "manifest.json")
        entries = [line.split("  ", 1) for line in checksums.read_text().splitlines()]
        digests = [digest for digest, relative in entries if relative == "manifest.json"]
        require(digests == [sha256(manifest_path)], "cached manifest checksum mismatch")
        manifest = json.loads(manifest_path.read_text())
        require(manifest["schema_version"] == 1 and manifest["apk_tools"] == repo["apk_tools"]
                and manifest["base_url"] == repo["base_url"], "cached manifest incompatible")
        records = {}
        for record in manifest["packages"]:
            identity = (record["channel"], record["arch"], record["name"])
            require(identity not in records, "duplicate cached package")
            records[identity] = record
        return records
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        print("Previous signed cache unavailable or incompatible; signing fresh APKs.")
        return {}


def reuse_apk(apk, previous, records, record, destination, package, keys):
    from .publish import child

    old = records.get((record["channel"], record["arch"], record["name"]))
    fields = ("version", "source", "source_commit", "asset_id", "asset_sha256",
              "release_id", "release_tag", "upstream_prerelease", "file", "abi", "reuse_fingerprint")
    if old is None or any(old.get(field) != record.get(field) for field in fields):
        return False
    # Old manifests without a compatibility fingerprint must never be reused.
    if not record.get("reuse_fingerprint"):
        return False
    try:
        path = child(Path(previous).resolve(), old["file"])
        destination = Path(destination)
        # Keep the original upstream APK intact until a complete copy is validated.
        # A sibling staging directory ensures replace stays on the same filesystem.
        with tempfile.TemporaryDirectory(prefix="apk-reuse-", dir=destination.parent) as temporary:
            candidate = Path(temporary) / "candidate.apk"
            shutil.copyfile(path, candidate)
            require(sha256(candidate) == old["sha256"], "cached APK checksum mismatch")
            validate_package(apk, candidate, package, record["version"],
                             package["architectures"][record["arch"]]["package_arch"], keys)
            candidate.replace(destination)
        return True
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        print("Cached APK rejected; signing fresh: " + record["file"])
        return False
