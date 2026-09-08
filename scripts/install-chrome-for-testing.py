#!/usr/bin/env python3
"""Install an official Chrome for Testing build into this checkout.

The browser is intentionally not vendored in git and is never downloaded as a
side effect of starting the MCP server. Run this script explicitly when a
project-local browser is desired.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


MANIFEST_URL = (
    "https://googlechromelabs.github.io/chrome-for-testing/"
    "last-known-good-versions-with-downloads.json"
)
CHANNELS = ("Stable", "Beta", "Dev", "Canary")
ALLOWED_DOWNLOAD_HOSTS = {
    "storage.googleapis.com",
    "chrome-for-testing-public.storage.googleapis.com",
}
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 50_000


def _cft_platform() -> str:
    machine = platform.machine().lower()
    if sys.platform != "win32":
        raise RuntimeError("This installer is for Windows only")
    if machine in {"amd64", "x86_64"}:
        return "win64"
    if machine in {"x86", "i386", "i686"}:
        return "win32"
    raise RuntimeError(f"Unsupported Windows architecture: {machine or 'unknown'}")


def _binary_relative(cft_platform: str) -> Path:
    if cft_platform.startswith("win"):
        return Path("chrome.exe")
    raise RuntimeError(f"Unsupported Chrome for Testing platform: {cft_platform}")


def _validate_download_url(url: str) -> None:
    """Allow only the HTTPS Google storage hosts used by the CfT manifest."""
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("Chrome for Testing download URL is malformed") from exc
    if (
        parsed.scheme.lower() != "https"
        or host not in ALLOWED_DOWNLOAD_HOSTS
        or port is not None
        or not parsed.path
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise RuntimeError("Chrome for Testing download URL is not an allowed HTTPS Google URL")


def _select_download(payload: dict, channel: str, cft_platform: str) -> tuple[str, str]:
    try:
        entry = payload["channels"][channel]
        version = str(entry["version"])
        downloads = entry["downloads"]["chrome"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("Chrome for Testing manifest has an unexpected format") from exc

    for item in downloads:
        if isinstance(item, dict) and item.get("platform") == cft_platform and item.get("url"):
            url = str(item["url"])
            _validate_download_url(url)
            return version, url
    raise RuntimeError(f"No Chrome for Testing download for {channel}/{cft_platform}")


def _read_manifest(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "chrome-web-mcp-cft-installer"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read Chrome for Testing manifest: {exc}") from exc


def _safe_extract(archive: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise RuntimeError("Downloaded Chrome archive contains too many files")
            total_size = 0
            seen_targets: set[str] = set()
            for member in members:
                target = (destination / member.filename).resolve()
                if not target.is_relative_to(destination_resolved):
                    raise RuntimeError("Downloaded Chrome archive contains an unsafe path")
                target_key = str(target).casefold()
                if target_key in seen_targets:
                    raise RuntimeError("Downloaded Chrome archive contains duplicate paths")
                seen_targets.add(target_key)
                mode = (member.external_attr >> 16) & 0o170000
                if stat.S_ISLNK(mode):
                    raise RuntimeError("Downloaded Chrome archive contains a symbolic link")
                total_size += max(0, member.file_size)
                if total_size > MAX_EXTRACTED_BYTES:
                    raise RuntimeError("Downloaded Chrome archive is too large after extraction")
            bad_member = bundle.testzip()
            if bad_member:
                raise RuntimeError(
                    f"Downloaded Chrome archive failed ZIP integrity check: {bad_member}"
                )
            bundle.extractall(destination)
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"Downloaded Chrome archive is not a valid ZIP file: {exc}") from exc


def _download_archive(url: str, archive: Path) -> None:
    _validate_download_url(url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "chrome-web-mcp-cft-installer"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            declared = response.headers.get("Content-Length")
            if declared:
                try:
                    if int(declared) > MAX_DOWNLOAD_BYTES:
                        raise RuntimeError("Chrome for Testing archive is larger than the 1 GiB limit")
                except ValueError as exc:
                    raise RuntimeError("Chrome for Testing server returned an invalid size") from exc
            downloaded = 0
            with archive.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > MAX_DOWNLOAD_BYTES:
                        raise RuntimeError("Chrome for Testing archive is larger than the 1 GiB limit")
                    output.write(chunk)
    except RuntimeError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Could not download Chrome for Testing: {exc}") from exc


def _remove_path(path: Path, *, ignore_errors: bool = False) -> None:
    """Remove a staged directory or file without following a symlink target."""
    try:
        if path.is_symlink() or not path.is_dir():
            path.unlink(missing_ok=True)
        else:
            shutil.rmtree(path)
    except OSError:
        if not ignore_errors:
            raise


def _replace_install(staged_root: Path, install_dir: Path) -> None:
    """Replace an existing install only after the new archive passed validation."""
    backup_parent = Path(
        tempfile.mkdtemp(prefix=f".{install_dir.name}-backup-", dir=install_dir.parent)
    )
    backup_dir = backup_parent / install_dir.name
    moved_old = False
    new_move_started = False
    try:
        if install_dir.exists():
            shutil.move(str(install_dir), str(backup_dir))
            moved_old = True
        new_move_started = True
        shutil.move(str(staged_root), str(install_dir))
        if moved_old:
            _remove_path(backup_dir, ignore_errors=True)
    except OSError as exc:
        if new_move_started and install_dir.exists():
            _remove_path(install_dir, ignore_errors=True)
        if new_move_started and install_dir.exists():
            raise RuntimeError(
                "Could not replace the installed Chrome build and could not remove "
                f"the partial new directory; previous build is preserved at {backup_dir}"
            ) from exc
        if moved_old and backup_dir.exists():
            try:
                shutil.move(str(backup_dir), str(install_dir))
            except OSError as restore_exc:
                raise RuntimeError(
                    "Could not replace Chrome and could not restore the previous build; "
                    f"previous build is preserved at {backup_dir}"
                ) from restore_exc
        raise RuntimeError(f"Could not replace the installed Chrome build: {exc}") from exc
    finally:
        if not backup_dir.exists():
            _remove_path(backup_parent, ignore_errors=True)


def install(project_root: Path, channel: str) -> Path:
    project_root = project_root.resolve()
    local_root = project_root / ".local-chrome"
    local_root.mkdir(parents=True, exist_ok=True)

    cft_platform = _cft_platform()
    payload = _read_manifest(MANIFEST_URL)
    version, download_url = _select_download(payload, channel, cft_platform)

    install_dir = local_root / f"chrome-{cft_platform}"
    binary_rel = _binary_relative(cft_platform)
    binary = install_dir / binary_rel
    version_file = install_dir / ".cft-version"

    if binary.is_file() and version_file.is_file():
        try:
            installed_version = version_file.read_text(encoding="utf-8").strip()
        except OSError:
            installed_version = ""
        if installed_version == version:
            print(f"Chrome for Testing {version} is already installed: {binary}")
            return binary

    with tempfile.TemporaryDirectory(prefix="cft-install-", dir=local_root) as tmp_name:
        tmp = Path(tmp_name)
        archive = tmp / "chrome.zip"
        _download_archive(download_url, archive)

        extracted = tmp / "extracted"
        extracted.mkdir()
        _safe_extract(archive, extracted)

        extracted_root = extracted / f"chrome-{cft_platform}"
        extracted_binary = extracted_root / binary_rel
        if not extracted_binary.is_file():
            raise RuntimeError("Downloaded Chrome archive did not contain the expected executable")
        (extracted_root / ".cft-version").write_text(version + "\n", encoding="utf-8")
        _replace_install(extracted_root, install_dir)

    binary = install_dir / binary_rel
    print(f"Installed Chrome for Testing {version}: {binary}")
    return binary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--channel",
        choices=CHANNELS,
        default="Stable",
        help="Chrome for Testing channel (default: Stable)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="chrome-web-mcp checkout root (default: repository containing this script)",
    )
    args = parser.parse_args()
    try:
        install(args.root, args.channel)
    except (RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
