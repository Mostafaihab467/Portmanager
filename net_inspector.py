"""Backend for the Port Manager app.

Collects TCP/UDP connections (with owning PID and process name), resolves
process details and terminates processes by PID.

Two collectors are provided:
  * psutil  - preferred, fast and cross platform.
  * netstat - Windows fallback when psutil is unavailable (or fails).
"""

from __future__ import annotations

import csv
import ctypes
import os
import socket
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:  # pragma: no cover - optional dependency
    import psutil
except Exception:  # pragma: no cover
    psutil = None  # type: ignore

IS_WINDOWS = os.name == "nt"

CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


@dataclass
class Connection:
    """A single TCP/UDP connection row."""

    proto: str = ""
    local_host: str = ""
    local_port: int = 0
    remote_host: str = ""
    remote_port: int = 0
    status: str = ""
    pid: int = 0
    process: str = ""
    user: str = ""

    @property
    def local(self) -> str:
        return format_address(self.local_host, self.local_port)

    @property
    def remote(self) -> str:
        return format_address(self.remote_host, self.remote_port)


def format_address(host: str, port: int) -> str:
    host = host or ""
    if host.startswith("[") and "]" in host:
        host = host[1: host.index("]")]
    return f"{host}:{port}" if host else str(port)


# --------------------------------------------------------------------------- #
# process helpers
# --------------------------------------------------------------------------- #
def is_elevated() -> bool:
    """True when the app runs with administrator rights."""
    if IS_WINDOWS:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAnElevated())
        except Exception:
            return False
    try:
        return os.geteuid() == 0  # type: ignore[attr-defined]
    except Exception:
        return False


def pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if psutil is not None:
        try:
            return psutil.pid_exists(pid)
        except Exception:
            pass
    return _windows_pid_exists(pid)


def _windows_pid_exists(pid: int) -> bool:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    # Fall back to tasklist
    return pid in tasklist_processes()


def tasklist_processes() -> Dict[int, str]:
    """Map of pid -> image name using `tasklist` (Windows only)."""
    result: Dict[int, str] = {}
    if not IS_WINDOWS:
        return result
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        return result
    for row in csv.reader(out.stdout.splitlines()):
        if len(row) < 2:
            continue
        name = row[0].strip()
        try:
            result[int(row[1].strip())] = name
        except ValueError:
            continue
    return result


def process_name(pid: int) -> str:
    """Best effort process image name for a PID."""
    if pid <= 0:
        return ""
    if psutil is not None:
        try:
            return psutil.Process(pid).name()
        except Exception:
            pass
    name = tasklist_processes().get(pid)
    return name or ""


def process_details(pid: int) -> Dict[str, str]:
    """Extended information about a process (exe, user, command line)."""
    details = {"pid": str(pid), "name": "", "exe": "", "user": "", "cmdline": "", "cwd": ""}
    if pid <= 0:
        return details
    if psutil is not None:
        try:
            proc = psutil.Process(pid)
            details["name"] = proc.name()
            details["exe"] = proc.exe()
            try:
                details["user"] = proc.username()
            except Exception:
                details["user"] = ""
            try:
                details["cmdline"] = " ".join(proc.cmdline())
            except Exception:
                details["cmdline"] = ""
            try:
                details["cwd"] = proc.cwd()
            except Exception:
                details["cwd"] = ""
            return details
        except Exception:
            pass
    details["name"] = process_name(pid)
    return details


def kill_pid(pid: int, force: bool = True, tree: bool = True) -> Tuple[bool, str]:
    """Terminate a process.

    Returns (success, message).
    """
    if pid <= 0:
        return False, "Invalid PID."
    if not pid_exists(pid):
        return False, f"PID {pid} is not running."
    name = process_name(pid) or "process"

    if psutil is not None:
        try:
            proc = psutil.Process(pid)
            if force:
                proc.kill()
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()
            if tree:
                try:
                    for child in proc.children(recursive=True):
                        try:
                            child.kill()
                        except Exception:
                            pass
                except Exception:
                    pass
            return True, f"Killed {name} (PID {pid})."
        except psutil.NoSuchProcess:
            return False, f"PID {pid} already exited."
        except psutil.AccessDenied:
            return False, f"Access denied for {name} (PID {pid}). Try running as administrator."
        except Exception as exc:  # pragma: no cover
            return False, f"Failed to kill PID {pid}: {exc}"

    if IS_WINDOWS:
        cmd = ["taskkill", "/PID", str(pid), "/F" if force else ""]
        cmd = [c for c in cmd if c]
        if tree:
            cmd.append("/T")
        try:
            out = subprocess.run(
                cmd, capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW
            )
            ok = out.returncode == 0
            return ok, (out.stdout or out.stderr or "").strip() or f"taskkill exit {out.returncode}"
        except Exception as exc:
            return False, f"Failed to kill PID {pid}: {exc}"

    try:
        os.kill(pid, 9 if force else 15)
        return True, f"Killed {name} (PID {pid})."
    except Exception as exc:
        return False, f"Failed to kill PID {pid}: {exc}"


def open_process_location(pid: int) -> Tuple[bool, str]:
    """Open Explorer with the executable of a process selected."""
    if not IS_WINDOWS:
        return False, "Only supported on Windows."
    details = process_details(pid)
    exe = details.get("exe") or ""
    if not exe:
        return False, "Executable path unavailable (needs administrator rights)."
    try:
        subprocess.Popen(["explorer", "/select,", exe])
        return True, f"Opened location of {os.path.basename(exe)}."
    except Exception as exc:
        return False, str(exc)


# --------------------------------------------------------------------------- #
# connection collectors
# --------------------------------------------------------------------------- #
def _split_addr(value: str) -> Tuple[str, int]:
    """Split 'host:port' (also '[v6]:port') into host and port."""
    if not value:
        return "", 0
    if value.startswith("["):
        end = value.find("]")
        if end != -1:
            host = value[1:end]
            rest = value[end + 1:]
            port = rest[1:] if rest.startswith(":") else ""
            try:
                return host, int(port)
            except ValueError:
                return host, 0
    host, _, port = value.rpartition(":")
    try:
        return host, int(port)
    except ValueError:
        return value, 0


def collect_psutil() -> List[Connection]:
    if psutil is None:
        raise RuntimeError("psutil not available")
    names: Dict[int, str] = {}
    users: Dict[int, str] = {}
    conns: List[Connection] = []
    for raw in psutil.net_connections(kind="inet"):
        if raw.pid and raw.pid not in names:
            names[raw.pid] = process_name(raw.pid)
        lhost, lport = _split_addr(f"{raw.laddr.ip}:{raw.laddr.port}" if raw.laddr else "")
        rhost, rport = ("", 0)
        if raw.raddr:
            rhost, rport = _split_addr(f"{raw.raddr.ip}:{raw.raddr.port}")
        status = raw.status or ""
        if raw.type == socket.SOCK_DGRAM:
            status = status or "UDP"
        proto = "UDP" if raw.type == socket.SOCK_DGRAM else "TCP"
        status = status.upper()
        if status == "LISTEN":
            status = "LISTENING"
        conns.append(
            Connection(
                proto=proto,
                local_host=lhost,
                local_port=lport,
                remote_host=rhost,
                remote_port=rport,
                status=status.upper() if status else "",
                pid=raw.pid or 0,
                process=names.get(raw.pid or 0, ""),
            )
        )
    return conns


def collect_netstat() -> List[Connection]:
    """Windows fallback collector based on `netstat -ano`."""
    if not IS_WINDOWS:
        raise RuntimeError("netstat collector is Windows only")
    out = subprocess.run(
        ["netstat", "-ano"],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=CREATE_NO_WINDOW,
    )
    names = tasklist_processes()
    conns: List[Connection] = []
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        proto = parts[0].upper()
        if proto not in ("TCP", "UDP"):
            continue
        local = parts[1]
        remote = parts[2]
        status = ""
        pid = 0
        if proto == "TCP":
            if len(parts) < 4:
                continue
            status = parts[3].upper()
            pid = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
        else:
            pid = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        lhost, lport = _split_addr(local)
        rhost, rport = _split_addr(remote)
        conns.append(
            Connection(
                proto=proto,
                local_host=lhost,
                local_port=lport,
                remote_host=rhost,
                remote_port=rport,
                status=status,
                pid=pid,
                process=names.get(pid, ""),
            )
        )
    return conns


def collect_connections() -> Tuple[List[Connection], str]:
    """Collect connections, returns (connections, backend name)."""
    if psutil is not None:
        try:
            return collect_psutil(), "psutil"
        except psutil.AccessDenied:
            pass
        except Exception:
            pass
    return collect_netstat(), "netstat"


def pids_listening_on(port: int, proto: str = "") -> List[int]:
    """PIDs of processes listening (or bound) on a local port."""
    try:
        conns, _ = collect_connections()
    except Exception:
        return []
    pids = []
    for c in conns:
        if c.local_port != port:
            continue
        if proto and c.proto != proto.upper():
            continue
        if c.status and c.status not in ("LISTENING", "LISTEN", "BOUND", "UDP"):
            continue
        if c.pid and c.pid not in pids:
            pids.append(c.pid)
    return pids
