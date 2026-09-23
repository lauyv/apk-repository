"""Sign a complete feed, publish its public metadata, and verify it without executing packages."""

import datetime
import html
import json
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from .apk import check_tool, index, make_package, metadata, run, validate_package
from .build import write_json
from .config import load, read_json, require
from .upstream import sha256, version_parts


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
                for p in packages if p["enabled"] and c in p["channels"] and t["arch"] in p["architectures"]}
    seen = set()
    for record in manifest["packages"]:
        identity = record["channel"], record["arch"], record["name"]
        require(identity in expected and identity not in seen, "unexpected/duplicate package in manifest")
        seen.add(identity)
        pkg, target = expected[identity]
        require(record["source"] == pkg["source"] and record["abi"] == target["abi"], "wrong package provenance")
        _, version = version_parts(record["release_tag"])
        require(record["version"] == version + "-r" + str(pkg["revision"]), "wrong APK version")
        if record["channel"] == "stable":
            require(not record["upstream_prerelease"], "prerelease package in stable feed")
        elif not pkg["prerelease_fallback"]:
            require(record["upstream_prerelease"], "stable package in strict prerelease feed")
        expected_file = "apk/{}/{}/{}-{}.apk".format(*identity[:2], record["name"], record["version"])
        require(record["file"] == expected_file, "noncanonical APK filename")
        path = child(site, record["file"])
        require(sha256(path) == record["sha256"], "APK artifact digest mismatch")
        if keys is None:
            require(record["sha256"] == record["asset_sha256"], "upstream APK was modified before signing")
        require(sha256(child(site, record["source_archive"])) == record["source_sha256"], "source digest mismatch")
        validate_package(apk, path, pkg, record["version"], pkg["architectures"][record["arch"]]["package_arch"], keys)
    require(seen == set(expected), "manifest is missing configured packages")
    return seen


def bootstrap(apk, repo, site, work, channel, arch, version, license_path):
    name = "apk-repository-" + channel
    root = work / name / arch
    keys = root / "etc/apk/keys"
    keys.mkdir(parents=True)
    shutil.copyfile(site / "apk/apk-repository.pem", keys / "apk-repository.pem")
    feeds = root / "etc/apk/repositories.d"
    feeds.mkdir(parents=True)
    (feeds / "apk-repository.list").write_text("@apk_repository {}/apk/{}/{}/packages.adb\n".format(repo["base_url"], channel, arch))
    license_directory = root / "usr/share/licenses" / name
    license_directory.mkdir(parents=True)
    shutil.copyfile(license_path, license_directory / "LICENSE")
    opposite = "prerelease" if channel == "stable" else "stable"
    path = site / "apk" / channel / arch / (name + "-" + version + ".apk")
    make_package(apk, root, path, {"name": name, "version": version, "arch": arch,
        "license": "MIT", "description": f"apk-repository {channel} feed configuration",
        "depends": "!apk-repository-" + opposite})
    return {"name": name, "version": version, "channel": channel, "arch": arch,
            "file": path.relative_to(site).as_posix()}


def render_site(repo, site, manifest):
    rows = []
    for record in manifest["packages"]:
        rows.append("<tr>" + "".join("<td>" + html.escape(str(record[key])) + "</td>"
                    for key in ("channel", "arch", "name", "version"))
                    + '<td><a href="{}">APK</a> · <a href="{}">源码</a></td></tr>'.format(
                        html.escape(record["file"], quote=True), html.escape(record["source_archive"], quote=True)))
    links = "".join('<li><a href="apk/{0}/{1}/packages.adb">{0} / {1}</a></li>'.format(c, t["arch"])
                    for c in repo["channels"] for t in repo["targets"])
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>apk-repository</title>
<style>body{font:16px/1.6 system-ui,sans-serif;max-width:1000px;margin:48px auto;padding:0 24px;color:#18202b}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:10px;border-bottom:1px solid #ddd}code,pre{background:#f3f5f7;padding:4px;overflow:auto}a{color:#1859ac}</style>
<h1>apk-repository</h1><p>OpenWrt 25.12 APK 软件源。仅保留最新正式版和最新预发布版。</p>
<p><a href="apk/apk-repository.pem">仓库公钥</a> · <a href="manifest.json">发布清单</a> · <a href="SHA256SUMS">SHA-256</a> · <a href="SHA256SUMS.sig">清单签名</a> · <a href="install.txt">安装说明</a></p>
<p>公钥 SHA-256：<code>FINGERPRINT</code></p><ul>FEEDS</ul>
<table><thead><tr><th>通道</th><th>架构</th><th>包</th><th>版本</th><th>下载</th></tr></thead><tbody>ROWS</tbody></table>
<p>软件包来自其声明的上游，保留原始内容与许可证，仅追加仓库签名。</p></html>
'''
    (site / "index.html").write_text(page.replace("FINGERPRINT", manifest["key_sha256"]).replace("FEEDS", links).replace("ROWS", "".join(rows)))
    (site / ".nojekyll").touch()
    sections = ["apk-repository\n仅适用于已列出的 OpenWrt APK v3 架构。保留设备官方软件源以解析依赖。\n",
                "先从维护者或本次可信 Actions 日志确认公钥 SHA-256：" + manifest["key_sha256"] + "\n"]
    for feed in manifest["bootstrap"]:
        selected = " ".join(record["name"] + "@apk_repository" for record in manifest["packages"]
                            if record["channel"] == feed["channel"] and record["arch"] == feed["arch"])
        command = """\n{channel} / {arch}：
wget -O /tmp/apk-repository.pem '{base}/apk/apk-repository.pem'
sha256sum /tmp/apk-repository.pem
# 确认上述指纹匹配后执行：
mkdir -p /etc/apk/keys
cp /tmp/apk-repository.pem /etc/apk/keys/apk-repository.pem
wget -O /tmp/apk-repository-feed.apk '{base}/{file}'
apk add /tmp/apk-repository-feed.apk
apk update
apk add -u {selected}
""".format(base=repo["base_url"], selected=selected, **feed)
        sections.append(command)
    sections.append(r"""
移除订阅（保留应用包）：
先备份 world 并清理本仓库标签，再移除配置包和专用文件。
(
  set -eu
  cp -p /etc/apk/world /etc/apk/world.before-apk-repository-removal
  sed -i -E 's/@apk_repository([<>=~]|$)/\1/g' /etc/apk/world
  for package in apk-repository-stable apk-repository-prerelease; do
    if apk info -e "$package" >/dev/null 2>&1; then
      apk del "$package"
    fi
  done
  rm -f /etc/apk/repositories.d/apk-repository.list
  rm -f /etc/apk/keys/apk-repository.pem
)
保留应用包条目、版本约束及其他仓库标签；后续 APK 操作按剩余软件源解析版本。
仅启用一个通道。切换前完整执行上述移除步骤，再按新通道步骤安装，重新添加仓库标签。
切回稳定源不会自动降级已安装的较新预发布包。不执行全系统 apk upgrade。
""")
    (site / "install.txt").write_text("".join(sections))


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
    expected_apks = {record["file"] for record in original["packages"]}
    require({p.relative_to(unsigned).as_posix() for p in unsigned.rglob("*.apk")} == expected_apks, "unlisted APK in build artifacts")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="apk-sign-", dir=output.parent) as temporary:
        work = Path(temporary)
        site = work / "site"
        # Copy only explicitly referenced input artifacts, never arbitrary build output.
        site.mkdir()
        for record in original["packages"]:
            for relative in (record["file"], record["source_archive"], record["source_archive"].removesuffix(".tar.gz") + ".LICENSE"):
                destination = site / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(child(unsigned, relative), destination)
        public = site / "apk/apk-repository.pem"
        run(["openssl", "pkey", "-in", private_key, "-pubout", "-out", public])
        public.chmod(0o644)
        keys = work / "keys"
        keys.mkdir()
        shutil.copyfile(public, keys / public.name)
        manifest = original
        manifest["key_sha256"] = sha256(public)
        manifest["bootstrap"] = []
        version = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S") + "-r0"
        for channel in repo["channels"]:
            for target in repo["targets"]:
                manifest["bootstrap"].append(bootstrap(apk, repo, site, work, channel, target["arch"], version, Path(root) / "LICENSE"))
        for record in manifest["packages"] + manifest["bootstrap"]:
            path = child(site, record["file"])
            before = metadata(apk, path)
            run([apk, "adbsign", "--allow-untrusted", "--sign-key", private_key, path])
            require(metadata(apk, path) == before, "signing changed package metadata")
            run([apk, "verify", "--keys-dir", keys, path])
            record["sha256"] = sha256(path)
        manifest["indexes"] = []
        for channel in repo["channels"]:
            for target in repo["targets"]:
                feed = site / "apk" / channel / target["arch"]
                index(apk, feed, private_key)
                path = feed / "packages.adb"
                run([apk, "verify", "--keys-dir", keys, path])
                records = [r for r in manifest["packages"] if r["channel"] == channel and r["arch"] == target["arch"]]
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
        expected_apks = {r["file"] for r in manifest["packages"] + manifest["bootstrap"]}
        require({p.relative_to(site).as_posix() for p in site.rglob("*.apk")} == expected_apks, "unexpected or historical APK")
        expected_indexes = {"apk/{}/{}/packages.adb".format(c, t["arch"]) for c in repo["channels"] for t in repo["targets"]}
        require({r["file"] for r in manifest["indexes"]} == expected_indexes, "missing feed index")
        for record in manifest["indexes"] + manifest["bootstrap"]:
            path = child(site, record["file"])
            require(sha256(path) == record["sha256"], "artifact checksum mismatch")
            run([apk, "verify", "--keys-dir", keys, path])
        for relative in expected_indexes:
            data = json.loads(run([apk, "adbdump", "--format", "json", site / relative], capture_output=True).stdout)
            expected = {(r["name"], r["version"]) for r in manifest["packages"] + manifest["bootstrap"]
                        if Path(r["file"]).parent == Path(relative).parent}
            entries = data.get("packages", [])
            require(len(entries) == len(expected) and {(p["name"], p["version"]) for p in entries} == expected,
                    "index contains missing, duplicate or historical packages")
    print("Signed site verified: only current stable/prerelease packages are present.")
