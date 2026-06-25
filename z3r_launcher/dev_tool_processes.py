from __future__ import annotations

import os
import signal
import subprocess
import sys
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
    interrupt_pid(process.pid)
    if wait_for_process(process, timeout):
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
    interrupt_pid(pid)
    if wait_for_release(pid, timeout, released):
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


def stop_port_listeners(port: int, timeout: float, released: Callable[[], bool] | None = None) -> None:
    for pid in listening_pids(port):
        if pid != os.getpid():
            stop_pid(pid, timeout, released)
            if released and released():
                return


def listening_pids(port: int) -> list[int]:
    if os.name == "nt":
        return windows_listening_pids(port)
    if sys.platform.startswith("linux"):
        return linux_listening_pids(port)
    return command_listening_pids(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"])


def linux_listening_pids(port: int) -> list[int]:
    inodes = linux_listening_socket_inodes(port)
    if not inodes:
        return []
    pids: set[int] = set()
    for process_dir in Path("/proc").iterdir():
        if process_dir.name.isdigit() and linux_process_has_socket(process_dir, inodes):
            pids.add(int(process_dir.name))
    return sorted(pids)


def linux_listening_socket_inodes(port: int) -> set[str]:
    inodes: set[str] = set()
    for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            lines = table.read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            columns = line.split()
            if len(columns) > 9 and columns[3] == "0A" and socket_row_uses_port(columns[1], port):
                inodes.add(columns[9])
    return inodes


def linux_process_has_socket(process_dir: Path, inodes: set[str]) -> bool:
    fd_dir = process_dir / "fd"
    try:
        entries = list(fd_dir.iterdir())
    except OSError:
        return False
    for fd in entries:
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target.startswith("socket:[") and target.removeprefix("socket:[").removesuffix("]") in inodes:
            return True
    return False


def socket_row_uses_port(local_address: str, port: int) -> bool:
    try:
        return int(local_address.rsplit(":", 1)[1], 16) == port
    except (IndexError, ValueError):
        return False


def windows_listening_pids(port: int) -> list[int]:
    output = command_output(["netstat", "-ano", "-p", "TCP"])
    pids: set[int] = set()
    for line in output.splitlines():
        columns = line.split()
        if len(columns) >= 5 and columns[-2].upper() == "LISTENING" and address_uses_port(columns[-4], port):
            try:
                pids.add(int(columns[-1]))
            except ValueError:
                continue
    return sorted(pids)


def address_uses_port(address: str, port: int) -> bool:
    try:
        return int(address.rsplit(":", 1)[1]) == port
    except (IndexError, ValueError):
        return False


def command_listening_pids(command: list[str]) -> list[int]:
    output = command_output(command)
    pids = []
    for line in output.splitlines():
        try:
            pids.append(int(line.strip()))
        except ValueError:
            continue
    return sorted(set(pids))


def command_output(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            **hidden_subprocess_kwargs(),
        )
    except OSError:
        return ""
    return completed.stdout if completed.returncode == 0 else ""


def interrupt_pid(pid: int) -> None:
    if os.name == "nt":
        windows_signal_pid(pid, getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
        return
    signal_pid(pid, signal.SIGINT)


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


def windows_signal_pid(pid: int, sig: signal.Signals) -> None:
    try:
        os.kill(pid, sig)
    except (LookupError, OSError, ValueError):
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
