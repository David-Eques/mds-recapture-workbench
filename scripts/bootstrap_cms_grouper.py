"""Fetch and verify the official CMS PDPM V2.4000 runtime package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "cms-grouper.lock.json"
DEFAULT_DESTINATION = ROOT / ".cache" / "cms-grouper" / "v2.4000"
ALLOWED_HOSTS = {"cms.gov", "www.cms.gov"}


class CmsRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = 3

    def redirect_request(self, request, fp, code, message, headers, new_url):  # noqa: ANN001, ANN201
        _assert_cms_url(new_url)
        return super().redirect_request(request, fp, code, message, headers, new_url)


def _assert_cms_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
        raise RuntimeError("CMS artifact URL or redirect is outside the approved HTTPS hosts")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_lock() -> dict[str, Any]:
    return dict(json.loads(LOCK_PATH.read_text(encoding="utf-8")))


def _download(url: str, destination: Path) -> None:
    _assert_cms_url(url)
    opener = urllib.request.build_opener(CmsRedirectHandler())
    request = urllib.request.Request(url, headers={"User-Agent": "mds-recapture-workbench/0.2"})
    with opener.open(request, timeout=60) as response, destination.open("wb") as output:
        _assert_cms_url(response.geturl())
        shutil.copyfileobj(response, output, length=1024 * 1024)


def _safe_member(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError("CMS archive contains an unsafe member path")


def _verify_existing(destination: Path, lock: dict[str, Any]) -> bool:
    for member in lock["members"]:
        path = destination / member["output_path"]
        if not path.is_file() or _sha256(path) != member["sha256"]:
            return False
    return all((destination / notice["output_path"]).is_file() for notice in lock["notices"])


def bootstrap(destination: Path = DEFAULT_DESTINATION) -> Path:
    lock = _load_lock()
    if destination.exists():
        if _verify_existing(destination, lock):
            return destination
        raise RuntimeError("existing CMS destination failed verification; remove it before retrying")

    destination.parent.mkdir(parents=True, exist_ok=True)
    archive_fd, archive_name = tempfile.mkstemp(prefix="cms-pdpm-", suffix=".zip")
    os.close(archive_fd)
    archive = Path(archive_name)
    staging = Path(tempfile.mkdtemp(prefix="cms-pdpm-stage-", dir=destination.parent))
    try:
        _download(str(lock["source_url"]), archive)
        if _sha256(archive) != lock["archive_sha256"]:
            raise RuntimeError("CMS archive SHA-256 mismatch")
        selected = [*lock["members"], *lock["notices"]]
        with zipfile.ZipFile(archive) as package:
            names = set(package.namelist())
            for entry in selected:
                source = str(entry["archive_path"])
                _safe_member(source)
                if source not in names:
                    raise RuntimeError("CMS archive is missing a locked member")
                target = staging / str(entry["output_path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(source) as input_file, target.open("wb") as output_file:
                    shutil.copyfileobj(input_file, output_file)
        for member in lock["members"]:
            if _sha256(staging / member["output_path"]) != member["sha256"]:
                raise RuntimeError("extracted CMS JAR SHA-256 mismatch")
        os.replace(staging, destination)
        return destination
    finally:
        archive.unlink(missing_ok=True)
        if staging.exists():
            shutil.rmtree(staging)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()
    installed = bootstrap(args.destination.resolve())
    print(installed)  # noqa: T201 - CLI output is the verified non-sensitive destination


if __name__ == "__main__":
    main()
