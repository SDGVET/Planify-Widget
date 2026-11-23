#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ID="local.planify.tasks"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALLED_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/plasma/plasmoids/$PLUGIN_ID"

usage() {
    echo "Usage: ./install.sh [--uninstall]"
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Error: $1 was not found. $2" >&2
        exit 1
    fi
}

if [[ $# -gt 1 ]]; then
    usage >&2
    exit 2
fi

mode="install"
case "${1:-}" in
    "") ;;
    --uninstall) mode="uninstall" ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
esac

require_command kpackagetool6 "Plasma 6 is required."

if [[ "$mode" == "uninstall" ]]; then
    if [[ ! -d "$INSTALLED_DIR" ]]; then
        echo "Planify Tasks is not installed."
        exit 0
    fi
    kpackagetool6 -t Plasma/Applet -r "$PLUGIN_ID"
    echo "Planify Tasks was removed. Remove any widget instances still on your panel or desktop."
    exit 0
fi

require_command python3 "Install Python 3 and try again."

if [[ ! -f "$SCRIPT_DIR/package/contents/code/read_tasks.py" ]]; then
    echo "Error: package/contents/code/read_tasks.py is missing." >&2
    exit 1
fi

cd -- "$SCRIPT_DIR"
if [[ -d "$INSTALLED_DIR" ]]; then
    kpackagetool6 -t Plasma/Applet -u package
    echo "Planify Tasks was upgraded. Log out and back in if the old version is still showing."
else
    kpackagetool6 -t Plasma/Applet -i package
    echo "Planify Tasks was installed."
fi

cat <<'EOF'

To add the widget:
  Panel:   right-click the panel, choose Add or Manage Widgets, then add "Planify Tasks".
  Desktop: right-click the desktop, choose Enter Edit Mode or Add Widgets, then drag "Planify Tasks" onto the desktop.
EOF
