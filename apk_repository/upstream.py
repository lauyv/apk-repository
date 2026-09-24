"""Resolve immutable GitHub Release inputs and download verified assets."""

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .config import require

MAX_DOWNLOAD = 512 * 1024 * 1024
VERSION = re.compile(r"v?(\d+\.\d+\.\d+)(?:-(alpha|beta|rc)[.-]?(\d+))?")


def version_parts(tag):
    match = VERSION.fullmatch(tag)
    require(match is not None, "unsupported upstream version: " + tag)
    base, suffix, number = match.groups()
    upstream = tag.removeprefix("v")
    apk = base + ("_" + suffix + number if suffix else "")
    return upstream, apk


def package_version(tag, pattern, asset_name, revision, observed_version=None):
    upstream, apk = version_parts(tag)
    expected = pattern.replace("{version}", upstream)
    if revision is None:
        if "{revision}" in expected:
            expression = re.escape(expected).replace(r"\{revision\}", r"(0|[1-9][0-9]*)")
            match = re.fullmatch(expression, asset_name)
            require(match is not None, "asset name does not match configured revision pattern")
            revision = int(match.group(1))
        else:
            require(asset_name == expected, "asset name does not match configured pattern")
            require(isinstance(observed_version, str)
                    and re.fullmatch(re.escape(apk) + r"-r(0|[1-9][0-9]*)", observed_version),
                    "APK metadata version does not match Release tag")
            return observed_version
    else:
        require(asset_name == expected, "asset name does not match configured pattern")
    result = apk + "-r" + str(revision)
    require(observed_version is None or observed_version == result, "APK metadata version mismatch")
    return result


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        require(urllib.parse.urlsplit(newurl).scheme == "https", "refusing non-HTTPS redirect")
        result = super().redirect_request(request, fp, code, msg, headers, newurl)
        # An API token must never follow a redirect to an asset storage host.
        if result and urllib.parse.urlsplit(newurl).hostname != "api.github.com":
            result.remove_header("Authorization")
        return result


def open_url(url, api=False):
    parsed = urllib.parse.urlsplit(url)
    require(parsed.scheme == "https" and not parsed.username and not parsed.password, "HTTPS URL required")
    headers = {"User-Agent": "apk-repository", "Accept": "application/vnd.github+json" if api else "*/*"}
    if api:
        require(parsed.hostname == "api.github.com", "unexpected API host")
        headers["X-GitHub-Api-Version"] = "2022-11-28"
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = "Bearer " + token
    opener = urllib.request.build_opener(HTTPSRedirect())
    for attempt in range(3):
        try:
            return opener.open(urllib.request.Request(url, headers=headers), timeout=120)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)


def api_json(path):
    for attempt in range(3):
        with open_url("https://api.github.com/" + path, api=True) as response:
            content = response.read(16 * 1024 * 1024 + 1)
        require(len(content) <= 16 * 1024 * 1024, "API response too large")
        try:
            return json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            if attempt == 2:
                raise ValueError("invalid GitHub API JSON from " + path) from exc
            time.sleep(2 ** attempt)


def download(url, destination, digest=None, size=None):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and digest and sha256(destination) == digest:
        require(size is None or destination.stat().st_size == size, "cached asset size mismatch")
        return destination
    partial = destination.with_name(destination.name + ".partial")
    try:
        total = 0
        with open_url(url) as response, partial.open("wb") as output:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                require(total <= MAX_DOWNLOAD, "download exceeds size limit")
                output.write(block)
        require(size is None or total == size, "download size mismatch")
        require(digest is None or sha256(partial) == digest, "download SHA-256 mismatch")
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def select_release(releases, channel, tag=None):
    require(channel in ("stable", "latest"), "unknown channel: " + channel)
    candidates = [r for r in releases if not r.get("draft") and r.get("published_at")
                  and (channel == "latest" or not r.get("prerelease"))
                  and (tag is None or r.get("tag_name") == tag)]
    require(candidates, "no eligible Release for " + channel + (": " + tag if tag else ""))
    selected = max(candidates, key=lambda r: (r["published_at"], r["id"]))
    version_parts(selected["tag_name"])
    if channel == "stable":
        require(VERSION.fullmatch(selected["tag_name"]).group(2) is None, "prerelease tag in stable channel")
    return selected


def resolve_tag_commit(source, tag):
    quoted = urllib.parse.quote(tag, safe="")
    reference = api_json(f"repos/{source}/git/ref/tags/{quoted}")
    target = reference["object"]
    for _ in range(5):
        sha = target["sha"]
        require(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha), "invalid upstream commit")
        if target["type"] == "commit":
            return sha
        require(target["type"] == "tag", "release tag does not point to a commit")
        target = api_json(f"repos/{source}/git/tags/{sha}")["object"]
    require(False, "release tag indirection is too deep")


def resolve_release(source, channel, tag=None):
    if tag:
        releases = [api_json("repos/{}/releases/tags/{}".format(source, urllib.parse.quote(tag, safe="")))]
    elif channel == "stable":
        releases = [api_json(f"repos/{source}/releases/latest")]
    elif channel == "latest":
        releases = []
        page = 1
        while True:
            batch = api_json(f"repos/{source}/releases?per_page=10&page={page}")
            require(isinstance(batch, list), "invalid Release API response")
            releases.extend(batch)
            if len(batch) < 10:
                break
            page += 1
    else:
        require(False, "unknown channel: " + channel)
    release = select_release(releases, channel, tag)
    commit = resolve_tag_commit(source, release["tag_name"])
    return release, commit


def select_asset(release, source, pattern):
    version, _ = version_parts(release["tag_name"])
    expected = pattern.replace("{version}", version)
    expression = re.escape(expected).replace(r"\{revision\}", r"(0|[1-9][0-9]*)")
    assets = [a for a in release["assets"] if re.fullmatch(expression, a["name"])]
    require(len(assets) == 1, "Release must contain exactly one asset matching " + expected)
    asset = assets[0]
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", asset.get("digest") or ""), "asset has no GitHub SHA-256 digest")
    require(type(asset["size"]) is int and 0 < asset["size"] <= MAX_DOWNLOAD, "invalid asset size")
    url = urllib.parse.urlsplit(asset["browser_download_url"])
    require(url.scheme == "https" and url.hostname == "github.com"
            and urllib.parse.unquote(url.path) == "/{}/releases/download/{}/{}".format(source, release["tag_name"], asset["name"]),
            "asset URL does not belong to the configured repository and Release")
    return asset
