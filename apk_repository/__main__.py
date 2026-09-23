import argparse
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

from .config import ConfigError, load, plan


def main():
    parser = argparse.ArgumentParser(description="Synchronize upstream OpenWrt APKs and publish signed APK v3 feeds.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="validate all registered packages, including disabled ones")
    preview = commands.add_parser("plan", help="print a JSON plan; does not download, build or publish")
    preview.add_argument("--channel", choices=("stable", "prerelease"), default="stable")
    preview.add_argument("--include-disabled", action="store_true", help="also show candidate packages and blockers")
    assemble = commands.add_parser("build", help="download and validate latest stable/prerelease APKs")
    assemble.add_argument("--apk", required=True, type=Path)
    assemble.add_argument("--output", required=True, type=Path)
    signing = commands.add_parser("sign", help="sign a complete build and produce the Pages site")
    signing.add_argument("--apk", required=True, type=Path)
    signing.add_argument("--input", required=True, type=Path)
    signing.add_argument("--output", required=True, type=Path)
    signing.add_argument("--key", required=True, type=Path)
    verification = commands.add_parser("verify", help="verify site signatures, checksums and indexes")
    verification.add_argument("--apk", required=True, type=Path)
    verification.add_argument("--site", required=True, type=Path)
    args = parser.parse_args()
    try:
        repo, packages = load(args.root)
        if args.command == "validate":
            print("Configuration valid: {} packages, {} enabled, {} targets".format(
                len(packages), sum(p["enabled"] for p in packages), len(repo["targets"])))
        elif args.command == "plan":
            print(json.dumps(plan(repo, packages, args.channel, args.include_disabled), indent=2))
        elif args.command == "build":
            from .build import build
            build(args.root, args.apk.resolve(), args.output)
        elif args.command == "sign":
            from .publish import sign
            sign(args.root, args.apk.resolve(), args.input, args.output, args.key)
        elif args.command == "verify":
            from .publish import verify
            verify(args.root, args.apk.resolve(), args.site)
    except (ConfigError, ValueError, OSError, KeyError, subprocess.SubprocessError, urllib.error.URLError) as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
