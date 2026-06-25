from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable

from .platform_paths import hidden_subprocess_kwargs


def dev_tool_subprocess_kwargs() -> dict[str, object]:
    kwargs: dict[str, object] = hidden_subprocess_kwargs()
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        group_flag = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) | group_flag
    return kwargs


def write_pid_file(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def read_pid_file(path: Path | None) -> int | None:
    if not path:
        return None
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return value if value > 0 else None


def remove_pid_file(path: Path | None) -> None:
    if not path:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def stop_process(
    process: subprocess.Popen | None,
    timeout: float,
    released: Callable[[], bool] | None = None,
) -> None:
    if not process or process.poll() is not None:
        return
    terminate_pid(process.pid)
    if wait_for_process(process, timeout):
        return
    kill_pid(process.pid)
    wait_for_process_or_release(process, timeout, released)


def stop_pid(pid: int | None, timeout: float, released: Callable[[], bool] | None = None) -> None:
    if not pid:
        return
    if released and released():
        return
    terminate_pid(pid)
    if wait_for_release(pid, timeout, released):
        return
    kill_pid(pid)
    wait_for_release(pid, timeout, released)


def wait_for_release(pid: int, timeout: float, released: Callable[[], bool] | None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_exists(pid) or (released and released()):
            return True
        time.sleep(0.05)
    return not process_exists(pid) or bool(released and released())


def wait_for_process_or_release(
    process: subprocess.Popen,
    timeout: float,
    released: Callable[[], bool] | None,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None or (released and released()):
            return True
        time.sleep(0.05)
    return process.poll() is not None or bool(released and released())


def wait_for_process(process: subprocess.Popen, timeout: float) -> bool:
    try:
        process.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return process.poll() is not None


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_pid(pid: int) -> None:
    if os.name == "nt":
        taskkill_pid(pid, force=False)
        return
    signal_pid(pid, signal.SIGTERM)


def kill_pid(pid: int) -> None:
    if os.name == "nt":
        taskkill_pid(pid, force=True)
        return
    signal_pid(pid, signal.SIGKILL)


def signal_pid(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(os.getpgid(pid), sig)
    except ProcessLookupError:
        try:
            os.killpg(pid, sig)
        except (LookupError, OSError):
            return
    except (LookupError, OSError):
        return


def taskkill_pid(pid: int, force: bool) -> None:
    args = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        args.append("/F")
    try:
        subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            **hidden_subprocess_kwargs(),
        )
    except OSError:
        return
