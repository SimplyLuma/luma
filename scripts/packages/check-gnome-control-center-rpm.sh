#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Check the complete RPM payload before inspecting individual packaged files.
set -euo pipefail
gcc_rpm=${1:?usage: check-gnome-control-center-rpm.sh RPM}
check_dir=$(mktemp -d)
trap 'rm -rf "$check_dir"' EXIT
# Consume rpm2cpio fully. An early-exiting cpio reader can SIGPIPE its producer
# under pipefail even though it successfully extracted the selected entry.
rpm2cpio "$gcc_rpm" > "$check_dir/payload.cpio"
cpio -i --quiet --to-stdout ./usr/share/applications/gnome-dash-panel.desktop <"$check_dir/payload.cpio" >"$check_dir/gnome-dash-panel.desktop"
test -s "$check_dir/gnome-dash-panel.desktop"
desktop-file-validate "$check_dir/gnome-dash-panel.desktop"
grep -Fxq "Exec=gnome-control-center dash" "$check_dir/gnome-dash-panel.desktop"
# Allow free placement in Dash settings; the dead status-order switch gone (0022).
cpio -i --quiet --to-stdout ./usr/bin/gnome-control-center <"$check_dir/payload.cpio" >"$check_dir/gcc-bin"
grep -aFq "shelf-free-placement" "$check_dir/gcc-bin"
grep -aFq "Allow free placement" "$check_dir/gcc-bin"
! grep -aFq "Swap the clock and quick controls" "$check_dir/gcc-bin"
# Span and Float follow Use islands (0023).
grep -aFq "Always on when islands are off" "$check_dir/gcc-bin"
# Dock folders (0024): the section, its two switches, the per-folder
# arrangement, and the ways on and off the dock.
grep -aFq "Dock folders" "$check_dir/gcc-bin"
grep -aFq "Show the pinned folders in the Dash" "$check_dir/gcc-bin"
grep -aFq "Pin a folder to show it" "$check_dir/gcc-bin"
grep -aFq "Give the folders a surface of their own beside the dock" "$check_dir/gcc-bin"
grep -aFq "Add folder" "$check_dir/gcc-bin"
grep -aFq "Remove from the dock" "$check_dir/gcc-bin"
grep -aFq "Opens as" "$check_dir/gcc-bin"
grep -aFq "dock-folders-visible" "$check_dir/gcc-bin"
grep -aFq "dock-folders-separate" "$check_dir/gcc-bin"
grep -aFq "dock-folders-views" "$check_dir/gcc-bin"
grep -aFq "dock-folders-seen" "$check_dir/gcc-bin"
grep -aFq "dock-folders-detached" "$check_dir/gcc-bin"
# Saved passwords follow a password change (0025): the keyring is asked
# rather than assumed, and the person is told when it stayed behind.
grep -aFq "Saved passwords still use your old password" "$check_dir/gcc-bin"
grep -aFq "org.gnome.keyring.InternalUnsupportedGuiltRiddenInterface" "$check_dir/gcc-bin"
# Scroll Speed on Mouse and Touchpad (0026).
grep -aFq "org.projectluma.peripherals.touchpad" "$check_dir/gcc-bin"
grep -aFq "Reset Scroll Speed" "$check_dir/gcc-bin"
