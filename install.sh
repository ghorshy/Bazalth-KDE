#!/usr/bin/env bash
# Installs the Kvantum theme and the KDE color scheme for the current user.
#   ./install.sh          copy the files only
#   ./install.sh --apply  also select them in Kvantum, qt5ct, qt6ct and kdeglobals
#                         (each changed file is backed up once as <file>.bak)
set -euo pipefail

SRC_DIR=$(cd "$(dirname "$0")" && pwd)
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
NAME=$(basename "$(find "$SRC_DIR/Kvantum" -mindepth 1 -maxdepth 1 -type d | head -n1)")
SCHEME="$DATA_DIR/color-schemes/$NAME.colors"

apply=false
case "${1:-}" in
  --apply) apply=true ;;
  "") ;;
  *) echo "usage: $0 [--apply]" >&2; exit 1 ;;
esac

# set_key FILE GROUP KEY VALUE: set one key in an INI file, keeping everything else.
set_key() {
  local file=$1 group=$2 key=$3 value=$4 tmp
  mkdir -p "$(dirname "$file")"
  touch "$file"
  # Back up the original once; an empty backup tells uninstall.sh the file was new.
  [ -e "$file.bak" ] || cp "$file" "$file.bak"
  tmp=$(mktemp)
  awk -v group="[$group]" -v key="$key" -v value="$value" '
    function flush() { if (in_group && !done) { print key "=" value; done = 1 } }
    $0 == group { in_group = 1; print; next }
    /^\[/ { flush(); in_group = 0 }
    in_group && index($0, key "=") == 1 { if (!done) print key "=" value; done = 1; next }
    { print }
    END {
      flush()
      if (!done) { print ""; print group; print key "=" value }
    }
  ' "$file" > "$tmp"
  cat "$tmp" > "$file"
  rm -f "$tmp"
}

mkdir -p "$CONFIG_DIR/Kvantum" "$DATA_DIR/color-schemes"
rm -rf "${CONFIG_DIR:?}/Kvantum/$NAME"
cp -r "$SRC_DIR/Kvantum/$NAME" "$CONFIG_DIR/Kvantum/"
cp "$SRC_DIR/color-schemes/$NAME.colors" "$SCHEME"
echo "Installed $NAME to $CONFIG_DIR/Kvantum/$NAME and $SCHEME"

if ! $apply; then
  cat <<EOF

To use it:
  - Kvantum: theme=$NAME in $CONFIG_DIR/Kvantum/kvantum.kvconfig (or kvantummanager)
  - qt5ct/qt6ct: style "kvantum", color scheme $SCHEME
Or run: $0 --apply
EOF
  exit 0
fi

set_key "$CONFIG_DIR/Kvantum/kvantum.kvconfig" General theme "$NAME"
for ct in qt5ct qt6ct; do
  conf="$CONFIG_DIR/$ct/$ct.conf"
  set_key "$conf" Appearance style kvantum
  set_key "$conf" Appearance color_scheme_path "$SCHEME"
  set_key "$conf" Appearance custom_palette true
done

# KDE apps read their colors from kdeglobals, so copy the scheme in.
kdeglobals="$CONFIG_DIR/kdeglobals"
group=""
while IFS= read -r line; do
  case "$line" in
    \[*\]) group=${line#[}; group=${group%]} ;;
    *=*) case "$group" in Colors:* | ColorEffects:* | WM)
        set_key "$kdeglobals" "$group" "${line%%=*}" "${line#*=}" ;;
      esac ;;
  esac
done < "$SCHEME"
set_key "$kdeglobals" General ColorScheme "$NAME"

echo "Applied $NAME (backups: *.bak next to each changed file). Restart Qt apps to see it."
