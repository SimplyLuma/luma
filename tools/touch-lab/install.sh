#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Install Touch Lab for the current user: ~/.local/share/luma-touch-lab, a
# launcher and two shortcuts. No root, no system files. `install.sh --remove`
# takes the launcher and shortcuts away and keeps recordings.
set -eu
src=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=${XDG_DATA_HOME:-$HOME/.local/share}/luma-touch-lab
apps=${XDG_DATA_HOME:-$HOME/.local/share}/applications
desktop=$apps/org.projectluma.TouchLab.desktop
schema=org.gnome.settings-daemon.plugins.media-keys
base=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings

bindings() { gsettings get $schema custom-keybindings; }
set_bindings() { gsettings set $schema custom-keybindings "$1"; }
without() {  # drop our two paths from the list
  printf '%s' "$1" | python3 -c 'import ast,sys; l=ast.literal_eval(sys.stdin.read().replace("@as ","")); print([p for p in l if "/luma-touch-lab-" not in p])'
}

if [ "${1:-}" = "--remove" ]; then
  set_bindings "$(without "$(bindings)")"
  rm -f "$desktop"
  echo "Removed the launcher and shortcuts; recordings stay in $root"
  exit 0
fi

mkdir -p "$root/app" "$root/sessions" "$root/labels" "$root/control" "$root/reports" "$root/models" "$apps"
chmod 700 "$root"
rm -rf "$root/app.new"
mkdir "$root/app.new"
cp -R "$src/touchlab" "$src/bin" "$src/plugins" "$root/app.new/"
find "$root/app.new" -name __pycache__ -prune -exec rm -rf {} +
if git -C "$src" rev-parse HEAD >/dev/null 2>&1; then git -C "$src" rev-parse HEAD > "$root/app.new/COMMIT"; fi
rm -rf "$root/app.old"
[ -d "$root/app" ] && mv "$root/app" "$root/app.old"
mv "$root/app.new" "$root/app"
rm -rf "$root/app.old"

"$root/app/bin/touchlab" sync-settings >/dev/null

cat > "$desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Touch Lab
Comment=Teach the touchpad what you meant
Exec=$root/app/bin/touch-lab-app
Icon=input-touchpad
Categories=Utility;
NoDisplay=false
DESK

list=$(without "$(bindings)")
for which in wrong almost; do
  path=$base/luma-touch-lab-$which/
  key='<Super><Alt>w'; name='Touch Lab: that was wrong'
  [ $which = almost ] && key='<Super><Alt>a' && name='Touch Lab: almost wrong'
  gsettings set $schema.custom-keybinding:$path name "$name"
  gsettings set $schema.custom-keybinding:$path command "$root/app/bin/touchlab mark $which"
  gsettings set $schema.custom-keybinding:$path binding "$key"
  list=$(printf '%s' "$list" | python3 -c "import ast,sys; l=ast.literal_eval(sys.stdin.read()); l.append('$path'); print(l)")
done
set_bindings "$list"

echo "Installed in $root/app"
echo "Start recording with:"
"$root/app/bin/touchlab" sudo-command | sed 's/^/  /'
