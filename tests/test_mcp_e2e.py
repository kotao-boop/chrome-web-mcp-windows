"""End-to-end MCP stdio lifecycle tests: handshake, tool call, SIGTERM shutdown.

These spawn the real server.py as a subprocess over stdio and assert:
  - tools/list exposes google_search and fetch_url
  - a real google_search call returns structured external results
  - a real fetch_url call returns rendered readable text
  - SIGTERM (idle and with browser running) exits fast and leaves no orphans
Run: .venv/bin/pytest tests/test_mcp_e2e.py -v
"""

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)

# Per-test sandbox so the test's Chrome/lock does not collide with a live
# chrome-web-v2 server (which holds the default profile lock).
SANDBOX = Path(os.environ.get("CW_E2E_SANDBOX", str(ROOT / ".e2e-sandbox")))
SANDBOX.mkdir(parents=True, exist_ok=True)
ENV = dict(
    os.environ,
    CW_PROFILE_DIR=str(SANDBOX / "profile"),
    CW_LOCK_PATH=str(SANDBOX / ".instance.lock"),
    CW_RATE_LIMIT_DB=str(SANDBOX / "rate-limit.sqlite3"),
)


class McpClient:
    def __init__(self):
        self.proc = subprocess.Popen(
            [str(PYTHON), "-m", "chrome_web_mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=ENV,
        )
        self._id = 0
        self._lines = queue.Queue()
        def read_lines():
            for line in self.proc.stdout:
                self._lines.put(line)
            self._lines.put(None)
        threading.Thread(target=read_lines, daemon=True).start()

    def close(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            stream.close()

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self._id += 1
            msg["id"] = self._id
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        return msg

    def _next(self, timeout):
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError("MCP response timed out") from exc
        if not line:
            raise RuntimeError("server closed stdout")
        return json.loads(line)

    def rpc(self, method, params=None, timeout=120):
        want = self.send(method, params)
        want_id = want["id"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self._next(max(0.001, deadline - time.monotonic()))
            if msg.get("id") == want_id:
                if "error" in msg:
                    raise RuntimeError(f"{method} error: {msg['error']}")
                return msg["result"]
        raise TimeoutError(f"{method} timed out")

    def handshake(self):
        self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "e2e", "version": "0"},
            },
        )
        self.send("notifications/initialized", notify=True)


def find_orphans():
    """Remote-debugging Chrome processes still alive under the test's sandbox profile.

    Matches --user-data-dir pointing at the test sandbox profile, so a
    concurrently-running live chrome-web-mcp server (default profile) is not
    counted.
    """
    import psutil

    profile = str(SANDBOX / "profile")
    out = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
        except (psutil.Error, TypeError):
            continue
        if f"--user-data-dir={profile}" in cmdline and any("remote-debugging" in part for part in cmdline):
            out.append(" ".join(cmdline))
    return out


@pytest.fixture
def client():
    c = McpClient()
    try:
        yield c
    finally:
        c.close()


def test_sigterm_idle_exits_fast(client):
    c = client
    c.handshake()
    t0 = time.monotonic()
    c.proc.send_signal(signal.SIGTERM)
    code = c.proc.wait(timeout=10)
    dt = time.monotonic() - t0
    c.proc.stderr.read()
    # Windows TerminateProcess reports 1; POSIX SIGTERM handler exits 0.
    assert code in ((0, 1) if sys.platform == "win32" else (0,))
    assert dt < 5, f"idle SIGTERM took {dt:.1f}s"
    assert find_orphans() == []


@pytest.mark.live
def test_sigterm_with_browser_exits_fast_and_no_orphans(client):
    c = client
    c.handshake()
    tools = c.rpc("tools/list")
    names = sorted(t["name"] for t in tools["tools"])
    assert names == ["fetch_url", "google_search", "health_check"]
    res = c.rpc("tools/call", {"name": "google_search", "arguments": {"query": "Hermes Agent", "limit": 2}})
    payload = json.loads(res["content"][0]["text"])
    if payload.get("captcha_required"):
        pytest.skip("Google requires human CAPTCHA; fixture still cleans up the server")
    assert payload["success"] is True
    assert len(payload["data"]["web"]) == 2
    # fetch_url: render a public page and expect readable text + final URL.
    res2 = c.rpc("tools/call", {"name": "fetch_url", "arguments": {"url": "https://example.com", "char_limit": 2000}})
    payload2 = json.loads(res2["content"][0]["text"])
    assert payload2["success"] is True, payload2
    assert payload2["data"]["title"], "expected a page title"
    assert len(payload2["data"]["markdown"]) > 20, "expected non-trivial rendered markdown"
    plain = c.rpc("tools/call", {"name": "fetch_url", "arguments": {"url": "https://example.com", "format": "text"}})
    plain_payload = json.loads(plain["content"][0]["text"])
    assert plain_payload["success"] is True, plain_payload
    assert len(plain_payload["data"]["text"]) > 20
    assert payload2["data"]["url"].startswith("https://"), payload2["data"]["url"]
    # browser is now up
    assert find_orphans(), "browser should be running after a search"
    t0 = time.monotonic()
    c.proc.send_signal(signal.SIGTERM)
    code = c.proc.wait(timeout=25)
    dt = time.monotonic() - t0
    err = c.proc.stderr.read()[:400]
    assert code in ((0, 1) if sys.platform == "win32" else (0,)), f"exit={code} stderr={err}"
    assert dt < 15, f"browser SIGTERM took {dt:.1f}s"
    time.sleep(1)
    assert find_orphans() == []
