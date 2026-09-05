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
import signal
import subprocess
import sys
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

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self._id += 1
            msg["id"] = self._id
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        return msg

    def _next(self):
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError(f"server closed stdout: {self.proc.stderr.read()[:300]}")
        return json.loads(line)

    def rpc(self, method, params=None, timeout=120):
        want = self.send(method, params)
        want_id = want["id"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self._next()
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
    concurrently-running live chrome-web-v2 server (default profile) is not
    counted.
    """
    profile = str(SANDBOX / "profile")
    r = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True)
    out = []
    for line in r.stdout.splitlines():
        if f"--user-data-dir={profile}" in line and "remote-debugging" in line:
            out.append(line)
    return out


def test_sigterm_idle_exits_fast():
    c = McpClient()
    c.handshake()
    t0 = time.monotonic()
    c.proc.send_signal(signal.SIGTERM)
    code = c.proc.wait(timeout=10)
    dt = time.monotonic() - t0
    c.proc.stderr.read()
    assert code == 0
    assert dt < 5, f"idle SIGTERM took {dt:.1f}s"
    assert find_orphans() == []


def test_sigterm_with_browser_exits_fast_and_no_orphans():
    c = McpClient()
    c.handshake()
    tools = c.rpc("tools/list")
    names = sorted(t["name"] for t in tools["tools"])
    assert names == ["fetch_url", "google_search", "health_check"]
    res = c.rpc("tools/call", {"name": "google_search", "arguments": {"query": "Hermes Agent", "limit": 2}})
    payload = json.loads(res["content"][0]["text"])
    assert payload["success"] is True
    assert len(payload["data"]["web"]) == 2
    # fetch_url: render a public page and expect readable text + final URL.
    res2 = c.rpc("tools/call", {"name": "fetch_url", "arguments": {"url": "https://example.com", "char_limit": 2000}})
    payload2 = json.loads(res2["content"][0]["text"])
    assert payload2["success"] is True, payload2
    assert payload2["data"]["title"], "expected a page title"
    assert len(payload2["data"]["text"]) > 20, "expected non-trivial rendered text"
    assert payload2["data"]["url"].startswith("https://"), payload2["data"]["url"]
    # browser is now up
    assert find_orphans(), "browser should be running after a search"
    t0 = time.monotonic()
    c.proc.send_signal(signal.SIGTERM)
    code = c.proc.wait(timeout=25)
    dt = time.monotonic() - t0
    err = c.proc.stderr.read()[:400]
    assert code == 0, f"exit={code} stderr={err}"
    assert dt < 15, f"browser SIGTERM took {dt:.1f}s"
    time.sleep(1)
    assert find_orphans() == []
