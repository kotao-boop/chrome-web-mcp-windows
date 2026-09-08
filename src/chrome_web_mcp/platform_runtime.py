"""Windows runtime helpers for Chrome discovery, locking, and cleanup.

Browser and network policy stay in the main server. This module keeps the
Windows-specific process, profile, and native-window operations in one place.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
import psutil


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_CHROME_DIR = PROJECT_ROOT / ".local-chrome"


def running_on_windows() -> bool:
    """Return whether this process is running on the supported platform."""
    return sys.platform == "win32"


def platform_key() -> str:
    if not running_on_windows():
        raise RuntimeError("The Windows edition of chrome-web-mcp requires Windows 10/11")
    return "windows"


def platform_description() -> str:
    return f"{platform.system()} {platform.release()} ({platform.machine()})"


def browser_mode(show_browser: bool, override: str = "") -> str:
    """Map the user-facing browser setting to a native Windows mode."""
    platform_key()
    requested = override.strip().lower()
    if requested:
        if requested not in {"native", "hidden", "headless"}:
            raise ValueError(
                "CW_DISPLAY_MODE on Windows must be native, hidden, or headless"
            )
        return requested
    return "native" if show_browser else "hidden"


def default_config_path() -> Path:
    platform_key()
    appdata = os.environ.get("APPDATA", "").strip()
    root = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return root / "chrome-web-mcp" / "config.json"


def pid_is_alive(pid: int) -> bool:
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except psutil.Error:
        return True


def owns_directory(path: Path) -> bool:
    try:
        return os.access(path, os.W_OK)
    except OSError:
        return False


def same_user_process(proc: psutil.Process) -> bool:
    try:
        return proc.username() == psutil.Process().username()
    except (psutil.Error, AttributeError, OSError):
        return False


def _is_executable(path: str) -> bool:
    candidate = Path(path)
    return candidate.is_file() and candidate.suffix.lower() in {".exe", ".bat", ".cmd", ""}


def bundled_chrome_for_testing_candidates() -> list[str]:
    """Find project-local Chrome for Testing builds."""
    platform_key()
    if not LOCAL_CHROME_DIR.is_dir():
        return []
    relative_names = (
        Path("chrome-win64") / "chrome.exe",
        Path("chrome-win32") / "chrome.exe",
        Path("chrome.exe"),
    )
    candidates: list[Path] = []
    seen: set[str] = set()
    for relative in relative_names:
        direct = LOCAL_CHROME_DIR / relative
        if direct.is_file() and str(direct) not in seen:
            candidates.append(direct)
            seen.add(str(direct))
    try:
        discovered = sorted(LOCAL_CHROME_DIR.glob("**/chrome.exe"))
    except OSError:
        discovered = []
    for path in discovered:
        if str(path) not in seen:
            candidates.append(path)
            seen.add(str(path))
    return [str(path) for path in candidates]


def _windows_chrome_paths() -> list[str]:
    program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    stable_names = (
        Path("Google") / "Chrome" / "Application" / "chrome.exe",
        Path("Chromium") / "Application" / "chrome.exe",
    )
    alternate_names = (
        Path("Google") / "Chrome Beta" / "Application" / "chrome.exe",
        Path("Google") / "Chrome SxS" / "Application" / "chrome.exe",
    )
    roots = [Path(program_files), Path(program_files_x86)]
    if local_appdata:
        roots.append(Path(local_appdata))
    # Prefer the stable browser regardless of whether it is installed for all
    # users or only for the current user. Beta/Canary are useful fallbacks but
    # must not unexpectedly win over a stable Chrome installation.
    paths = [str(root / name) for root in roots for name in stable_names]
    path_names = [
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        shutil.which("chromium"),
        shutil.which("chromium.exe"),
    ]
    paths.extend(str(item) for item in path_names if item)
    paths.extend(str(root / name) for root in roots for name in alternate_names)
    return [item for item in paths if item]


def chrome_candidates() -> list[str]:
    """Return executable candidates in fail-closed preference order."""
    platform_key()
    configured = os.environ.get("CW_CHROME", "").strip()
    if configured:
        return [str(Path(configured).expanduser())]

    bundled = bundled_chrome_for_testing_candidates()
    return list(dict.fromkeys(bundled + _windows_chrome_paths()))


def discover_chrome() -> str:
    configured = os.environ.get("CW_CHROME", "").strip()
    for candidate in chrome_candidates():
        if _is_executable(candidate):
            return candidate
    if configured:
        raise RuntimeError(f"CW_CHROME is not an executable Chrome/Chromium binary: {configured}")
    raise RuntimeError("No supported Chrome/Chromium executable found on Windows")


def _looks_like_chrome_version(line: str) -> bool:
    if not line:
        return False
    lowered = line.lower()
    return any(ch.isdigit() for ch in line) and (
        "chrome" in lowered or "chromium" in lowered or line[0].isdigit()
    )


def chrome_version(binary: str) -> str:
    file_version = _windows_file_version(binary)
    if file_version:
        return file_version
    creationflags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW
    try:
        completed = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Could not query Chrome version: {exc}") from exc
    text = (completed.stdout or completed.stderr or "").strip().splitlines()
    if completed.returncode == 0 and text and _looks_like_chrome_version(text[0]):
        return text[0][:300]
    raise RuntimeError("Chrome version query failed")


def _windows_file_version(binary: str) -> str:
    try:
        import ctypes
        from ctypes import wintypes

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", wintypes.DWORD),
                ("dwStrucVersion", wintypes.DWORD),
                ("dwFileVersionMS", wintypes.DWORD),
                ("dwFileVersionLS", wintypes.DWORD),
                ("dwProductVersionMS", wintypes.DWORD),
                ("dwProductVersionLS", wintypes.DWORD),
                ("dwFileFlagsMask", wintypes.DWORD),
                ("dwFileFlags", wintypes.DWORD),
                ("dwFileOS", wintypes.DWORD),
                ("dwFileType", wintypes.DWORD),
                ("dwFileSubtype", wintypes.DWORD),
                ("dwFileDateMS", wintypes.DWORD),
                ("dwFileDateLS", wintypes.DWORD),
            ]

        version = ctypes.windll.version
        version.GetFileVersionInfoSizeW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        version.GetFileVersionInfoW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        version.GetFileVersionInfoW.restype = wintypes.BOOL
        version.VerQueryValueW.argtypes = [
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.UINT),
        ]
        version.VerQueryValueW.restype = wintypes.BOOL
        size = version.GetFileVersionInfoSizeW(binary, None)
        if not size:
            return ""
        buffer = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(binary, 0, size, buffer):
            return ""
        pointer = ctypes.c_void_p()
        length = wintypes.UINT()
        if not version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
            return ""
        info = ctypes.cast(pointer, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        major = info.dwFileVersionMS >> 16
        minor = info.dwFileVersionMS & 0xFFFF
        build = info.dwFileVersionLS >> 16
        revision = info.dwFileVersionLS & 0xFFFF
        return f"Google Chrome {major}.{minor}.{build}.{revision}"
    except Exception:
        return ""


def process_identity(pid: int) -> dict:
    """Return a same-user PID birth identity that survives PID reuse checks."""
    if pid <= 0:
        raise ValueError("Invalid process id")
    try:
        proc = psutil.Process(pid)
        if not same_user_process(proc):
            raise ValueError("Process is owned by another user")
        created = proc.create_time()
    except (psutil.NoSuchProcess, psutil.ZombieProcess) as exc:
        raise ProcessLookupError(pid) from exc
    except psutil.AccessDenied as exc:
        raise ValueError("Process identity is not accessible") from exc
    return {"pid": pid, "create_time": f"{created:.6f}"}


def process_summary(pid: int) -> tuple[bool, str]:
    """Return (alive, short command) for a same-user process."""
    try:
        proc = psutil.Process(pid)
        if not same_user_process(proc):
            return True, "another-user process"
        cmdline = proc.cmdline()
        name = cmdline[0] if cmdline else proc.name()
        return True, name[:120]
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False, ""
    except psutil.AccessDenied:
        return True, "inaccessible process"
    except psutil.Error:
        return True, "inaccessible process"


def processes_with_exact_arg(argument: str) -> list[dict]:
    """Find same-user processes carrying one exact argv token."""
    records: list[dict] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            if not same_user_process(proc):
                continue
            if argument in (proc.info.get("cmdline") or []):
                records.append(process_identity(int(proc.info["pid"])))
        except (psutil.Error, ValueError, ProcessLookupError):
            continue
    return records


def terminate_process_tree(pid: int, timeout: float) -> None:
    """Terminate a process and its descendants. Safe if the pid is already gone."""
    try:
        parent = psutil.Process(pid)
    except psutil.Error:
        return
    try:
        children = parent.children(recursive=True)
    except psutil.Error:
        # The process can disappear, or Windows can revoke access, between the
        # Process() call and this snapshot. Terminating the known parent is still
        # useful and, for a Windows Job Object, the job close is the final guard.
        children = []
    targets = [parent, *children]
    for proc in targets:
        try:
            proc.terminate()
        except psutil.Error:
            pass
    try:
        _gone, alive = psutil.wait_procs(targets, timeout=timeout)
    except psutil.Error:
        alive = []
    for proc in alive:
        try:
            proc.kill()
        except psutil.Error:
            pass


def lock_exclusive_nb(fileobj) -> None:
    """Acquire a non-blocking exclusive lock, or raise BlockingIOError."""
    import msvcrt

    fileobj.seek(0, os.SEEK_END)
    if fileobj.tell() < 1:
        fileobj.write("\n")
        fileobj.flush()
    fileobj.seek(0)
    try:
        msvcrt.locking(fileobj.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        raise BlockingIOError("profile lock is held") from exc


def unlock(fileobj) -> None:
    import msvcrt

    fileobj.seek(0)
    try:
        msvcrt.locking(fileobj.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


def write_lock_holder(fileobj, pid: int) -> None:
    line = f"pid={pid} start={time.time():.6f}\n"
    fileobj.seek(0)
    fileobj.write(line)
    fileobj.truncate(fileobj.tell())
    fileobj.flush()


def holder_record_path(lock_path: Path) -> Path:
    return lock_path.with_name(lock_path.name + ".holder")


def write_holder_record(lock_path: Path, pid: int) -> None:
    holder_record_path(lock_path).write_text(
        f"pid={pid} start={time.time():.6f}\n",
        encoding="utf-8",
    )


def read_holder_record(lock_path: Path) -> str:
    side = holder_record_path(lock_path)
    try:
        text = side.read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass
    try:
        return lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def popen_kwargs() -> dict:
    """Return Windows flags for an owned child process."""
    # Chrome is a GUI process, so CREATE_NO_WINDOW is not needed. A new process
    # group keeps the fallback termination path well-defined for helper
    # processes started by this module.
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}


class WindowsJob:
    """Kill-on-close job object so Chrome dies if the MCP process is terminated."""

    def __init__(self) -> None:
        if not running_on_windows():
            raise RuntimeError("Windows Job Objects require Windows")
        import ctypes
        from ctypes import wintypes

        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        self._kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self.handle = self._kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(_windows_error("CreateJobObjectW failed"))

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._kernel32.SetInformationJobObject(
            self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            self.close()
            raise OSError(_windows_error("SetInformationJobObject failed"))

    def assign(self, pid: int) -> None:
        if not self.handle:
            return
        import ctypes

        process = self._kernel32.OpenProcess(0x0001 | 0x0100, False, pid)  # TERMINATE | SET_QUOTA
        if not process:
            raise OSError(_windows_error(f"OpenProcess failed for pid {pid}"))
        try:
            if not self._kernel32.AssignProcessToJobObject(self.handle, process):
                raise OSError(_windows_error("AssignProcessToJobObject failed"))
        finally:
            self._kernel32.CloseHandle(process)

    def close(self) -> None:
        if not self.handle:
            return
        try:
            self._kernel32.CloseHandle(self.handle)
        except Exception:
            pass
        self.handle = None


def _windows_error(message: str) -> str:
    """Return a useful Win32 error without exposing the numeric code alone."""
    import ctypes

    error_code = ctypes.get_last_error()
    if error_code:
        return f"{message}: [{error_code}] {ctypes.FormatError(error_code)}"
    return message


def hide_process_windows(root_pid: int) -> None:
    """Hide top-level windows owned by a process tree (Windows only)."""
    if not running_on_windows():
        raise RuntimeError("Native window hiding requires Windows")
    import ctypes
    from ctypes import wintypes

    pids = {root_pid}
    try:
        pids.update(child.pid for child in psutil.Process(root_pid).children(recursive=True))
    except (psutil.Error, OSError):
        pass

    user32 = ctypes.windll.user32
    SW_HIDE = 0

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids and user32.IsWindowVisible(hwnd):
            user32.ShowWindow(hwnd, SW_HIDE)
        return True

    user32.EnumWindows(enum_proc, 0)


class WindowsWindowHider:
    """Keep a hidden Chrome process tree's top-level windows out of view.

    Chrome can create a renderer, extension, or popup window after the initial
    browser startup. A one-time EnumWindows pass misses those later windows, so
    this small daemon watcher repeats the pass until the browser is cleaned up.
    """

    def __init__(self, root_pid: int, interval: float = 0.2) -> None:
        self.root_pid = root_pid
        self.interval = max(0.05, interval)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="chrome-web-mcp-window-hider",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                hide_process_windows(self.root_pid)
            except Exception:
                # Window enumeration is best effort: process exit and desktop
                # teardown are normal races during server shutdown.
                pass
            self._stop.wait(self.interval)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
