#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build dependent desktop components against an already verified toolkit cohort.
set -euo pipefail
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
[ "${LUMA_RPM_BUILDER_DIRECT:-0}" = 1 ] && [ "$(id -u)" = 0 ] &&
  { [ -f /run/.containerenv ] || [ -f /.dockerenv ]; } || {
  printf 'error: use an isolated Fedora builder with an admitted toolkit cohort\n' >&2
  exit 1
}
architecture=${LUMA_TARGET_ARCHITECTURE:?select x86_64 or aarch64}
case "$architecture" in x86_64|aarch64) ;; *) exit 2 ;; esac
[ "$(rpm --eval '%{_arch}')" = "$architecture" ]
. "$root/config/desktop/inputs.env"
gtk=${GTK4_NEVRA%.*}.$architecture
adwaita=${LIBADWAITA_NEVRA%.*}.$architecture
platform=${LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA%.*}.$architecture
gtkdir="$root/build/packages/gtk4/RPMS/$architecture"
adwdir="$root/build/packages/libadwaita/RPMS/$architecture"
platformdir="$root/build/packages/luma-developer-platform/$architecture/RPMS"
rpms=("$gtkdir/$gtk.rpm" "${gtkdir}/${gtk/gtk4-/gtk4-devel-}.rpm"
  "${gtkdir}/${gtk/gtk4-/gtk4-devel-tools-}.rpm"
  "$adwdir/$adwaita.rpm" "${adwdir}/${adwaita/libadwaita-/libadwaita-devel-}.rpm"
  "$platformdir/$platform.rpm" "${platformdir}/${platform/luma-developer-platform-/luma-developer-platform-devel-}.rpm"
  "$root/build/packages/figtree-fonts/RPMS/noarch/$FIGTREE_NEVRA.rpm")
# Refuse a missing, mislabelled or wrong-architecture provider before installing.
for file in "${rpms[@]}"; do
  test -f "$file"
  identity=$(rpm -qp --nosignature --qf '%{NEVRA}' "$file")
  [ "$(basename "$file")" = "$identity.rpm" ]
done
dnf5 -y install "${rpms[@]}"
for component in gnome-control-center nautilus gnome-shell; do
  printf 'Building desktop component: %s (%s)\n' "$component" "$architecture"
  bash "$root/scripts/packages/build-$component.sh"
done
