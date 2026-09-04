"""Lock and orphan-sweep behavior tests for the chrome-web server.

Verifies the foolproofing added so multiple Hermes sessions can no longer wedge
each other over a shared Chrome profile:
  - a second instance contending for the SAME profile gets a diagnostic error
    naming the live holder (not a bare "owns this profile");
  - a holder that dies (fd released by the kernel) lets the next acquirer
    through after the short retry window;
  - the startup orphan sweep removes per-pid profile dirs whose owner pid is
    dead and keeps ones whose owner is alive.
Run: .venv/bin/pytest tests/test_locking.py -v
"""

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import fcntl
import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER_PY = ROOT / "src" / "chrome_web_mcp" / "server.py"
SPEC = importlib.util.spec_from_file_location("chrome_web_server", SERVER_PY)
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)

BrowserRuntime = server.BrowserRuntime


def test_per_session_profile_default_uses_pid():
    # Without CW_PROFILE_DIR/CW_LOCK_PATH the default profile dir must be per-pid.
    saved = {k: os.environ.pop(k) for k in ("CW_PROFILE_DIR", "CW_LOCK_PATH") if k in os.environ}
    try:
        spec = importlib.util.spec_from_file_location(
            "chrome_web_server_fresh", SERVER_PY
        )
        fresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fresh)
        assert str(os.getpid()) in str(fresh.PROFILE_DIR)
        assert fresh.LOCK_PATH == fresh.PROFILE_DIR / ".instance.lock"
    finally:
        os.environ.update(saved)


def test_live_holder_yields_diagnostic_error(tmp_path, monkeypatch):
    lock_path = tmp_path / ".instance.lock"
    monkeypatch.setattr(server, "LOCK_PATH", lock_path)
    # A live holder: this process takes the flock and records our (alive) pid.
    holder = lock_path.open("a+")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    holder.seek(0)
    holder.truncate()
    holder.write(f"pid={os.getpid()} start={time.time():.6f}\n")
    holder.flush()

    rt = BrowserRuntime()
    with pytest.raises(RuntimeError) as exc:
        rt._acquire_lock()
    message = str(exc.value)
    assert str(lock_path) in message
    assert "ALIVE" in message
    assert str(os.getpid()) in message
    holder.close()


def test_dead_holder_lock_is_reacquired(tmp_path, monkeypatch):
    lock_path = tmp_path / ".instance.lock"
    monkeypatch.setattr(server, "LOCK_PATH", lock_path)
    lock_path.touch()
    # A dead holder: record a pid that no longer exists and hold the flock in a
    # subprocess that we kill (the kernel releases the flock with the fd).
    dead_pid_holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl,os,sys,time;"
                "f=os.fdopen(os.open(sys.argv[1], os.O_RDWR), 'w');"
                "fcntl.flock(f, fcntl.LOCK_EX);"
                "f.write(f'pid={sys.argv[2]} start=0\\n');f.flush();"
                "print('held', flush=True);"
                "time.sleep(60)"
            ),
            str(lock_path),
            "99999999",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert "held" in dead_pid_holder.stdout.readline()
    dead_pid_holder.kill()
    dead_pid_holder.wait()
    # flock is released with the dead process's fd.

    rt = BrowserRuntime()
    rt._acquire_lock()
    try:
        assert rt.lock_file is not None
        # Our own pid must now be recorded as holder.
        content = lock_path.read_text(encoding="utf-8")
        assert f"pid={os.getpid()}" in content
    finally:
        rt.cleanup()


def test_sweep_orphans_removes_dead_keeps_alive(tmp_path, monkeypatch):
    base = tmp_path / "chrome-web-v2-profile"
    base.mkdir()
    dead_dir = base / "1234567"  # an unlikely-live pid
    dead_dir.mkdir()
    (dead_dir / "marker").write_text("x")
    alive_dir = base / str(os.getpid())
    alive_dir.mkdir()

    # _sweep_orphans hardcodes tempfile.gettempdir(); point it at our tmp_path.
    monkeypatch.setattr(server.tempfile, "gettempdir", lambda: str(tmp_path))
    BrowserRuntime._sweep_orphans()
    assert not dead_dir.exists(), "dead-owner profile dir should be removed"
    assert alive_dir.exists(), "alive-owner profile dir must be kept"
