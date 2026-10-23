#!/usr/bin/env bash
# Start Port Manager on Linux/macOS from wherever the folder is installed.
set -euo pipefail
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
exec python3 port_manager.py "$@"
