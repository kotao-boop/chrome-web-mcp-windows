#!/usr/bin/env python3
"""Focused DS4-derived Google search + URL fetch MCP server.

Runs a normal Chrome window inside Xvfb, renders pages with JavaScript through
CDP, and exposes a stateless Google search tool plus a public-URL fetch tool
(JS-rendered readable text). Fetch targets and their post-redirect final URLs
are validated fail-closed; arbitrary page-context JavaScript is never exposed.
"""

from __future__ import annotations

import asyncio
import atexit
import fcntl
import ipaddress
import itertools
import json
import os
import random
import re
import select
import shutil
import signal
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
import mcp.types as types
import websockets
from mcp.server import Server
from mcp.server.stdio import stdio_server

HERE = Path(__file__).resolve().parent
# Each MCP server process (one per Hermes session) gets its OWN throwaway Chrome
# profile under /tmp, so sessions never contend for a shared profile: a live
# second session can no longer wedge this one with "owns this profile". The
# shared on-disk profile (HERE/chrome-profile) is still supported via the
# CW_PROFILE_DIR override, for environments that intentionally share one.
# The instance lock (flock) still guards whichever profile directory is in use;
# its holder pid/starttime are embedded so a DEAD holder's stale lock can be
# recovered automatically instead of blocking forever.
PROFILE_DIR = (
    Path(os.environ["CW_PROFILE_DIR"])
    if os.environ.get("CW_PROFILE_DIR")
    else Path(tempfile.gettempdir()) / "chrome-web-v2-profile" / str(os.getpid())
)
LOCK_PATH = Path(os.environ["CW_LOCK_PATH"]) if os.environ.get("CW_LOCK_PATH") else PROFILE_DIR / ".instance.lock"
DEVTOOLS_FILE = PROFILE_DIR / "DevToolsActivePort"
MAX_CDP_MESSAGE = 2 * 1024 * 1024

app = Server("chrome-web")

_SECRET_RE = re.compile(
    r"(?:sk-|ghp_|github_pat_|xox[baprs]-|AIza|hf_|pplx-|tvly-|exa_|xai-)[A-Za-z0-9_-]{10,}",
    re.IGNORECASE,
)
_BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "metadata.goog"}
_CDP_IDS = itertools.count(1)


class CaptchaRequired(RuntimeError):
    """The Google page requires a human interaction before search can continue."""


def _is_google_challenge(url: str, text: str) -> bool:
    """Detect Google's challenge page without treating ordinary result text as one."""
    parsed = urllib.parse.urlparse(url)
    if not _is_google_host(parsed.hostname):
        return False
    path = parsed.path.lower()
    if path == "/sorry" or path.startswith("/sorry/"):
        return True
    normalized = " ".join(text.lower().split())
    return any(
        phrase in normalized
        for phrase in (
            "our systems have detected unusual traffic",
            "unusual traffic from your computer network",
            "automated queries",
            "not a robot",
            "captcha",
        )
    )


def _is_google_host(host: str | None) -> bool:
    normalized = (host or "").lower().rstrip(".")
    return normalized == "google.com" or normalized.endswith(".google.com")


def _validate_public_url(url: str) -> str:
    """Return a public HTTP(S) URL or raise a fail-closed validation error."""
    if _SECRET_RE.search(url) or _SECRET_RE.search(urllib.parse.unquote(url)):
        raise ValueError("Blocked: URL contains a credential-like value")
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError as exc:
        raise ValueError("Blocked: malformed URL") from exc
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise ValueError("Blocked: only public HTTP(S) URLs without credentials are allowed")
    if host in _BLOCKED_HOSTS or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("Blocked: local or metadata hostname")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
            }
        except OSError as exc:
            raise ValueError("Blocked: hostname could not be resolved safely") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Blocked: URL resolves to a non-public address")
    return urllib.parse.urlunparse(parsed)


def _normalize_candidate_href(href: str) -> str | None:
    """Normalize direct and legacy Google result links without fetching them."""
    try:
        parsed = urllib.parse.urlparse(href)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if _is_google_host(parsed.hostname) and parsed.path == "/url":
        target = urllib.parse.parse_qs(parsed.query).get("q", [None])[0]
        return _normalize_candidate_href(target) if target else None
    return href


async def _resolve_candidate_url(href: str) -> str | None:
    """Resolve Google wrappers without following a redirect to the destination."""
    current = _normalize_candidate_href(href)
    if not current:
        return None
    for _ in range(3):
        parsed = urllib.parse.urlparse(current)
        if not _is_google_host(parsed.hostname):
            return _validate_public_url(current)
        if parsed.path == "/url":
            current = _normalize_candidate_href(current)
            if not current:
                return None
            continue
        if parsed.path != "/goto":
            return None
        # The only fetched host is Google. The external Location is validated but
        # is never requested here, preventing redirect-based SSRF.
        async with httpx.AsyncClient(follow_redirects=False, timeout=8.0) as client:
            response = await client.get(
                current,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/150 Safari/537.36"},
            )
        location = response.headers.get("location")
        if not location or response.status_code not in {301, 302, 303, 307, 308}:
            return None
        current = urllib.parse.urljoin(current, location)
    return None


async def _build_results(
    candidates: list[dict],
    limit: int,
    *,
    resolve_url: Callable[[str], Awaitable[str | None]],
) -> list[dict]:
    results: list[dict] = []
    seen: set[str] = set()
    for candidate in candidates:
        title = " ".join(str(candidate.get("title", "")).split())
        href = str(candidate.get("href", ""))
        description = " ".join(str(candidate.get("description", "")).split())
        if not title or not href:
            continue
        try:
            url = await resolve_url(href)
        except (ValueError, OSError, httpx.HTTPError):
            continue
        if not url or url in seen:
            continue
        seen.add(url)
        results.append(
            {
                "title": title[:300],
                "url": url,
                "description": description[:1000],
                "position": len(results) + 1,
            }
        )
        if len(results) >= limit:
            break
    return results


def _terminate_owned_process(proc: subprocess.Popen | None, timeout: float) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=timeout)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass


class BrowserRuntime:
    """Own one isolated Xvfb/Chrome pair for this MCP process."""

    def __init__(self) -> None:
        self.xvfb: subprocess.Popen | None = None
        self.xephyr: subprocess.Popen | None = None
        self.chrome: subprocess.Popen | None = None
        self.lock_file: Any = None
        self.display: str | None = None
        self.port: int | None = None
        self.browser_ws: str | None = None
        self.xpra_server: subprocess.Popen | None = None
        self.xpra_client: subprocess.Popen | None = None
        self.user_display = os.environ.get("DISPLAY")
        # CW_DISPLAY_MODE=xvfb (default): Chrome on a private Xvfb display,
        # fully hidden, never steals focus.
        # CW_DISPLAY_MODE=xephyr: Chrome on a nested Xephyr window living on
        # the user's desktop. The user can see/minimize it, but Chrome inside
        # can never pop a window to the front outside of it.
        self.display_mode = os.environ.get("CW_DISPLAY_MODE", "xvfb").strip().lower()
        # A long-lived background search tab, reused across queries so we do not
        # repeatedly open/close targets (which looks like bot activity to Google).
        self.search_target_id: str | None = None
        # Separate long-lived tab for arbitrary URL fetches, so a fetch does not
        # steal the Google search tab mid-query.
        self.fetch_target_id: str | None = None

    # Fingerprint hardening, injected into every new document before scripts run.
    _STEALTH_INIT_JS = r"""
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    window.chrome = window.chrome || { runtime: {} };
    Object.defineProperty(navigator, 'languages', {get: () => ['ja-JP', 'ja', 'en-US', 'en']});
    Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
    const _qp = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (p) {
      if (p === 37445) return 'Intel Inc.';
      if (p === 37446) return 'Intel Iris OpenGL Engine';
      return _qp.call(this, p);
    };
    """

    @staticmethod
    def _browser_environment(display: str) -> dict[str, str]:
        """Force Chrome onto the private Xvfb display, never the user Wayland session."""
        env = os.environ.copy()
        env["DISPLAY"] = display
        env["XDG_SESSION_TYPE"] = "x11"
        for key in ("WAYLAND_DISPLAY", "WAYLAND_SOCKET"):
            env.pop(key, None)
        return env

    def _human_display_environment(self) -> dict[str, str]:
        """Prepare an X11 client environment for the user's desktop display."""
        env = os.environ.copy()
        env["DISPLAY"] = self.user_display or ""
        env["XDG_SESSION_TYPE"] = "x11"
        for key in ("WAYLAND_DISPLAY", "WAYLAND_SOCKET"):
            env.pop(key, None)
        return env

    def hide_for_human(self) -> None:
        """Stop the temporary Xpra shadow server and its visible client."""
        _terminate_owned_process(self.xpra_client, 3)
        _terminate_owned_process(self.xpra_server, 3)
        self.xpra_client = None
        self.xpra_server = None

    def expose_for_human(self) -> None:
        """Attach the private Xvfb display to the user's desktop via Xpra."""
        if not self.display:
            raise RuntimeError("CAPTCHA browser display is not ready")
        if not self.user_display:
            raise RuntimeError("CAPTCHA display is unavailable: user DISPLAY is not set")
        if self.xpra_client and self.xpra_client.poll() is None:
            return
        xpra = shutil.which("xpra")
        if not xpra:
            raise RuntimeError("Xpra is not installed; install the xpra package to solve CAPTCHA")
        self.hide_for_human()
        server_env = self._browser_environment(self.display)
        self.xpra_server = subprocess.Popen(
            [
                xpra,
                "shadow",
                self.display,
                "--daemon=no",
                "--mdns=no",
                "--notifications=no",
                "--bell=no",
                "--system-tray=no",
            ],
            env=server_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        client_env = self._human_display_environment()
        ready = False
        for _ in range(40):
            if self.xpra_server.poll() is not None:
                break
            status = subprocess.run(
                [xpra, "list"],
                env=client_env,
                capture_output=True,
                text=True,
                timeout=2,
            )
            if self.display in status.stdout:
                ready = True
                break
            time.sleep(0.25)
        if not ready:
            self.hide_for_human()
            raise RuntimeError("Xpra shadow session did not become ready")
        self.xpra_client = subprocess.Popen(
            [
                xpra,
                "attach",
                self.display,
                "--opengl=no",
                "--clipboard=no",
                "--notifications=no",
                "--bell=no",
                "--system-tray=no",
            ],
            env=client_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(0.25)
        if self.xpra_client.poll() is not None:
            self.hide_for_human()
            raise RuntimeError("Xpra attach client exited before showing the browser")

    @staticmethod
    def _chrome_executable() -> str:
        configured = os.environ.get("CW_CHROME")
        candidates = [configured] if configured else []
        candidates += [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]
        for candidate in candidates:
            if candidate and os.access(candidate, os.X_OK):
                return candidate
        raise RuntimeError("No supported Chrome/Chromium executable found")

    def _acquire_lock(self) -> None:
        """Acquire the profile lock, embedding our pid/starttime for forensics.

        On contention we wait a few seconds (a dying holder releases its fd
        asynchronously) and then raise an error that names the live holder, so
        a real conflict with another running session is diagnosable at a glance
        instead of a bare 'owns this profile'.
        """
        if self.lock_file is not None:
            return
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        while True:
            lock_file = LOCK_PATH.open("a+")
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock_file.close()
                if time.monotonic() - start > 4.0:
                    raise RuntimeError(
                        "Another chrome-web MCP instance owns this profile "
                        f"({LOCK_PATH}). {self._holder_diagnosis()} A live second "
                        "session is expected to hold its own lock independently; "
                        "if this persists, check for a stale chrome-web-v2 server "
                        "process (ps aux | grep chrome-web-v2/server.py)."
                    ) from None
                time.sleep(0.25)
                continue
            # Record who holds the lock (best-effort, never fatal).
            try:
                lock_file.seek(0)
                lock_file.truncate()
                lock_file.write(f"pid={os.getpid()} start={time.time():.6f}\n")
                lock_file.flush()
            except OSError:
                pass
            self.lock_file = lock_file
            return

    @staticmethod
    def _holder_diagnosis() -> str:
        """Read the embedded holder pid from the lock file and report its state."""
        try:
            content = LOCK_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            return "No holder recorded in the lock file."
        pid = None
        for field in content.split():
            if field.startswith("pid="):
                try:
                    pid = int(field.split("=", 1)[1])
                except ValueError:
                    pid = None
        if pid is None:
            return f"Lock content: {content[:80]!r} (no pid recorded)."
        stat_path = Path(f"/proc/{pid}/stat")
        try:
            stat = stat_path.read_text(encoding="utf-8")
            comm = stat[stat.rfind(")") + 2 :].split()
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[0].decode(
                "utf-8", "replace"
            )
            return (
                f"Holder pid {pid} is ALIVE ({cmdline[:120] or comm[0] if comm else 'unknown'}); "
                "it owns the lock legitimately and will release it on exit."
            )
        except OSError:
            return (
                f"Holder pid {pid} is DEAD — the flock released automatically; "
                "this instance is acquiring the freed lock."
            )

    @staticmethod
    def _sweep_orphans() -> None:
        """Kill orphaned Chrome/Xvfb and remove per-session profile dirs whose
        owning MCP server process is dead (crash / kill -9 leftovers)."""
        base = Path(tempfile.gettempdir()) / "chrome-web-v2-profile"
        if not base.is_dir():
            return
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            try:
                pid = int(entry.name)
            except ValueError:
                continue
            if pid == os.getpid():
                continue
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                # Owner dead: sweep orphaned browsers pointing at this profile.
                try:
                    subprocess.run(
                        ["pkill", "-f", f"--user-data-dir={entry}"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
                try:
                    shutil.rmtree(entry, ignore_errors=True)
                except OSError:
                    pass
            except OSError:
                # EPERM: owner alive (or a kernel pid we cannot signal) — keep it.
                pass

    def _start_xvfb(self) -> str:
        proc = subprocess.Popen(
            ["Xvfb", "-displayfd", "1", "-screen", "0", "1365x900x24", "-nolisten", "tcp"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        self.xvfb = proc
        assert proc.stdout is not None
        ready, _, _ = select.select([proc.stdout], [], [], 10)
        if not ready:
            raise RuntimeError("Xvfb did not allocate a display within 10 seconds")
        number = proc.stdout.readline().strip()
        if not number.isdigit():
            raise RuntimeError("Xvfb returned an invalid display number")
        display = f":{number}"
        for _ in range(30):
            check = subprocess.run(
                ["xdpyinfo", "-display", display],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            if check.returncode == 0:
                return display
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        raise RuntimeError("Xvfb failed its readiness check")

    def _start_xephyr(self, host_display: str) -> str:
        """Start a nested Xephyr window on the user's desktop and return its display.

        Xephyr is a plain X client: it appears as one ordinary window the user
        can minimize, move to another workspace, or close. Chrome runs *inside*
        it, so tool calls can never pop a window to the front of the desktop
        the way running Chrome directly on DISPLAY would.
        """
        env = self._human_display_environment()
        env["DISPLAY"] = host_display
        proc = subprocess.Popen(
            [
                "Xephyr",
                "-displayfd",
                "1",
                "-screen",
                "1365x900x24",
                "-title",
                "chrome-web-mcp",
                "-nolisten",
                "tcp",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        self.xephyr = proc
        assert proc.stdout is not None
        ready, _, _ = select.select([proc.stdout], [], [], 10)
        if not ready:
            raise RuntimeError("Xephyr did not allocate a display within 10 seconds")
        number = proc.stdout.readline().strip()
        if not number.isdigit():
            raise RuntimeError("Xephyr returned an invalid display number")
        display = f":{number}"
        for _ in range(30):
            check = subprocess.run(
                ["xdpyinfo", "-display", display],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            if check.returncode == 0:
                self._keep_nested_window_behind()
                return display
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        raise RuntimeError("Xephyr failed its readiness check")

    @staticmethod
    def _keep_nested_window_behind() -> None:
        """Best-effort: start the Xephyr window minimized.

        A newly mapped window is raised by the window manager by default, and
        lowering races with the mapping, so the window kept popping to the
        front. Minimizing is deterministic: the window sits in the dock/task
        bar, never steals focus, and the user opens it on demand. Later
        navigations reuse the same tab and never raise anything.
        """
        try:
            time.sleep(0.5)
            subprocess.run(
                ["xdotool", "search", "--name", "chrome-web-mcp", "windowminimize"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    def _start_chrome(self, display: str) -> tuple[int, str]:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        DEVTOOLS_FILE.unlink(missing_ok=True)
        env = self._browser_environment(display)
        command = [
            self._chrome_executable(),
            "--ozone-platform=x11",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            f"--user-data-dir={PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "--password-store=basic",
            "--disable-gpu",
            "--mute-audio",
            "--disable-blink-features=AutomationControlled",
            "--lang=ja-JP",
            "--window-size=1365,900",
            "--window-position=0,0",
            "about:blank",
        ]
        if os.geteuid() == 0:
            command.insert(1, "--no-sandbox")
        proc = subprocess.Popen(
            command,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.chrome = proc
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"Chrome exited before CDP was ready ({proc.returncode})")
            try:
                lines = DEVTOOLS_FILE.read_text(encoding="utf-8").splitlines()
                port = int(lines[0])
                path = lines[1]
                return port, f"ws://127.0.0.1:{port}{path}"
            except (FileNotFoundError, IndexError, ValueError):
                time.sleep(0.1)
        raise RuntimeError("Chrome did not expose CDP within 20 seconds")

    def ensure(self) -> str:
        if self.chrome and self.chrome.poll() is None and self.browser_ws:
            return self.browser_ws
        self.cleanup()
        self._acquire_lock()
        try:
            if self.display_mode == "xephyr":
                if not self.user_display:
                    raise RuntimeError(
                        "CW_DISPLAY_MODE=xephyr needs a user DISPLAY, but none is set"
                    )
                self.display = self._start_xephyr(self.user_display)
            else:
                self.display = self._start_xvfb()
            self.port, self.browser_ws = self._start_chrome(self.display)
            return self.browser_ws
        except Exception:
            self.cleanup()
            raise

    def cleanup(self) -> None:
        self.hide_for_human()
        _terminate_owned_process(self.chrome, 5)
        _terminate_owned_process(self.xephyr, 3)
        _terminate_owned_process(self.xvfb, 3)
        self.chrome = None
        self.xephyr = None
        self.xvfb = None
        self.display = None
        self.port = None
        self.browser_ws = None
        DEVTOOLS_FILE.unlink(missing_ok=True)
        if self.lock_file is not None:
            try:
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                self.lock_file.close()
                self.lock_file = None


class SharedSearchRateLimiter:
    """Reserve globally spaced Google-search start slots across processes."""

    _BUCKET = "google_search"

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        min_delay: float = 1.0,
        max_delay: float = 2.5,
    ) -> None:
        if min_delay < 0 or max_delay < min_delay:
            raise ValueError("invalid search rate-limit delay range")
        default_path = Path(tempfile.gettempdir()) / "chrome-web-mcp" / "search-rate-limit.sqlite3"
        configured = os.environ.get("CW_RATE_LIMIT_DB")
        self.db_path = Path(db_path or configured or default_path)
        self.min_delay = min_delay
        self.max_delay = max_delay

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection = sqlite3.connect(self.db_path, timeout=10.0, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS rate_limit ("
            "bucket TEXT PRIMARY KEY, next_at REAL NOT NULL)"
        )
        return connection

    def reserve_slot(self) -> float:
        """Atomically reserve a slot and return seconds to wait before starting."""
        now = time.time()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT next_at FROM rate_limit WHERE bucket = ?", (self._BUCKET,)
            ).fetchone()
            previous = float(row[0]) if row else now
            slot = max(now, previous)
            next_at = slot + random.uniform(self.min_delay, self.max_delay)
            connection.execute(
                "INSERT INTO rate_limit(bucket, next_at) VALUES(?, ?) "
                "ON CONFLICT(bucket) DO UPDATE SET next_at=excluded.next_at",
                (self._BUCKET, next_at),
            )
            connection.commit()
            return max(0.0, slot - now)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


_RUNTIME = BrowserRuntime()
# Reap crash/kill -9 leftovers from previous sessions (dead per-pid profile
# dirs and their orphaned Chrome/Xvfb) before this session needs a browser.
BrowserRuntime._sweep_orphans()

# Serialize searches within this process and reserve a shared inter-process slot.
_SEARCH_LOCK = asyncio.Lock()
_SEARCH_LIMITER = SharedSearchRateLimiter()
# Serialize fetches within this process: _fetch_page reuses a single long-lived
# fetch tab (separate from the search tab), so two concurrent fetches would
# navigate the SAME target and the second URL overwrites the first before the
# first read happens (both callers then return the winner's content).
_FETCH_LOCK = asyncio.Lock()


async def _cdp_call(connection: Any, method: str, params: dict | None = None) -> dict:
    request_id = next(_CDP_IDS)
    await connection.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
    while True:
        raw = await asyncio.wait_for(connection.recv(), timeout=25)
        message = json.loads(raw)
        if message.get("id") == request_id:
            if "error" in message:
                raise RuntimeError(f"CDP {method} failed: {message['error']}")
            return message.get("result", {})


async def _evaluate(connection: Any, expression: str) -> Any:
    result = await _cdp_call(
        connection,
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
    )
    remote = result.get("result", {})
    if remote.get("subtype") == "error" or "exceptionDetails" in result:
        raise RuntimeError("JavaScript evaluation failed")
    return remote.get("value")


async def _wait_ready(connection: Any) -> None:
    deadline = time.monotonic() + 25
    stable = 0
    previous = -1
    while time.monotonic() < deadline:
        try:
            state = await _evaluate(
                connection,
                "JSON.stringify({ready:document.readyState,href:location.href,n:(document.body?.innerText||'').length})",
            )
            parsed = json.loads(state)
            size = int(parsed.get("n", 0))
            stable = stable + 1 if size == previous and size > 0 else 0
            previous = size
            if parsed.get("ready") in {"interactive", "complete"} and stable >= 1:
                return
        except (RuntimeError, ValueError, TypeError, json.JSONDecodeError):
            pass
        await asyncio.sleep(0.25)
    raise TimeoutError("Google page did not finish rendering")


async def _click_google_consent(connection: Any) -> bool:
    clicked = await _evaluate(
        connection,
        """(() => {
          const patterns=[/accept all/i,/i agree/i,/すべて同意/i];
          for(const el of document.querySelectorAll('button,[role=button],input[type=submit]')){
            const text=(el.innerText||el.value||el.textContent||'').trim();
            if(patterns.some(p=>p.test(text))){el.click();return true;}
          }
          return false;
        })()""",
    )
    return bool(clicked)


_EXTRACT_RESULTS_JS = r"""(() => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const output = [];
  for (const anchor of document.querySelectorAll('a[href]')) {
    const heading = anchor.querySelector('h3');
    if (!heading) continue;
    const title = clean(heading.innerText || heading.textContent);
    if (!title) continue;
    const block = anchor.closest('[data-hveid]') || anchor.parentElement?.parentElement || anchor.parentElement;
    const blockText = clean(block?.innerText || '');
    let description = blockText;
    if (description.startsWith(title)) description = clean(description.slice(title.length));
    output.push({title, href: anchor.href || '', description: description.slice(0, 1000)});
    if (output.length >= 40) break;
  }
  return JSON.stringify(output);
})()"""


async def _ensure_search_page(browser: Any) -> tuple[Any, str, bool]:
    """Return (page_ws, target_id, created) for the long-lived search tab.

    The target is created once per browser session and reused across searches so
    we are not constantly opening/closing targets. The browser-level socket is
    ephemeral (re-connected per search), so we identify the target by its ID and
    simply verify it is still alive before reconnecting the page socket.
    """
    # Reuse the existing target if it is still alive in this browser session.
    if _RUNTIME.search_target_id:
        try:
            alive = await _cdp_call(
                browser, "Target.getTargetInfo", {"targetId": _RUNTIME.search_target_id}
            )
            if alive.get("targetInfo", {}).get("targetId"):
                port = _RUNTIME.port
                if port:
                    page = await websockets.connect(
                        f"ws://127.0.0.1:{port}/devtools/page/{_RUNTIME.search_target_id}",
                        max_size=MAX_CDP_MESSAGE,
                    )
                    return page, _RUNTIME.search_target_id, False
        except Exception:
            pass
        _RUNTIME.search_target_id = None
    # No usable target: create one (background tab, not a new window).
    created = await _cdp_call(
        browser,
        "Target.createTarget",
        {"url": "about:blank", "background": True, "newWindow": False},
    )
    target_id = created.get("targetId")
    if not target_id or _RUNTIME.port is None:
        raise RuntimeError("Chrome did not create a search tab")
    _RUNTIME.search_target_id = target_id
    port = _RUNTIME.port
    page = await websockets.connect(
        f"ws://127.0.0.1:{port}/devtools/page/{target_id}",
        max_size=MAX_CDP_MESSAGE,
    )
    return page, target_id, True


async def _extract_google_candidates(query: str, limit: int) -> list[dict]:
    browser_ws = await asyncio.to_thread(_RUNTIME.ensure)
    browser = await websockets.connect(browser_ws, max_size=MAX_CDP_MESSAGE)
    page: Any = None
    try:
        try:
            page, _tid, _reused = await _ensure_search_page(browser)
        except Exception:
            # Browser-level connection was fine but the page socket may have been
            # dropped; fall back to a fresh target.
            page, _tid, _reused = await _ensure_search_page(browser)
        await _cdp_call(page, "Page.enable")
        await _cdp_call(page, "Runtime.enable")
        # Register fingerprint-hardening JS before any document runs. This is
        # per-DevTools-session (not per-target), so it must be (re)added on every
        # fresh page socket connection, reused tab or not.
        try:
            await _cdp_call(
                page,
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": BrowserRuntime._STEALTH_INIT_JS},
            )
        except Exception:
            pass
        search_url = "https://www.google.com/search?" + urllib.parse.urlencode(
            {"q": query, "num": min(max(limit + 5, 10), 25), "udm": "14"}
        )
        await _cdp_call(page, "Page.navigate", {"url": search_url})
        await _wait_ready(page)
        if await _click_google_consent(page):
            await asyncio.sleep(1)
            await _wait_ready(page)
        final_url = str(await _evaluate(page, "location.href"))
        if not _is_google_host(urllib.parse.urlparse(final_url).hostname):
            raise RuntimeError("Google navigation left the allowed origin")
        body_text = str(await _evaluate(page, "document.body?.innerText || ''"))
        if _is_google_challenge(final_url, body_text):
            if _xpra_expose_enabled():
                try:
                    await asyncio.to_thread(_RUNTIME.expose_for_human)
                    detail = "Xpra has shown the CAPTCHA browser window; solve it, then retry the same search."
                except Exception as exc:
                    detail = f"CAPTCHA detected, but the browser could not be shown: {exc}"
            else:
                detail = _captcha_detail_for_mode()
            raise CaptchaRequired(detail)
        raw = await _evaluate(page, _EXTRACT_RESULTS_JS)
        candidates = json.loads(raw or "[]")
        if not isinstance(candidates, list):
            raise RuntimeError("Google result extraction returned an invalid payload")
        return candidates
    finally:
        # Keep the page socket for reuse, but never leak the browser socket.
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass
        await browser.close()


def _xpra_expose_enabled() -> bool:
    """Xpra auto-attach is opt-in: it once crashed the desktop session."""
    return os.environ.get("CW_XPRA_EXPOSE", "").strip() == "1"


def _captcha_detail_for_mode() -> str:
    if _RUNTIME.display_mode == "xephyr":
        return (
            "Google CAPTCHA detected. Solve it in the chrome-web-mcp window "
            "on your desktop, then retry the same search."
        )
    return "Google CAPTCHA detected. Wait a while, then retry the same search."


async def _search_google(query: str, limit: int) -> list[dict]:
    """Run one Google search with process-wide pacing and a shared slot queue."""
    async with _SEARCH_LOCK:
        wait_for = await asyncio.to_thread(_SEARCH_LIMITER.reserve_slot)
        if wait_for:
            await asyncio.sleep(wait_for)
        candidates = await _extract_google_candidates(query, limit)
    results = await _build_results(candidates, limit, resolve_url=_resolve_candidate_url)
    if not results:
        raise RuntimeError("Google rendered no usable external search results")
    await asyncio.to_thread(_RUNTIME.hide_for_human)
    return results


# Extract clean, readable text from a rendered page (no scripts/styles/nav).
_FETCH_TEXT_JS = r"""(() => {
  const clone = document.body ? document.body.cloneNode(true) : document;
  for (const sel of ['script','style','noscript','svg','canvas','iframe','nav','footer','header','[aria-hidden="true"]']) {
    clone.querySelectorAll(sel).forEach(el => el.remove());
  }
  const title = (document.title || '').trim();
  const text = (clone.innerText || '').replace(/\u00a0/g, ' ');
  return JSON.stringify({title, text});
})()"""


async def _fetch_page(url: str, char_limit: int) -> dict:
    """Render a public URL in a dedicated tab and return its readable text.

    Uses a separate long-lived tab from the Google search tab. The navigation
    target and its post-redirect final URL are both validated as public HTTP(S)
    (fail-closed), blocking localhost/metadata/secret-bearing destinations and
    redirect-based SSRF.
    """
    # Validate the requested URL up front (fail-closed).
    validated = _validate_public_url(url)
    async with _FETCH_LOCK:
        browser_ws = await asyncio.to_thread(_RUNTIME.ensure)
        browser = await websockets.connect(browser_ws, max_size=MAX_CDP_MESSAGE)
        page: Any = None
        try:
            # Reuse or create a dedicated fetch tab.
            if _RUNTIME.fetch_target_id:
                try:
                    alive = await _cdp_call(
                        browser, "Target.getTargetInfo", {"targetId": _RUNTIME.fetch_target_id}
                    )
                    if not alive.get("targetInfo", {}).get("targetId"):
                        _RUNTIME.fetch_target_id = None
                except Exception:
                    _RUNTIME.fetch_target_id = None
            if not _RUNTIME.fetch_target_id:
                created = await _cdp_call(
                    browser,
                    "Target.createTarget",
                    {"url": "about:blank", "background": True, "newWindow": False},
                )
                _RUNTIME.fetch_target_id = created.get("targetId")
                if not _RUNTIME.fetch_target_id or _RUNTIME.port is None:
                    raise RuntimeError("Chrome did not create a fetch tab")
            page = await websockets.connect(
                f"ws://127.0.0.1:{_RUNTIME.port}/devtools/page/{_RUNTIME.fetch_target_id}",
                max_size=MAX_CDP_MESSAGE,
            )
            await _cdp_call(page, "Page.enable")
            await _cdp_call(page, "Runtime.enable")
            await _cdp_call(page, "Page.navigate", {"url": validated})
            await _wait_ready(page)
            # Re-validate the final URL after redirects (catch public->local SSRF).
            final_url = str(await _evaluate(page, "location.href"))
            _validate_public_url(final_url)
            raw = await _evaluate(page, _FETCH_TEXT_JS)
            data = json.loads(raw or "{}")
            text = " ".join(str(data.get("text", "")).split())
            title = " ".join(str(data.get("title", "")).split())
            truncated = len(text) > char_limit
            return {
                "url": final_url,
                "title": title[:300],
                "text": text[:char_limit],
                "truncated": truncated,
            }
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
            await browser.close()


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="google_search",
            description=(
                "Search Google in a JavaScript-rendering Chrome browser running "
                "non-headless inside Xvfb. Returns structured search results."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results (1-20)",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 20,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="fetch_url",
            description=(
                "Fetch a public HTTP(S) URL with JavaScript rendering (Xvfb Chrome) "
                "and return the page's readable text plus final URL and title. "
                "Use for pages that need JS to render (SPAs, paywalled-after-consent "
                "layouts, etc.). char_limit caps the returned text."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Public http(s) URL to fetch (no credentials, no localhost).",
                    },
                    "char_limit": {
                        "type": "integer",
                        "description": "Maximum characters of readable text to return (default 15000).",
                        "default": 15000,
                        "minimum": 100,
                        "maximum": 200000,
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "google_search":
            query = arguments.get("query", "")
            limit = arguments.get("limit", 5)
            if not isinstance(query, str) or not query.strip():
                raise ValueError("query is required")
            if len(query) > 512:
                raise ValueError("query is too long")
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
                raise ValueError("limit must be an integer from 1 to 20")
            results = await _search_google(query.strip(), limit)
            payload = {"success": True, "data": {"web": results}}
        elif name == "fetch_url":
            url = arguments.get("url", "")
            char_limit = arguments.get("char_limit", 15000)
            if not isinstance(url, str) or not url.strip():
                raise ValueError("url is required")
            if len(url) > 2048:
                raise ValueError("url is too long")
            if isinstance(char_limit, bool) or not isinstance(char_limit, int) or not 100 <= char_limit <= 200000:
                raise ValueError("char_limit must be an integer from 100 to 200000")
            payload = {"success": True, "data": await _fetch_page(url.strip(), char_limit)}
        else:
            raise ValueError(f"Unknown tool: {name}")
    except CaptchaRequired as exc:
        payload = {"success": False, "error": str(exc), "captcha_required": True}
    except Exception as exc:
        payload = {"success": False, "error": str(exc)}
    return [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]


async def main() -> None:
    loop = asyncio.get_running_loop()
    state: dict = {"task": None, "handling": False}

    def _teardown() -> None:
        """Synchronous best-effort teardown used from the signal callback."""
        task = state.get("task")
        if task is not None:
            task.cancel()
        _RUNTIME.cleanup()
        # The stdio transport's writer task blocks on open pipes; os._exit is
        # the deterministic way to leave without waiting on the task group.
        # All owned processes (Chrome, Xvfb) are already terminated above.
        os._exit(0)

    async def _on_stop(signum: int) -> None:
        if state["handling"]:
            return
        state["handling"] = True
        _teardown()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda s=sig: asyncio.ensure_future(_on_stop(s)))
        except NotImplementedError:
            signal.signal(sig, lambda *_: _teardown())

    async with stdio_server() as (read_stream, write_stream):
        run_task = asyncio.create_task(
            app.run(read_stream, write_stream, app.create_initialization_options())
        )
        state["task"] = run_task
        await run_task
        # run_task finishes only when the client closes stdin (EOF): exit the
        # transport context so the reader/writer tasks wind down normally.


atexit.register(_RUNTIME.cleanup)
if not os.environ.get("CW_PROFILE_DIR"):
    # Throwaway per-session profile: remove it on process exit (the startup
    # sweep also reaps these if the process is killed hard).
    atexit.register(lambda: shutil.rmtree(PROFILE_DIR, ignore_errors=True))


def run() -> None:
    """Run the MCP stdio server for console-script and python -m users."""
    try:
        asyncio.run(main())
    finally:
        _RUNTIME.cleanup()


if __name__ == "__main__":
    run()
