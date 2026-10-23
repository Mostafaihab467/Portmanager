#!/usr/bin/env bash
# Install a Port Manager entry into the Linux applications menu (and Desktop).
set -euo pipefail

DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
chmod +x "$DIR/port_manager.sh"

APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
DESKTOP_FILE="$APPS/port-manager.desktop"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Port Manager
GenericName=Port viewer and killer
Comment=View, filter and kill processes by port
Exec='$DIR/port_manager.sh'
Path='$DIR'
Icon='$DIR/assets/icon.png'
Terminal=false
Categories=System;Monitor;Development;
StartupWMClass=Port Manager
EOF

echo "installed $DESKTOP_FILE"

DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESKTOP_DIR" ]; then
    cp "$DESKTOP_FILE" "$DESKTOP_DIR/port-manager.desktop"
    chmod +x "$DESKTOP_DIR/port-manager.desktop"
    # GNOME/KDE refuse to launch an untrusted desktop file until you allow it.
    gio set "$DESKTOP_DIR/port-manager.desktop" metadata::trusted true 2>/dev/null || true
    echo "copied to $DESKTOP_DIR (right-click it and choose Allow Launching if needed)"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS"
fi
echo "look for 'Port Manager' in the applications menu"
