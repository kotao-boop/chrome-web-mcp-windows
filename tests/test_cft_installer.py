"""Deterministic tests for the optional Chrome for Testing bootstrap script."""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install-chrome-for-testing.py"
SPEC = importlib.util.spec_from_file_location("chrome_web_mcp_cft_installer", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


@pytest.mark.parametrize(
    "machine,expected",
    [("AMD64", "win64"), ("x86_64", "win64"), ("x86", "win32")],
)
def test_cft_platform_mapping_windows(monkeypatch, machine, expected):
    monkeypatch.setattr(installer.sys, "platform", "win32")
    monkeypatch.setattr(installer.platform, "machine", lambda: machine)
    assert installer._cft_platform() == expected


def test_selects_requested_channel_and_platform():
    payload = {
        "channels": {
            "Stable": {
                "version": "999.1.2.3",
                "downloads": {
                    "chrome": [
                        {
                            "platform": "win64",
                            "url": "https://storage.googleapis.com/chrome-for-testing-public/win64.zip",
                        },
                        {
                            "platform": "win32",
                            "url": "https://storage.googleapis.com/chrome-for-testing-public/win32.zip",
                        },
                    ]
                },
            }
        }
    }
    assert installer._select_download(payload, "Stable", "win64") == (
        "999.1.2.3",
        "https://storage.googleapis.com/chrome-for-testing-public/win64.zip",
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://storage.googleapis.com/chrome-for-testing-public/win64.zip",
        "https://example.test/win64.zip",
        "file:///C:/Windows/win64.zip",
        "https://storage.googleapis.com:443/chrome-for-testing-public/win64.zip",
        "https://storage.googleapis.com/chrome-for-testing-public/win64.zip#fragment",
    ],
)
def test_download_url_must_be_allowed_https_google_storage(url):
    with pytest.raises(RuntimeError, match="allowed HTTPS Google URL"):
        installer._validate_download_url(url)


def test_cft_installer_is_windows_only(monkeypatch):
    monkeypatch.setattr(installer.sys, "platform", "unsupported")
    with pytest.raises(RuntimeError, match="Windows only"):
        installer._cft_platform()


def test_safe_extract_rejects_path_traversal(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape.txt", "nope")
    destination = tmp_path / "out"
    destination.mkdir()
    with pytest.raises(RuntimeError, match="unsafe path"):
        installer._safe_extract(archive, destination)
    assert not (tmp_path / "escape.txt").exists()


def test_safe_extract_rejects_duplicate_paths(tmp_path):
    archive = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("chrome-win64/chrome.exe", "first")
        bundle.writestr("chrome-win64/CHROME.EXE", "second")
    destination = tmp_path / "out"
    destination.mkdir()
    with pytest.raises(RuntimeError, match="duplicate paths"):
        installer._safe_extract(archive, destination)


def test_install_validates_then_replaces_previous_build(tmp_path, monkeypatch):
    version = {"value": "999.1.2.3"}
    monkeypatch.setattr(installer.sys, "platform", "win32")
    monkeypatch.setattr(installer.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(
        installer,
        "_read_manifest",
        lambda url: {
            "channels": {
                "Stable": {
                    "version": version["value"],
                    "downloads": {
                        "chrome": [
                            {
                                "platform": "win64",
                                "url": "https://storage.googleapis.com/chrome-for-testing-public/test.zip",
                            }
                        ]
                    },
                }
            }
        },
    )

    def fake_download(url, archive):
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("chrome-win64/chrome.exe", b"MZ")

    monkeypatch.setattr(installer, "_download_archive", fake_download)
    first = installer.install(tmp_path, "Stable")
    assert first == tmp_path / ".local-chrome" / "chrome-win64" / "chrome.exe"
    assert first.is_file()
    assert (first.parent / ".cft-version").read_text(encoding="utf-8").strip() == "999.1.2.3"

    version["value"] = "999.2.3.4"
    second = installer.install(tmp_path, "Stable")
    assert second == first
    assert (second.parent / ".cft-version").read_text(encoding="utf-8").strip() == "999.2.3.4"
