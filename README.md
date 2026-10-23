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

* Python 3.8+ with Tkinter, on Windows, Linux or macOS
  (Linux: `sudo apt install python3-tk`; the socket fallback also uses `ss`
  from `iproute2`, or `netstat` from `net-tools`)
* `psutil` (optional but recommended - without it the app parses `netstat`
  on Windows and `ss`/`netstat` on Linux, which hides other users' processes)

```
pip install -r requirements.txt
```

or just double-click `install_deps.bat`.

## Run

| OS | Command |
| --- | --- |
| Windows | double-click `run.bat`, or `python port_manager.py` |
| Linux / macOS | `./port_manager.sh`, or `python3 port_manager.py` |

## Shortcuts and app menu entries

Windows - creates a Start Menu entry, a Desktop shortcut and a
*Port Manager (Administrator)* entry, all using `assets/icon.ico`:

```
install_shortcut.bat
```

The Start Menu entry shows up when you press the Windows key and type
"Port Manager"; right-click it to *Pin to Start* or *Pin to taskbar*.
If you have run `build_portable.bat` (below) the shortcuts start the built
`PortManager.exe`; otherwise they start the script with `pythonw.exe`.

Linux - installs a `.desktop` entry into `~/.local/share/applications` (and a
copy on the Desktop, where GNOME/KDE need a right-click → *Allow Launching*
before the first run):

```
./install_linux_desktop.sh
```

## Standalone Windows build (optional)

Explorer, Alt-Tab and Task Manager read an icon from the *executable* of the
process that owns the window, so while the app runs from `pythonw.exe` those
lists show the Python logo (the title bar and taskbar already show the app
icon). Build your own executable to fix that:

```
python -m pip install pyinstaller      # once
build_portable.bat
```

That produces `dist\PortManager\PortManager.exe` (~26 MB folder) with the icon
and version info embedded, so Task Manager lists it as **Port Manager** with
its own icon and it runs on machines that have no Python installed. Copy the
whole `PortManager` folder to move it; `settings.json` is written next to the
exe. Re-run `install_shortcut.bat` afterwards to point the shortcuts at it.

## Running with elevated rights

Windows only lets you kill processes owned by another user or started by a
service when the app runs elevated; on Linux that needs root. The status bar
shows `admin` when the app has the rights - use the *Port Manager
(Administrator)* shortcut, right-click `run.bat` and choose *Run as
administrator*, or start it with `sudo -E ./port_manager.sh`.

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
├── port_manager.py           # GUI application (Tkinter)
├── net_inspector.py          # backend: collection, process info, kill
├── requirements.txt          # optional dependency: psutil
├── install_deps.bat          # pip install -r requirements.txt
├── run.bat                   # start the app (Windows)
├── port_manager.sh           # start the app (Linux / macOS)
├── make_shortcut.ps1         # create Start Menu + Desktop shortcuts (Windows)
├── install_shortcut.bat      # run make_shortcut.ps1 with one double-click
├── build_portable.bat        # optional: build dist\PortManager\PortManager.exe
├── version_info.txt          # version resource used by that build
├── install_linux_desktop.sh  # install the applications-menu entry (Linux)
├── assets/
│   ├── icon.ico              # Windows icon (16-256 px)
│   └── icon.png              # window / desktop icon (Linux, macOS)
├── dist/                     # build_portable.bat output (not in git)
└── settings.json             # created on exit (interval, filters, window size)
```

## Notes

* `psutil` is used when available; otherwise the app parses `netstat -ano` on
  Windows and `ss -tunap` (falling back to `netstat -tulnp`) on Linux, so it
  still works on machines without admin rights (fewer details are visible).
* The activity log at the bottom records every kill action.
* Settings are written next to the app; if that folder is read-only (e.g. an
  `/opt` install) they go to `%APPDATA%\PortManager` on Windows and
  `~/.config/PortManager` on Linux instead.
