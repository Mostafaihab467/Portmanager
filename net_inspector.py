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
import re
import socket
import subprocess
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:  # pragma: no cover - optional dependency
    import psutil
except Exception:  # pragma: no cover
    psutil = None  # type: ignore

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"

CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
# `creationflags` is rejected by subprocess on POSIX, so only pass it on Windows.
SUBPROCESS_FLAGS: Dict = {"creationflags": CREATE_NO_WINDOW} if IS_WINDOWS else {}


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
    """True when the app runs with administrator / root rights."""
    if IS_WINDOWS:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
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
    if IS_WINDOWS:
        return _windows_pid_exists(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


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
            **SUBPROCESS_FLAGS,
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


def _posix_process_name(pid: int) -> str:
    """Process name from /proc, falling back to `ps`."""
    try:
        with open(f"/proc/{pid}/comm", "r", encoding="utf-8", errors="replace") as fh:
            name = fh.read().strip()
        if name:
            return name
    except OSError:
        pass
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout.strip()
    except Exception:
        return ""


def process_name(pid: int) -> str:
    """Best effort process image name for a PID."""
    if pid <= 0:
        return ""
    if psutil is not None:
        try:
            return psutil.Process(pid).name()
        except Exception:
            pass
    if IS_WINDOWS:
        return tasklist_processes().get(pid) or ""
    return _posix_process_name(pid)


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
                cmd, capture_output=True, text=True, timeout=20, **SUBPROCESS_FLAGS
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


def _run(cmd: List[str], timeout: int = 30) -> str:
    """Run a helper command and return stdout, or '' when it is unavailable."""
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, **SUBPROCESS_FLAGS
        )
    except Exception:
        return ""
    if out.returncode != 0:
        return ""
    return out.stdout or ""


def open_process_location(pid: int) -> Tuple[bool, str]:
    """Open the file manager with the executable of a selected process."""
    details = process_details(pid)
    exe = details.get("exe") or ""
    if not exe:
        hint = " (needs administrator rights)" if IS_WINDOWS else " (needs root rights)"
        return False, f"Executable path unavailable{hint}."
    try:
        if IS_WINDOWS:
            subprocess.Popen(["explorer", "/select,", exe], **SUBPROCESS_FLAGS)
        elif IS_MACOS:
            subprocess.Popen(["open", "-R", exe])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(exe)])
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
    stdout = _run(["netstat", "-ano"])
    if not stdout:
        raise RuntimeError("netstat unavailable - install psutil (`pip install -r requirements.txt`).")
    names = tasklist_processes()
    conns: List[Connection] = []
    for line in stdout.splitlines():
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


_SS_PID_RE = re.compile(r'pid=(\d+)')
_SS_NAME_RE = re.compile(r'users:\(\("([^"]+)"')
_NETSTAT_PID_RE = re.compile(r"(\d+)/(.+)")


def collect_ss() -> List[Connection]:
    """Linux collector based on `ss -tunap`."""
    stdout = _run(["ss", "-tunap"])
    if not stdout:
        raise RuntimeError("ss unavailable")
    conns: List[Connection] = []
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        netid = parts[0].rstrip("46").lower()
        if netid not in ("tcp", "udp"):
            continue
        proto = "UDP" if netid == "udp" else "TCP"
        status = parts[1].upper()
        if status == "LISTEN":
            status = "LISTENING"
        elif status in ("UNCONN", ""):
            status = "UDP" if proto == "UDP" else ""
        lhost, lport = _split_addr(parts[4])
        rhost, rport = _split_addr(parts[5])
        pid = 0
        name = ""
        pid_match = _SS_PID_RE.search(line)
        if pid_match:
            pid = int(pid_match.group(1))
            name_match = _SS_NAME_RE.search(line)
            if name_match:
                name = name_match.group(1)
        if pid and not name:
            name = process_name(pid)
        conns.append(
            Connection(
                proto=proto,
                local_host=lhost,
                local_port=lport,
                remote_host=rhost,
                remote_port=rport,
                status=status,
                pid=pid,
                process=name,
            )
        )
    if not conns:
        raise RuntimeError("ss returned no rows")
    return conns


def collect_posix_netstat() -> List[Connection]:
    """Fallback collector for Linux/macOS using `netstat -tulnp`."""
    stdout = _run(["netstat", "-tulnp"]) or _run(["netstat", "-tuln"])
    if not stdout:
        raise RuntimeError("netstat unavailable")
    conns: List[Connection] = []
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        proto = parts[0].rstrip("46").upper()
        if proto not in ("TCP", "UDP"):
            continue
        lhost, lport = _split_addr(parts[3])
        rhost, rport = _split_addr(parts[4])
        owner = parts[-1]
        pid = 0
        name = ""
        owner_match = _NETSTAT_PID_RE.fullmatch(owner)
        if owner_match:
            pid, name = int(owner_match.group(1)), owner_match.group(2)
        elif owner.isdigit():
            pid = int(owner)
        status = ""
        if pid and len(parts) >= 7:
            status = parts[5].upper()
        if status == "LISTEN":
            status = "LISTENING"
        if proto == "UDP" and not status:
            status = "UDP"
        if pid and not name:
            name = process_name(pid)
        conns.append(
            Connection(
                proto=proto,
                local_host=lhost,
                local_port=lport,
                remote_host=rhost,
                remote_port=rport,
                status=status,
                pid=pid,
                process=name,
            )
        )
    if not conns:
        raise RuntimeError("netstat returned no rows")
    return conns


def collect_posix() -> Tuple[List[Connection], str]:
    """Prefer `ss` (the default tool on modern distros), fall back to `netstat`."""
    try:
        return collect_ss(), "ss"
    except Exception:
        return collect_posix_netstat(), "netstat"


def collect_connections() -> Tuple[List[Connection], str]:
    """Collect connections, returns (connections, backend name)."""
    if psutil is not None:
        try:
            return collect_psutil(), "psutil"
        except Exception:
            pass
    if IS_WINDOWS:
        return collect_netstat(), "netstat"
    return collect_posix()


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
