"""Deterministic tests for the Windows browser/process platform boundary."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time

import pytest

from chrome_web_mcp import platform_runtime, server

SYNTHETIC_SECRET = "sk-" + ("a" * 26)


def test_platform_detection():
    assert platform_runtime.platform_key() == "windows"


def test_windows_chrome_discovery(tmp_path, monkeypatch):
    chrome = tmp_path / "chrome.exe"
    chrome.write_bytes(b"MZ")
    monkeypatch.setattr(platform_runtime, "platform_key", lambda: "windows")
    monkeypatch.setenv("CW_CHROME", str(chrome))
    assert platform_runtime.discover_chrome() == str(chrome)


def test_windows_chrome_candidate_priority(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_runtime, "platform_key", lambda: "windows")
    monkeypatch.delenv("CW_CHROME", raising=False)
    local = tmp_path / ".local-chrome"
    bundled = local / "chrome-win64" / "chrome.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"MZ")
    monkeypatch.setattr(platform_runtime, "LOCAL_CHROME_DIR", local)
    monkeypatch.setattr(platform_runtime.shutil, "which", lambda name: None)
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "pf"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "pf86"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    candidates = platform_runtime.chrome_candidates()
    assert candidates[0] == str(bundled)


def test_stable_chrome_precedes_beta_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_runtime, "platform_key", lambda: "windows")
    stable = tmp_path / "pf" / "Google" / "Chrome" / "Application" / "chrome.exe"
    beta = tmp_path / "pf" / "Google" / "Chrome Beta" / "Application" / "chrome.exe"
    stable.parent.mkdir(parents=True)
    beta.parent.mkdir(parents=True)
    stable.write_bytes(b"MZ")
    beta.write_bytes(b"MZ")
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "pf"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "pf86"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    monkeypatch.setattr(platform_runtime.shutil, "which", lambda name: None)
    candidates = platform_runtime._windows_chrome_paths()
    assert candidates.index(str(stable)) < candidates.index(str(beta))


def test_cw_chrome_still_beats_bundled_cft(tmp_path, monkeypatch):
    override = tmp_path / "override-chrome.exe"
    override.write_bytes(b"MZ")
    monkeypatch.setattr(platform_runtime, "platform_key", lambda: "windows")
    monkeypatch.setenv("CW_CHROME", str(override))
    monkeypatch.setattr(platform_runtime, "LOCAL_CHROME_DIR", tmp_path / ".local-chrome")
    assert platform_runtime.chrome_candidates() == [str(override)]
    assert platform_runtime.discover_chrome() == str(override)


def test_windows_visible_backend(monkeypatch):
    monkeypatch.setattr(server.platform_runtime, "platform_key", lambda: "windows")
    monkeypatch.delenv("CW_DISPLAY_MODE", raising=False)
    config = dict(server._CONFIG_DEFAULTS)
    config["show_browser"] = True
    monkeypatch.setattr(server, "CONFIG", config)
    assert server.BrowserRuntime().display_mode == "native"


def test_windows_hidden_backend(monkeypatch):
    monkeypatch.setattr(server.platform_runtime, "platform_key", lambda: "windows")
    monkeypatch.delenv("CW_DISPLAY_MODE", raising=False)
    config = dict(server._CONFIG_DEFAULTS)
    config["show_browser"] = False
    monkeypatch.setattr(server, "CONFIG", config)
    assert server.BrowserRuntime().display_mode == "hidden"


def test_process_identity():
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        **platform_runtime.popen_kwargs(),
    )
    try:
        first = platform_runtime.process_identity(child.pid)
        second = platform_runtime.process_identity(child.pid)
        assert first == second
        assert first["pid"] == child.pid
        assert "create_time" in first
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_process_cleanup():
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        **platform_runtime.popen_kwargs(),
    )
    server._terminate_owned_process(child, 2)
    assert child.poll() is not None


def test_profile_isolation():
    if os.environ.get("CW_PROFILE_DIR") or os.environ.get("CW_LOCK_PATH"):
        pytest.skip("profile path overrides are set")
    assert str(os.getpid()) in str(server.PROFILE_DIR)
    assert server.LOCK_PATH == server.PROFILE_DIR / ".instance.lock"


class _FakeProxy:
    url = "http://127.0.0.1:12345"


@pytest.mark.parametrize(
    "mode,expects_headless,expects_hidden",
    [("native", False, False), ("hidden", False, True), ("headless", True, False)],
)
def test_chrome_command_uses_native_windows_modes(monkeypatch, mode, expects_headless, expects_hidden):
    monkeypatch.setattr(server.platform_runtime, "platform_key", lambda: "windows")
    monkeypatch.setattr(server.BrowserRuntime, "_chrome_executable", staticmethod(lambda: r"C:\Chrome\chrome.exe"))
    runtime = server.BrowserRuntime()
    runtime.display_mode = mode
    runtime.proxy = _FakeProxy()
    command = runtime._chrome_command()
    assert not any(flag.startswith("--ozone-platform=") for flag in command)
    assert "--disable-gpu" not in command
    assert "--disable-dev-shm-usage" not in command
    assert ("--headless=new" in command) is expects_headless
    assert ("--window-position=-32000,-32000" in command) is expects_hidden


def test_chrome_command_keeps_chrome_sandbox(monkeypatch):
    monkeypatch.setattr(server.platform_runtime, "platform_key", lambda: "windows")
    monkeypatch.setattr(
        server.BrowserRuntime,
        "_chrome_executable",
        staticmethod(lambda: r"C:\Chrome\chrome.exe"),
    )
    runtime = server.BrowserRuntime()
    runtime.proxy = _FakeProxy()
    assert "--no-sandbox" not in runtime._chrome_command()


def test_windows_job_object_failure_is_not_silent(monkeypatch):
    closed = []

    class _FailingJob:
        def assign(self, pid):
            raise OSError("permission denied")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(server.platform_runtime, "platform_key", lambda: "windows")
    monkeypatch.setattr(server.platform_runtime, "WindowsJob", _FailingJob)
    runtime = server.BrowserRuntime()
    with pytest.raises(RuntimeError, match="untracked browser"):
        runtime._assign_windows_job(1234)
    assert runtime.job is None
    assert closed == [True]


def test_windows_window_hider_repeats_until_closed(monkeypatch):
    calls = []
    monkeypatch.setattr(platform_runtime, "hide_process_windows", lambda pid: calls.append(pid))
    hider = platform_runtime.WindowsWindowHider(4321, interval=0.05)
    hider.start()
    time.sleep(0.18)
    hider.close()
    assert len(calls) >= 2
    assert set(calls) == {4321}


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "https://user:pass@example.com/",
        f"https://example.com/?key={SYNTHETIC_SECRET}",
    ],
)
def test_public_url_security(url):
    with pytest.raises(ValueError, match="Blocked"):
        server._validate_public_url(url)


def test_mcp_tools():
    tools = asyncio.run(server.list_tools())
    assert sorted(tool.name for tool in tools) == ["fetch_url", "google_search", "health_check"]


def test_health_check_windows(monkeypatch):
    monkeypatch.setattr(server.platform_runtime, "platform_description", lambda: "Windows test (AMD64)")
    monkeypatch.setattr(server.BrowserRuntime, "_chrome_executable", staticmethod(lambda: r"C:\Chrome\chrome.exe"))
    monkeypatch.setattr(server.platform_runtime, "chrome_version", lambda binary: "Google Chrome 999.0")
    monkeypatch.setattr(server._RUNTIME, "display_mode", "hidden")
    payload = json.loads(asyncio.run(server.call_tool("health_check", {}))[0].text)
    assert payload["success"] is True
    data = payload["data"]
    assert data["platform"] == "Windows test (AMD64)"
    assert data["chrome_binary"] == r"C:\Chrome\chrome.exe"
    assert data["chrome_version"] == "Google Chrome 999.0"
    assert data["display_mode"] == "hidden"
    assert data["windows_job_attached"] is False


def test_windows_stealth_does_not_spoof_intel_gpu(monkeypatch):
    script = server.BrowserRuntime._stealth_init_js()
    assert "Intel Iris OpenGL Engine" not in script
    assert "webdriver" in script


def test_default_config_path_uses_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert platform_runtime.default_config_path() == tmp_path / "chrome-web-mcp" / "config.json"


def test_hard_kill_removes_chrome_with_windows_job_object(tmp_path):
    """A hard-killed MCP process must not leave its Chrome process behind."""
    import psutil

    chrome = platform_runtime.discover_chrome()
    profile = tmp_path / "profile"
    ready = tmp_path / "ready"
    env = os.environ.copy()
    env.update(
        {
            "CW_CHROME": chrome,
            "CW_PROFILE_DIR": str(profile),
            "CW_LOCK_PATH": str(tmp_path / "instance.lock"),
            "CW_RATE_LIMIT_DB": str(tmp_path / "rate-limit.sqlite3"),
            "CW_DISPLAY_MODE": "headless",
            "CW_READY_FILE": str(ready),
        }
    )
    code = (
        "import os, time\n"
        "from pathlib import Path\n"
        "from chrome_web_mcp import server\n"
        "server._RUNTIME.ensure()\n"
        "Path(os.environ['CW_READY_FILE']).write_text('ready', encoding='utf-8')\n"
        "while True: time.sleep(1)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(300):
            if ready.is_file():
                break
            if child.poll() is not None:
                stderr = child.stderr.read() if child.stderr is not None else ""
                pytest.fail(
                    f"browser child exited before ready: {child.returncode}; stderr={stderr[:2000]}"
                )
            time.sleep(0.1)
        else:
            pytest.fail("browser child did not become ready within 30 seconds")
        child.kill()
        child.wait(timeout=10)
        time.sleep(1)
        profile_text = str(profile)
        leftovers = []
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = proc.info.get("cmdline") or []
            except psutil.Error:
                continue
            if profile_text in cmdline or any(
                profile_text in part for part in cmdline if isinstance(part, str)
            ):
                leftovers.append((proc.pid, cmdline))
        assert leftovers == []
    finally:
        if child.poll() is None:
            child.kill()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
