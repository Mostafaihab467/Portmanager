"""Port Manager - a GUI for viewing, filtering and killing processes by port.

Features
    * Live list of TCP/UDP connections with PID and process name
    * Free text filter, protocol / state / port-range filters and presets
    * Configurable auto refresh interval
    * Kill any process by PID, or kill whatever owns a given port
    * Sortable columns, CSV export, clipboard copy, process details
    * Works with psutil (preferred) or plain `netstat` as a fallback
"""

from __future__ import annotations

import csv
import json
import os
import queue
import sys
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import net_inspector as ni

APP_TITLE = "Port Manager"
APP_VERSION = "1.0"

# When frozen by PyInstaller the exe lives in BASE_DIR and the bundled files in
# _MEIPASS, so settings stay next to the exe while icons come from the bundle.
IS_FROZEN = bool(getattr(sys, "frozen", False))
BASE_DIR = (
    os.path.dirname(sys.executable)
    if IS_FROZEN
    else os.path.dirname(os.path.abspath(__file__))
)
RESOURCE_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
ICON_ICO = os.path.join(RESOURCE_DIR, "assets", "icon.ico")
ICON_PNG = os.path.join(RESOURCE_DIR, "assets", "icon.png")

# A read-only install (e.g. /opt/PortManager) still needs somewhere to persist.
FALLBACK_SETTINGS_FILE = os.path.join(
    os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config"),
    "PortManager",
    "settings.json",
)


def load_settings_file() -> dict:
    for path in (SETTINGS_FILE, FALLBACK_SETTINGS_FILE):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            continue
    return {}


def save_settings_file(data: dict) -> None:
    for path in (SETTINGS_FILE, FALLBACK_SETTINGS_FILE):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            return
        except Exception:
            continue


def pick_font(preferred: str, fallback: str) -> str:
    return preferred if os.name == "nt" else fallback


UI_FONT = pick_font("Segoe UI", "DejaVu Sans")
MONO_FONT = pick_font("Consolas", "DejaVu Sans Mono")


def set_window_icon(window: tk.Tk) -> None:
    """Apply the app icon to the title bar, taskbar and dock."""
    try:
        if os.name == "nt" and os.path.exists(ICON_ICO):
            window.iconbitmap(ICON_ICO)
        if os.path.exists(ICON_PNG):
            window._app_icon = tk.PhotoImage(file=ICON_PNG)
            window.iconphoto(True, window._app_icon)
    except tk.TclError:
        pass

PORT_PRESETS: Dict[str, List[int]] = {
    "All": [],
    "XAMPP": [80, 443, 3306, 8080, 8443, 8000, 8009, 8888, 5037, 8081],
    "Web servers": [80, 443, 3000, 5000, 5173, 8000, 8080, 8443, 8888],
    "Databases": [1433, 3306, 5432, 5984, 6379, 9042, 9200, 27017],
    "Mail / FTP": [20, 21, 22, 25, 110, 143, 587, 993, 995],
    "Games / misc": [25565, 27015, 27017, 19132, 28910],
}

COLUMNS = [
    ("proto", "Proto", 60, "center"),
    ("local", "Local Address", 200, "w"),
    ("lport", "Local Port", 80, "e"),
    ("remote", "Remote Address", 200, "w"),
    ("rport", "Remote Port", 80, "e"),
    ("status", "Status", 110, "w"),
    ("pid", "PID", 70, "e"),
    ("process", "Process", 210, "w"),
]


def enable_dpi_awareness() -> None:
    """Make the app crisp on high DPI Windows displays."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class PortManagerApp(tk.Tk):
    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        enable_dpi_awareness()

        self.title(APP_TITLE)
        set_window_icon(self)
        self.geometry("1280x720")
        self.minsize(900, 480)

        self.settings = self._load_settings()
        self.elevated = ni.is_elevated()
        if self.elevated:
            self.title(f"{APP_TITLE} - Administrator")

        # runtime state
        self._rows: Dict[str, ni.Connection] = {}
        self._order: List[str] = []
        self._all_connections: List[ni.Connection] = []
        self._filter_job: Optional[str] = None
        self._result_queue: "queue.Queue[tuple]" = queue.Queue()
        self._refreshing = False
        self._auto_job: Optional[str] = None
        self._sort_column: Optional[str] = None
        self._sort_reverse = False
        self._last_update = ""
        self._backend = "-"

        self._build_style()
        self._build_menu()
        self._build_toolbar()
        self._build_filterbar()
        self._build_table()
        self._build_log()
        self._build_statusbar()
        self._bind_keys()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        saved_geometry = self.settings.get("geometry")
        if isinstance(saved_geometry, str) and saved_geometry:
            try:
                self.geometry(saved_geometry)
            except Exception:
                pass
        self.after(100, self._drain_results)
        self.refresh()
        self._schedule_auto()

    # ------------------------------------------------------------------ #
    # settings
    # ------------------------------------------------------------------ #
    def _load_settings(self) -> dict:
        return load_settings_file()

    def _save_settings(self) -> None:
        save_settings_file(
            {
                "interval": self.interval.get(),
                "auto_refresh": self.auto_refresh.get(),
                "query": self.query.get(),
                "preset": self.preset_var.get(),
                "geometry": self.geometry(),
            }
        )

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=22)
        style.configure("Treeview.Heading", font=(UI_FONT, 9, "bold"))
        style.configure("Toolbutton", padding=(6, 3))
        style.configure("Status.TLabel", foreground="#444444")

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Refresh now\tF5", command=self.refresh)
        file_menu.add_command(label="Export CSV...", command=self.export_csv)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        proc_menu = tk.Menu(menubar, tearoff=0)
        proc_menu.add_command(label="Kill selected process\tDel", command=self.kill_selected)
        proc_menu.add_command(label="Kill process by port...", command=self.kill_by_port_dialog)
        proc_menu.add_command(label="Open file location", command=self.open_location)
        proc_menu.add_command(label="Process details...", command=self.show_details)
        menubar.add_cascade(label="Process", menu=proc_menu)

        view_menu = tk.Menu(menubar, tearoff=0)
        self.menu_auto = tk.BooleanVar(value=bool(self.settings.get("auto_refresh", True)))
        view_menu.add_checkbutton(
            label="Auto refresh", variable=self.menu_auto, command=self._toggle_auto_from_menu
        )
        view_menu.add_separator()
        view_menu.add_command(label="Toggle log panel", command=self._toggle_log)
        view_menu.add_command(label="Clear filters", command=self.clear_filters)
        menubar.add_cascade(label="View", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menubar)

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill=tk.X)

        ttk.Button(bar, text="Refresh", command=self.refresh).pack(side=tk.LEFT)
        ttk.Button(bar, text="Kill selected", command=self.kill_selected).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="Kill by port...", command=self.kill_by_port_dialog).pack(side=tk.LEFT)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        ttk.Button(bar, text="Export CSV", command=self.export_csv).pack(side=tk.LEFT)
        ttk.Button(bar, text="Copy", command=self.copy_selection).pack(side=tk.LEFT, padx=4)

        right = ttk.Frame(bar)
        right.pack(side=tk.RIGHT)

        self.auto_refresh = tk.BooleanVar(value=bool(self.settings.get("auto_refresh", True)))
        ttk.Checkbutton(
            right, text="Auto refresh", variable=self.auto_refresh, command=self._apply_auto_state
        ).pack(side=tk.LEFT)

        ttk.Label(right, text="  Interval (s):").pack(side=tk.LEFT)
        self.interval = tk.IntVar(value=int(self.settings.get("interval", 3)))
        spin = ttk.Spinbox(right, from_=1, to=3600, width=6, textvariable=self.interval)
        spin.pack(side=tk.LEFT)
        spin.bind("<FocusOut>", lambda _e: self._apply_auto_state())
        spin.bind("<Return>", lambda _e: self._apply_auto_state())

        ttk.Label(right, text="   ").pack(side=tk.LEFT)

    def _build_filterbar(self) -> None:
        bar = ttk.Frame(self, padding=(8, 0))
        bar.pack(fill=tk.X)

        ttk.Label(bar, text="Filter:").pack(side=tk.LEFT)
        self.query = tk.StringVar(value=self.settings.get("query", ""))
        self.query_entry = ttk.Entry(bar, textvariable=self.query, width=30)
        self.query_entry.pack(side=tk.LEFT, padx=(4, 8))
        self.query.trace_add("write", lambda *_: self._schedule_filter())

        ttk.Label(bar, text="Proto:").pack(side=tk.LEFT)
        self.proto_var = tk.StringVar(value="All")
        proto_box = ttk.Combobox(
            bar, textvariable=self.proto_var, values=["All", "TCP", "UDP"], state="readonly", width=7
        )
        proto_box.pack(side=tk.LEFT, padx=(4, 8))
        proto_box.bind("<<ComboboxSelected>>", lambda _e: self._schedule_filter())

        ttk.Label(bar, text="State:").pack(side=tk.LEFT)
        self.state_var = tk.StringVar(value="All")
        self.state_box = ttk.Combobox(
            bar, textvariable=self.state_var, state="readonly", width=15
        )
        self.state_box.pack(side=tk.LEFT, padx=(4, 8))
        self.state_box.bind("<<ComboboxSelected>>", lambda _e: self._schedule_filter())

        ttk.Label(bar, text="Ports:").pack(side=tk.LEFT)
        self.port_range = tk.StringVar(value="")
        self.port_entry = ttk.Entry(bar, textvariable=self.port_range, width=16)
        self.port_entry.pack(side=tk.LEFT, padx=(4, 8))
        self.port_entry.bind("<Return>", lambda _e: self._schedule_filter())
        self.port_entry.bind("<FocusOut>", lambda _e: self._schedule_filter())

        ttk.Label(bar, text="Preset:").pack(side=tk.LEFT)
        self.preset_var = tk.StringVar(value=self.settings.get("preset", "All"))
        preset_box = ttk.Combobox(
            bar,
            textvariable=self.preset_var,
            values=list(PORT_PRESETS.keys()),
            state="readonly",
            width=13,
        )
        preset_box.pack(side=tk.LEFT, padx=(4, 8))
        preset_box.bind("<<ComboboxSelected>>", lambda _e: self._schedule_filter())

        ttk.Button(bar, text="Clear", command=self.clear_filters).pack(side=tk.LEFT)

        opts = ttk.Frame(self, padding=(8, 4))
        opts.pack(fill=tk.X)
        self.only_listening = tk.BooleanVar(value=False)
        self.unique_ports = tk.BooleanVar(value=False)
        self.hide_system = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts, text="Only listening", variable=self.only_listening, command=self._schedule_filter
        ).pack(side=tk.LEFT)
        ttk.Checkbutton(
            opts, text="Unique ports only", variable=self.unique_ports, command=self._schedule_filter
        ).pack(side=tk.LEFT, padx=10)
        ttk.Checkbutton(
            opts, text="Hide system processes (PID < 100)", variable=self.hide_system,
            command=self._schedule_filter,
        ).pack(side=tk.LEFT)

        self.hint = ttk.Label(
            opts, text="Tip: right click a row for more actions", foreground="#777777"
        )
        self.hint.pack(side=tk.RIGHT)

    def _build_table(self) -> None:
        frame = ttk.Frame(self, padding=(8, 4))
        frame.pack(fill=tk.BOTH, expand=True)

        cols = [c[0] for c in COLUMNS]
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        for key, heading, width, anchor in COLUMNS:
            self.tree.heading(key, text=heading, command=lambda k=key: self.sort_by(k))
            self.tree.column(key, width=width, anchor=anchor, stretch=(key in ("local", "remote", "process")))

        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.tree.tag_configure("LISTENING", foreground="#0b6b2f")
        self.tree.tag_configure("ESTABLISHED", foreground="#12376b")
        self.tree.tag_configure("dim", foreground="#8a8a8a")

        self.tree.bind("<Double-1>", lambda _e: self.show_details())
        self.tree.bind("<Return>", lambda _e: self.show_details())
        self.tree.bind("<Button-3>", self._popup_menu)

        self._build_context_menu()

    def _build_context_menu(self) -> None:
        self.ctx = tk.Menu(self, tearoff=0)
        self.ctx.add_command(label="Kill process", command=self.kill_selected)
        self.ctx.add_command(label="Kill owner of this port", command=self.kill_port_of_selection)
        self.ctx.add_separator()
        self.ctx.add_command(label="Copy row", command=self.copy_selection)
        self.ctx.add_command(label="Copy port", command=self.copy_port)
        self.ctx.add_separator()
        self.ctx.add_command(label="Open file location", command=self.open_location)
        self.ctx.add_command(label="Process details...", command=self.show_details)
        self.ctx.add_separator()
        self.ctx.add_command(label="Filter by this process", command=self.filter_by_process)

    def _build_log(self) -> None:
        self.log_frame = ttk.LabelFrame(self, text="Activity log", padding=(4, 4))
        self.log_frame.pack(fill=tk.X, padx=8, pady=(0, 4))
        self.log = tk.Text(self.log_frame, height=5, wrap=tk.NONE, state=tk.DISABLED,
                           font=(MONO_FONT, 9))
        sb = ttk.Scrollbar(self.log_frame, orient=tk.VERTICAL, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_statusbar(self) -> None:
        self.status_bar = ttk.Frame(self, padding=(8, 2))
        self.status_bar.pack(fill=tk.X)
        self.status_conns = ttk.Label(self.status_bar, text="Connections: 0", style="Status.TLabel")
        self.status_conns.pack(side=tk.LEFT)
        self.status_shown = ttk.Label(self.status_bar, text="  |  Shown: 0", style="Status.TLabel")
        self.status_shown.pack(side=tk.LEFT)
        self.status_info = ttk.Label(self.status_bar, text="", style="Status.TLabel")
        self.status_info.pack(side=tk.RIGHT)

    def _bind_keys(self) -> None:
        self.bind("<F5>", lambda _e: self.refresh())
        self.bind("<Control-f>", lambda _e: self._focus_filter())
        self.bind("<Control-e>", lambda _e: self.export_csv())
        self.bind("<Delete>", lambda _e: self.kill_selected())
        self.bind("<Control-c>", lambda _e: self.copy_selection())
        self.bind("<Escape>", lambda _e: self.tree.selection_set(()))

    # ------------------------------------------------------------------ #
    # refresh pipeline
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        self.status_info.configure(text="Refreshing...")

        def worker() -> None:
            try:
                conns, backend = ni.collect_connections()
                if not conns:
                    time.sleep(0.4)
                    conns, backend = ni.collect_connections()
                self._result_queue.put(("ok", conns, backend))
            except Exception as exc:
                self._result_queue.put(("err", [], str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_results(self) -> None:
        try:
            while True:
                kind, payload, extra = self._result_queue.get_nowait()
                if kind == "ok":
                    self._refreshing = False
                    self._backend = extra
                    if not payload and self._all_connections:
                        self.log_msg("Refresh returned no rows - keeping last snapshot.")
                        payload = self._all_connections
                    self._all_connections = payload
                    self._last_update = datetime.now().strftime("%H:%M:%S")
                    self._refresh_states()
                    self.populate()
                elif kind == "kill":
                    failures = 0
                    for ok, msg in payload:
                        self.log_msg(("OK: " if ok else "FAILED: ") + msg)
                        failures += 0 if ok else 1
                    if failures:
                        self.status_info.configure(
                            text=f"{failures} process(es) could not be killed (see log)"
                        )
                    self.refresh()
                else:
                    self._refreshing = False
                    self.status_info.configure(text=f"Error: {extra}")
                    self.log_msg(f"ERROR: {extra}")
        except queue.Empty:
            pass
        self.after(150, self._drain_results)

    def _schedule_auto(self) -> None:
        if self._auto_job:
            try:
                self.after_cancel(self._auto_job)
            except Exception:
                pass
            self._auto_job = None
        if self.auto_refresh.get():
            delay = max(1, self._interval_seconds()) * 1000
            self._auto_job = self.after(delay, self._auto_tick)

    def _interval_seconds(self) -> int:
        try:
            value = int(str(self.interval.get()).strip())
        except (TypeError, ValueError):
            value = 3
        value = max(1, min(3600, value))
        return value

    def _auto_tick(self) -> None:
        self.refresh()
        self._schedule_auto()

    def _apply_auto_state(self) -> None:
        self.menu_auto.set(self.auto_refresh.get())
        self._schedule_auto()
        self._save_settings()

    def _toggle_auto_from_menu(self) -> None:
        self.auto_refresh.set(self.menu_auto.get())
        self._apply_auto_state()

    # ------------------------------------------------------------------ #
    # filtering
    # ------------------------------------------------------------------ #
    def _schedule_filter(self) -> None:
        if getattr(self, "_filter_job", None):
            try:
                self.after_cancel(self._filter_job)
            except Exception:
                pass
        self._filter_job = self.after(120, self.populate)

    def _refresh_states(self) -> None:
        states = sorted({c.status for c in getattr(self, "_all_connections", []) if c.status})
        self.state_box.configure(values=["All"] + states)

    def clear_filters(self) -> None:
        self.query.set("")
        self.proto_var.set("All")
        self.state_var.set("All")
        self.port_range.set("")
        self.preset_var.set("All")
        self.only_listening.set(False)
        self.unique_ports.set(False)
        self.hide_system.set(False)
        self._schedule_filter()

    def filter_by_process(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        rec = self._rows.get(sel[0])
        if not rec:
            return
        self.query.set(rec.process or str(rec.pid))
        self._schedule_filter()

    def _parse_port_spec(self) -> List[Tuple[int, int]]:
        spec = (self.port_range.get() or "").strip()
        if not spec:
            return []
        ranges: List[Tuple[int, int]] = []
        for chunk in spec.replace(" ", "").split(","):
            if not chunk:
                continue
            if "-" in chunk:
                lo, _, hi = chunk.partition("-")
                try:
                    ranges.append((int(lo), int(hi)))
                except ValueError:
                    continue
            else:
                try:
                    p = int(chunk)
                    ranges.append((p, p))
                except ValueError:
                    continue
        return ranges

    def _matches(self, c: ni.Connection) -> bool:
        query = (self.query.get() or "").strip().lower()
        proto = self.proto_var.get()
        state = self.state_var.get()
        preset = self.preset_var.get()

        if proto != "All" and c.proto != proto:
            return False
        if state != "All" and c.status != state:
            return False
        if self.only_listening.get():
            if c.proto == "UDP":
                if c.status not in ("UDP", "NONE", "UNCONN", ""):
                    return False
            elif c.status not in ("LISTENING", "LISTEN", "BOUND"):
                return False
        if self.hide_system.get() and 0 < c.pid < 100:
            return False

        ranges = self._parse_port_spec()
        if ranges:
            if not any(lo <= c.local_port <= hi for lo, hi in ranges):
                return False

        if preset and preset != "All":
            ports = PORT_PRESETS.get(preset, [])
            if ports and c.local_port not in ports:
                return False

        if query:
            haystack = " ".join(
                [
                    c.proto,
                    c.local_host,
                    str(c.local_port),
                    c.remote_host,
                    str(c.remote_port),
                    c.status,
                    str(c.pid),
                    c.process,
                ]
            ).lower()
            if query not in haystack:
                return False
        return True

    def populate(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        self._order.clear()

        conns = getattr(self, "_all_connections", [])
        filtered = [c for c in conns if self._matches(c)]

        if self.unique_ports.get():
            seen = set()
            unique: List[ni.Connection] = []
            for c in filtered:
                key = (c.proto, c.local_port)
                if key in seen:
                    continue
                seen.add(key)
                unique.append(c)
            filtered = unique

        if self._sort_column:
            filtered = self._sort_connections(filtered, self._sort_column, self._sort_reverse)

        for c in filtered:
            iid = self.tree.insert("", tk.END, values=self._row_values(c))
            self._rows[iid] = c
            self._order.append(iid)
            tag = self._tag_for(c)
            if tag:
                self.tree.item(iid, tags=(tag,))

        self.status_conns.configure(text=f"Connections: {len(conns)}")
        self.status_shown.configure(text=f"  |  Shown: {len(filtered)}")
        admin = "admin" if self.elevated else "standard"
        self.status_info.configure(
            text=f"Updated {self._last_update}  |  Source: {self._backend}  |  {admin}"
        )

    def _row_values(self, c: ni.Connection) -> tuple:
        return (
            c.proto,
            c.local,
            c.local_port,
            c.remote,
            c.remote_port,
            c.status,
            c.pid,
            c.process,
        )

    @staticmethod
    def _tag_for(c: ni.Connection) -> str:
        if c.status == "LISTENING":
            return "LISTENING"
        if c.status == "ESTABLISHED":
            return "ESTABLISHED"
        return ""

    # ------------------------------------------------------------------ #
    # sorting
    # ------------------------------------------------------------------ #
    def sort_by(self, column: str) -> None:
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = False
        for key, heading, _w, _a in COLUMNS:
            text = heading
            if key == column:
                text = f"{heading} {'v' if self._sort_reverse else '^'}"
            self.tree.heading(key, text=text)
        self.populate()

    @staticmethod
    def _sort_connections(conns: List[ni.Connection], column: str, reverse: bool) -> List[ni.Connection]:
        def key(c: ni.Connection):
            if column == "lport":
                return c.local_port
            if column == "rport":
                return c.remote_port
            if column == "pid":
                return c.pid
            if column == "local":
                return (c.local_host, c.local_port)
            if column == "remote":
                return (c.remote_host, c.remote_port)
            if column == "status":
                return c.status
            if column == "process":
                return (c.process.lower(), c.pid)
            return (c.proto, c.local_port)

        return sorted(conns, key=key, reverse=reverse)

    # ------------------------------------------------------------------ #
    # actions
    # ------------------------------------------------------------------ #
    def _selected_records(self) -> List[ni.Connection]:
        return [self._rows[i] for i in self.tree.selection() if i in self._rows]

    def kill_selected(self) -> None:
        records = self._selected_records()
        if not records:
            messagebox.showinfo("Port Manager", "Select one or more rows first.", parent=self)
            return
        pids = []
        for r in records:
            if r.pid and r.pid not in pids:
                pids.append(r.pid)
        if not pids:
            messagebox.showwarning(
                "Port Manager", "These rows have no owning process (system socket).", parent=self
            )
            return

        lines = []
        for r in records[:12]:
            lines.append(f"  {r.proto} {r.local}  {r.status or ''}  {r.process} (PID {r.pid})")
        if len(records) > 12:
            lines.append(f"  ... and {len(records) - 12} more")
        unique = sorted(set(pids))
        body = "\n".join(lines)
        if len(unique) > 1:
            body += f"\n\n{len(unique)} different processes will be terminated."

        if not messagebox.askyesno(
            "Confirm kill",
            f"Kill the following processes?\n\n{body}",
            parent=self,
            icon="warning",
            default="no",
        ):
            return

        force = messagebox.askyesno(
            "Kill mode",
            "Force kill immediately?\n\nYes = force kill (no graceful shutdown)\n"
            "No = try graceful stop first",
            parent=self,
            default=messagebox.NO,
        )

        def worker() -> None:
            results = []
            for pid in unique:
                ok, msg = ni.kill_pid(pid, force=force, tree=True)
                results.append((ok, msg))
            self._result_queue.put(("kill", results, ""))

        self.status_info.configure(text="Killing...")
        threading.Thread(target=worker, daemon=True).start()

    def kill_port_of_selection(self) -> None:
        records = self._selected_records()
        if not records:
            return
        port = records[0].local_port
        pids = ni.pids_listening_on(port)
        if not pids:
            messagebox.showinfo(
                "Port Manager", f"No process is listening on port {port}.", parent=self
            )
            return
        names = ", ".join(f"{ni.process_name(p)} (PID {p})" for p in pids)
        if not messagebox.askyesno(
            "Confirm kill", f"Kill the owner of port {port}?\n\n{names}", parent=self, icon="warning"
        ):
            return
        for pid in pids:
            ok, msg = ni.kill_pid(pid, force=True)
            self.log_msg(msg)
        self.refresh()

    def kill_by_port_dialog(self) -> None:
        dlg = tk.Toplevel(self)
        dlg.title("Kill process by port")
        set_window_icon(dlg)
        dlg.transient(self)
        dlg.resizable(False, False)
        frame = ttk.Frame(dlg, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Port number:").grid(row=0, column=0, sticky="w")
        var = tk.StringVar()
        entry = ttk.Entry(frame, textvariable=var, width=12)
        entry.grid(row=0, column=1, sticky="w", padx=6)
        entry.focus_set()

        listbox = tk.Listbox(frame, height=6, width=46, exportselection=False)
        listbox.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=8)
        frame.rowconfigure(1, weight=1)

        def lookup() -> None:
            listbox.delete(0, tk.END)
            try:
                port = int(var.get().strip())
            except ValueError:
                listbox.insert(tk.END, "Enter a valid port number")
                return
            pids = ni.pids_listening_on(port)
            if not pids:
                listbox.insert(tk.END, f"No process found listening on port {port}")
                return
            for pid in pids:
                listbox.insert(tk.END, f"{ni.process_name(pid) or 'unknown'}  (PID {pid})")

        def do_kill() -> None:
            try:
                port = int(var.get().strip())
            except ValueError:
                return
            pids = ni.pids_listening_on(port)
            if not pids:
                return
            if messagebox.askyesno(
                "Confirm kill", f"Kill {len(pids)} process(es) using port {port}?", parent=dlg
            ):
                for pid in pids:
                    ok, msg = ni.kill_pid(pid, force=True)
                    self.log_msg(msg)
                dlg.destroy()
                self.refresh()

        ttk.Button(frame, text="Look up", command=lookup).grid(row=2, column=0, sticky="w")
        ttk.Button(frame, text="Kill", command=do_kill).grid(row=2, column=1, sticky="e")
        entry.bind("<Return>", lambda _e: lookup())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

    def open_location(self) -> None:
        records = self._selected_records()
        if not records:
            messagebox.showinfo("Port Manager", "Select a row first.", parent=self)
            return
        pid = records[0].pid
        if not pid:
            messagebox.showwarning("Port Manager", "No process attached to this row.", parent=self)
            return
        ok, msg = ni.open_process_location(pid)
        self.log_msg(msg)
        if not ok:
            messagebox.showwarning("Port Manager", msg, parent=self)

    def show_details(self) -> None:
        records = self._selected_records()
        if not records:
            messagebox.showinfo("Port Manager", "Select a row first.", parent=self)
            return
        rec = records[0]
        details = ni.process_details(rec.pid) if rec.pid else {}
        dlg = tk.Toplevel(self)
        dlg.title(f"Process details - {rec.process or rec.pid}")
        set_window_icon(dlg)
        dlg.geometry("620x380")
        dlg.transient(self)
        frame = ttk.Frame(dlg, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        text = tk.Text(frame, wrap=tk.WORD, font=(MONO_FONT, 9))
        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        text.configure(yscrollcommand=sb.set)

        body = [
            f"Protocol      : {rec.proto}",
            f"Local address : {rec.local}",
            f"Remote address: {rec.remote}",
            f"Status        : {rec.status}",
            f"PID           : {rec.pid}",
            f"Process       : {rec.process}",
            "",
            f"Executable    : {details.get('exe', '')}",
            f"User          : {details.get('user', '')}",
            f"Command line  : {details.get('cmdline', '')}",
            f"Working dir   : {details.get('cwd', '')}",
        ]
        text.insert("1.0", "\n".join(body))
        text.configure(state=tk.DISABLED)
        ttk.Button(dlg, text="Close", command=dlg.destroy).pack(pady=(0, 8))

    def copy_selection(self) -> None:
        records = self._selected_records()
        if not records:
            return
        header = [c[1] for c in COLUMNS]
        lines = ["\t".join(header)]
        for r in records:
            lines.append("\t".join(str(v) for v in self._row_values(r)))
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self.log_msg(f"Copied {len(records)} row(s) to clipboard.")

    def copy_port(self) -> None:
        records = self._selected_records()
        if not records:
            return
        self.clipboard_clear()
        self.clipboard_append(str(records[0].local_port))
        self.log_msg(f"Copied port {records[0].local_port}.")

    def export_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Export connections",
            defaultextension=".csv",
            initialfile="connections.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow([c[1] for c in COLUMNS])
                shown = [self._rows[i] for i in self._order if i in self._rows]
                for r in shown:
                    writer.writerow(self._row_values(r))
            self.log_msg(f"Exported {len(shown)} row(s) to {path}")
            self.status_info.configure(text=f"Exported to {os.path.basename(path)}")
        except Exception as exc:
            messagebox.showerror("Port Manager", f"Export failed:\n{exc}", parent=self)

    def _popup_menu(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            try:
                self.ctx.tk_popup(event.x_root, event.y_root)
            finally:
                self.ctx.grab_release()

    def _focus_filter(self) -> None:
        self.query_entry.focus_set()
        self.query_entry.selection_range(0, tk.END)

    def _toggle_log(self) -> None:
        if self.log_frame.winfo_viewable():
            self.log_frame.pack_forget()
        else:
            self.log_frame.pack(fill=tk.X, padx=8, pady=(0, 4), before=self.status_bar)

    def show_about(self) -> None:
        messagebox.showinfo(
            "About Port Manager",
            f"{APP_TITLE} {APP_VERSION}\n\n"
            "View, filter and kill processes by port.\n"
            f"Backend: {self._backend}\n"
            f"psutil: {'installed' if ni.psutil else 'not installed (netstat fallback)'}",
            parent=self,
        )

    def log_msg(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, f"[{stamp}] {message}\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------ #
    def on_close(self) -> None:
        self._save_settings()
        self.destroy()


def main() -> None:
    # The class name becomes WM_CLASS on X11, which is what StartupWMClass in
    # port-manager.desktop matches so the dock reuses this icon.
    app = PortManagerApp(className="Port Manager")
    app.mainloop()


if __name__ == "__main__":
    main()
