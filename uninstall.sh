#!/usr/bin/env bash
# Removes the installed theme.
#   ./uninstall.sh          remove the files only
#   ./uninstall.sh --apply  also restore the configs backed up by ./install.sh --apply
set -euo pipefail

SRC_DIR=$(cd "$(dirname "$0")" && pwd)
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
NAME=$(basename "$(find "$SRC_DIR/Kvantum" -mindepth 1 -maxdepth 1 -type d | head -n1)")

case "${1:-}" in
  --apply)
    for file in "$CONFIG_DIR/Kvantum/kvantum.kvconfig" "$CONFIG_DIR/qt5ct/qt5ct.conf" \
                "$CONFIG_DIR/qt6ct/qt6ct.conf" "$CONFIG_DIR/kdeglobals"; do
      if [ -s "$file.bak" ]; then
        mv "$file.bak" "$file"
        echo "Restored $file"
      elif [ -e "$file.bak" ]; then
        # empty backup: the file did not exist before install.sh --apply
        rm -f "$file" "$file.bak"
        echo "Removed $file"
      fi
    done ;;
  "") ;;
  *) echo "usage: $0 [--apply]" >&2; exit 1 ;;
esac

rm -rf "${CONFIG_DIR:?}/Kvantum/$NAME"
rm -f "$DATA_DIR/color-schemes/$NAME.colors"
echo "Removed $NAME"
