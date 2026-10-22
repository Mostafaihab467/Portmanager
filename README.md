# Port Manager

A small desktop app (Python + Tkinter) that shows every TCP/UDP port on the machine
like `netstat` / XAMPP's "Netstat" panel, but with filtering, an auto-refresh
interval and the ability to kill whatever is holding a port.

![features](https://img.shields.io/badge/python-3.8%2B-3776ab) ![os](https://img.shields.io/badge/os-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)

## Features

| Area | What you get |
| --- | --- |
| Table | Proto, Local address/port, Remote address/port, Status, PID, Process name |
| Filter | Free-text search across every column |
| Filter | Protocol filter (All / TCP / UDP) |
| Filter | State filter (LISTENING, ESTABLISHED, TIME_WAIT, ...) |
| Filter | Port filter: `80`, `80,443`, `8000-9000` |
| Filter | Presets: XAMPP, Web servers, Databases, Mail/FTP, Games |
| Filter | "Only listening", "Unique ports only", "Hide system processes (PID < 100)" |
| Refresh | Manual refresh (F5) + auto refresh with a configurable interval (1-3600 s) |
| Kill | Kill the selected process(es) - graceful stop or force kill |
| Kill | "Kill by port..." - enter a port number and kill whoever owns it |
| Kill | Right click a row -> "Kill owner of this port" |
| Sort | Click any column header to sort (click again to reverse) |
| Export | Export the visible rows to CSV |
| Copy | Copy selected rows / the port number to the clipboard |
| Inspect | Process details dialog (exe path, user, command line, working dir) |
| Misc | Open the executable location in Explorer, activity log, settings persistence |

## Requirements

* Windows (also runs on Linux/macOS), Python 3.8+
* `psutil` (optional but recommended - the app falls back to `netstat` without it)

```
pip install -r requirements.txt
```

or just double-click `install_deps.bat`.

## Run

```
python port_manager.py
```

or double-click `run.bat`.

## Running as administrator

Windows only lets you kill processes owned by another user or started by a
service when the app runs elevated. The status bar shows `admin` when the app
has administrator rights - right-click `run.bat` and choose *Run as
administrator* if you see `Access denied` when killing a process.

## Quick answers to common questions

* **How do I free port 80/3306?** Click *Kill by port...*, type the number, press
  *Look up*, select the owner and press *Kill*. (XAMPP often needs this - stop
  `httpd.exe`/`mysqld.exe` first, or kill them here.)
* **How do I find what listens on a port?** Put the number in the *Ports* filter
  box, e.g. `3306`, and tick *Only listening*.
* **How do I find a program by name?** Type the process name in the *Filter* box,
  e.g. `chrome`.

## Keyboard shortcuts

| Key | Action |
| --- | --- |
| F5 | Refresh now |
| Ctrl+F | Focus the filter box |
| Delete | Kill selected process(es) |
| Ctrl+E | Export CSV |
| Ctrl+C | Copy selection |
| Esc | Clear selection |

## Files

```
PortManager/
├── port_manager.py     # GUI application (Tkinter)
├── net_inspector.py    # backend: collection, process info, kill
├── requirements.txt    # optional dependency: psutil
├── install_deps.bat    # pip install -r requirements.txt
├── run.bat             # start the app
└── settings.json       # created on exit (interval, filters, window size)
```

## Notes

* `psutil` is used when available; otherwise the app parses `netstat -ano`, so it
  still works on machines without admin rights (fewer details are visible).
* The activity log at the bottom records every kill action.
