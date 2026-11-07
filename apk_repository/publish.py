"""Sign a complete feed, publish its public metadata, and verify it without executing packages."""

import html
import json
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from .apk import check_tool, index, metadata, run, validate_package
from .build import write_json
from .config import load, read_json, require
from .upstream import (
    package_version,
    resolve_release,
    select_asset,
    sha256,
)


def child(root, relative):
    require(isinstance(relative, str) and not Path(relative).is_absolute()
            and ".." not in PurePosixPath(relative).parts, "expected safe relative artifact path")
    path = root / relative
    require(root in path.resolve().parents and not path.is_symlink(), "artifact escapes site")
    require(path.is_file(), "missing artifact: " + relative)
    return path


def check_records(repo, packages, manifest, site, apk, keys=None):
    require(manifest.get("schema_version") == 1, "unsupported manifest")
    require(manifest["apk_tools"] == repo["apk_tools"] and manifest["base_url"] == repo["base_url"], "manifest/configuration mismatch")
    expected = {(c, t["arch"], p["name"]): (p, t) for c in repo["channels"] for t in repo["targets"]
                for p in packages if c in p["channels"] and t["arch"] in p["architectures"]}
    seen = set()
    for record in manifest["packages"]:
        identity = record["channel"], record["arch"], record["name"]
        require(identity in expected and identity not in seen, "unexpected/duplicate package in manifest")
        seen.add(identity)
        pkg, target = expected[identity]
        require(record["source"] == pkg["source"] and record["abi"] == target["abi"], "wrong package provenance")
        pattern = pkg["architectures"][record["arch"]]["asset_pattern"]
        version = package_version(record["release_tag"], pattern, record["asset_name"],
                                  record["version"])
        require(record["version"] == version, "wrong APK version")
        if record["channel"] == "stable":
            require(not record["upstream_prerelease"] and "_" not in version, "prerelease in stable channel")
        expected_file = "apk/{}/{}/{}-{}.apk".format(*identity[:2], record["name"], record["version"])
        require(record["file"] == expected_file, "noncanonical APK filename")
        path = child(site, record["file"])
        require(sha256(path) == record["sha256"], "APK artifact digest mismatch")
        if keys is None:
            require(record["sha256"] == record["asset_sha256"], "upstream APK was modified before signing")
        validate_package(apk, path, pkg, record["version"], pkg["architectures"][record["arch"]]["package_arch"], keys)
    require(seen == set(expected), "manifest is missing configured packages")
    return seen


def check_upstream_assets(packages, manifest, site):
    """Recheck unsigned APKs against GitHub, independently of the build manifest."""
    configured = {pkg["name"]: pkg for pkg in packages}
    requests = dict.fromkeys((configured[r["name"]]["source"], r["release_tag"], r["channel"])
                             for r in manifest["packages"])
    with ThreadPoolExecutor(max_workers=8) as pool:
        pending = {k: pool.submit(resolve_release, k[0], k[1], channel=k[2]) for k in requests}
        releases = {key: future.result() for key, future in pending.items()}
    for record in manifest["packages"]:
        pkg = configured[record["name"]]
        key = (pkg["source"], record["release_tag"], record["channel"])
        release, commit = releases[key]
        require(record["release_id"] == release["id"]
                and record["source_commit"] == commit
                and record["upstream_prerelease"] == release["prerelease"],
                "upstream Release changed since build")
        pattern = pkg["architectures"][record["arch"]]["asset_pattern"]
        asset = select_asset(release, pkg["source"], pattern)
        digest = asset["digest"].split(":", 1)[1]
        require(record["asset_id"] == asset["id"] and record["asset_name"] == asset["name"]
                and record["asset_sha256"] == digest and sha256(child(site, record["file"])) == digest,
                "upstream APK does not match GitHub Release asset")


def render_site(repo, site, manifest):
    def escape(value, quote=False):
        return html.escape(str(value), quote=quote)

    groups = []
    for channel, target in ((c, t) for c in repo["channels"] for t in repo["targets"]):
        arch = target["arch"]
        feed = f"apk/{channel}/{arch}/"
        links = [f'<li><a href="{feed}packages.adb">packages.adb</a></li>',
                 f'<li><a href="{feed}manifest.json">manifest.json</a></li>']
        records = sorted((r for r in manifest["packages"] if r["arch"] == arch and r["channel"] == channel), key=lambda r: r["name"])
        for record in records:
            filename = PurePosixPath(record["file"]).name
            links.append('<li><a href="{}">{}</a></li>'.format(
                escape(record["file"], quote=True), escape(filename)))
        groups.append(f'<h2>{escape(channel)} / {escape(arch)}</h2><ul>{"".join(links)}</ul>')
    page = '''<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>apk-repository</title>
<style>
body{font:16px/1.6 system-ui,sans-serif;max-width:960px;margin:40px auto;padding:0 16px;overflow-wrap:anywhere}
</style>
<h1>apk-repository</h1>
<p>OpenWrt 25.12 · stable / latest</p>
<p><a href="apk/apk-repository.pem">仓库公钥</a> · <a href="manifest.json">发布清单</a> · <a href="SHA256SUMS">SHA-256</a> · <a href="SHA256SUMS.sig">清单签名</a> · <a href="install.txt">安装说明</a></p>
<p>公钥 SHA-256：<code>FINGERPRINT</code></p>
PACKAGES
</html>
'''
    (site / "index.html").write_text(page.replace("FINGERPRINT", escape(manifest["key_sha256"]))
                                     .replace("PACKAGES", "\n".join(groups)))
    (site / ".nojekyll").touch()
    instructions = ["apk-repository — OpenWrt 25.12 / stable / latest\n",
                    "公钥 SHA-256：" + manifest["key_sha256"] + "\n",
                    "先从可信渠道核对公钥指纹，再安装公钥并添加对应架构的软件源。\n",
                    repo["base_url"] + "/apk/apk-repository.pem\n"]
    for channel, target in ((c, t) for c in repo["channels"] for t in repo["targets"]):
        instructions.append(f"{channel} / {target['arch']}: {repo['base_url']}/apk/{channel}/{target['arch']}/packages.adb\n")
    (site / "install.txt").write_text("".join(instructions))


def sign(root, apk, unsigned, output, private_key):
    repo, packages = load(root)
    check_tool(apk, repo["apk_tools"]["version"])
    unsigned, output, private_key = Path(unsigned).resolve(), Path(output).resolve(), Path(private_key).resolve()
    require(private_key.is_file() and not private_key.stat().st_mode & 0o077, "private key must exist with mode 0600")
    require(not output.exists(), "output already exists; use a fresh directory")
    require(unsigned != private_key and unsigned not in private_key.parents, "private key cannot be inside unsigned artifacts")
    require(all(not p.is_symlink() for p in unsigned.rglob("*")), "symlinks in build artifacts")
    original = read_json(unsigned / "manifest.json")
    check_records(repo, packages, original, unsigned, apk)
    check_upstream_assets(packages, original, unsigned)
    expected_apks = {record["file"] for record in original["packages"]}
    require({p.relative_to(unsigned).as_posix() for p in unsigned.rglob("*.apk")} == expected_apks, "unlisted APK in build artifacts")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="apk-sign-", dir=output.parent) as temporary:
        work = Path(temporary)
        site = work / "site"
        # Copy only explicitly referenced input artifacts, never arbitrary build output.
        site.mkdir()
        for record in original["packages"]:
            relative = record["file"]
            destination = site / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(child(unsigned, relative), destination)
        public = site / "apk/apk-repository.pem"
        run(["openssl", "pkey", "-in", private_key, "-pubout", "-out", public])
        public.chmod(0o644)
        manifest = original
        manifest["key_sha256"] = sha256(public)
        signed = {}
        for record in manifest["packages"]:
            path = child(site, record["file"])
            digest = record["asset_sha256"]
            if digest in signed:
                source, signed_digest = signed[digest]
                shutil.copyfile(source, path)
                record["sha256"] = signed_digest
            else:
                before = metadata(apk, path)
                run([apk, "adbsign", "--allow-untrusted", "--sign-key", private_key, path])
                require(metadata(apk, path) == before, "signing changed package metadata")
                record["sha256"] = sha256(path)
                signed[digest] = path, record["sha256"]
        manifest["indexes"] = []
        for channel, target in ((c, t) for c in repo["channels"] for t in repo["targets"]):
            feed = site / "apk" / channel / target["arch"]
            index(apk, feed, private_key)
            path = feed / "packages.adb"
            records = [r for r in manifest["packages"] if r["arch"] == target["arch"] and r["channel"] == channel]
            write_json(feed / "manifest.json", {"channel": channel, "arch": target["arch"], "packages": records})
            manifest["indexes"].append({"file": path.relative_to(site).as_posix(), "sha256": sha256(path)})
        write_json(site / "manifest.json", manifest)
        render_site(repo, site, manifest)
        checksum_file = site / "SHA256SUMS"
        checksum_file.write_text("".join(f"{sha256(p)}  {p.relative_to(site).as_posix()}\n"
                                         for p in sorted(site.rglob("*")) if p.is_file()))
        run(["openssl", "dgst", "-sha256", "-sign", private_key, "-out", site / "SHA256SUMS.sig", checksum_file])
        site.rename(output)
    print("Public key SHA-256: " + manifest["key_sha256"])


def verify(root, apk, site):
    repo, packages = load(root)
    check_tool(apk, repo["apk_tools"]["version"])
    site = Path(site).resolve()
    require(all(not p.is_symlink() for p in site.rglob("*")), "symlinks in site")
    public = child(site, "apk/apk-repository.pem")
    run(["openssl", "dgst", "-sha256", "-verify", public, "-signature", child(site, "SHA256SUMS.sig"), child(site, "SHA256SUMS")])
    listed = set()
    for line in (site / "SHA256SUMS").read_text().splitlines():
        digest, relative = line.split("  ", 1)
        require(relative not in listed and sha256(child(site, relative)) == digest, "site checksum mismatch")
        listed.add(relative)
    actual = {p.relative_to(site).as_posix() for p in site.rglob("*") if p.is_file()}
    require(actual == listed | {"SHA256SUMS", "SHA256SUMS.sig"}, "unlisted file in signed site")
    manifest = read_json(site / "manifest.json")
    require(sha256(public) == manifest["key_sha256"], "wrong public key fingerprint")
    with tempfile.TemporaryDirectory(prefix="apk-verify-") as temporary:
        keys = Path(temporary)
        shutil.copyfile(public, keys / public.name)
        check_records(repo, packages, manifest, site, apk, keys)
        expected_apks = {r["file"] for r in manifest["packages"]}
        require({p.relative_to(site).as_posix() for p in site.rglob("*.apk")} == expected_apks, "unexpected or historical APK")
        expected_indexes = {f"apk/{c}/{t['arch']}/packages.adb" for c in repo["channels"] for t in repo["targets"]}
        require({r["file"] for r in manifest["indexes"]} == expected_indexes, "missing feed index")
        for record in manifest["indexes"]:
            path = child(site, record["file"])
            require(sha256(path) == record["sha256"], "artifact checksum mismatch")
            run([apk, "verify", "--keys-dir", keys, path])
        for relative in expected_indexes:
            data = json.loads(run([apk, "adbdump", "--format", "json", site / relative], capture_output=True).stdout)
            expected = {(r["name"], r["version"]) for r in manifest["packages"]
                        if Path(r["file"]).parent == Path(relative).parent}
            entries = data.get("packages", [])
            require(len(entries) == len(expected) and {(p["name"], p["version"]) for p in entries} == expected,
                    "index contains missing, duplicate or historical packages")
    print("Signed site verified: only current stable/latest packages are present.")
