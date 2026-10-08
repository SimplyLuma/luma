#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
patch_file="$repo_root/patches/gnome-shell/0011-luma-presence-login.patch"
build_script="$repo_root/scripts/packages/build-gnome-shell.sh"
mobile_build="$repo_root/scripts/mobile/build-luma-shell-fp6.sh"

test -s "$patch_file"
for theme in \
  gnome-shell-light.css \
  gnome-shell-dark.css \
  gnome-shell-high-contrast.css; do
  grep -Fq "diff --git a/data/theme/$theme b/data/theme/$theme" "$patch_file"
done
grep -Fq "file:///usr/share/backgrounds/luma/prism.png" "$patch_file"
grep -Fq "new Shell.BlurEffect" "$patch_file"
grep -Fq "radius: 28" "$patch_file"
grep -Fq "scale_x: 1.08" "$patch_file"
grep -Fq "dialogBox.y2 - 24 * scaleFactor - clockHeight" "$patch_file"
grep -Fq "dialogHeight * 0.49" "$patch_file"
grep -Fq "iconSize: 76" "$patch_file"
grep -Fq "this._defaultButtonWell.add_child(this._spinner)" "$patch_file"
grep -Fq "if (oldActor === this._spinner)" "$patch_file"
grep -Fq "this._addSystemAction(_('Suspend'), 'can-suspend'" "$patch_file"
grep -Fq "this._addSystemAction(_('Restart…'), 'can-restart'" "$patch_file"
grep -Fq "this._addSystemAction(_('Shut Down…'), 'can-power-off'" "$patch_file"
grep -Fq "new PopupMenu.PopupMenu(this, 1.0, St.Side.TOP)" "$patch_file"
! grep -Fq "this.connect('clicked', () => this._systemActions.activatePowerOff())" "$patch_file"
grep -Fq "this._inputWell.add_child(this._capsLockWarningLabel)" "$patch_file"

grep -Fq "0011-luma-presence-login.patch" "$build_script"
grep -Fq "0011-luma-presence-login.patch" "$mobile_build"
grep -Fq "! grep -Fq \".prairie-login-spinner-slot\"" "$build_script"

printf 'Luma Login source contract: PASS\n'
