#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Native CI cohort: build the same GTK, Adwaita and Luma platform on both CPUs.
set -euo pipefail
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
[ "${LUMA_RPM_BUILDER_DIRECT:-0}" = 1 ] && [ "$(id -u)" = 0 ] &&
  { [ -f /run/.containerenv ] || [ -f /.dockerenv ]; } || {
  printf 'error: this cohort driver requires the isolated native Fedora CI container\n' >&2
  exit 1
}
architecture=${LUMA_TARGET_ARCHITECTURE:?select x86_64 or aarch64}
case "$architecture" in x86_64|aarch64) ;; *) exit 2 ;; esac
[ "$(rpm --eval '%{_arch}')" = "$architecture" ]
export SOURCE_DATE_EPOCH=$(git -c safe.directory="$root" -C "$root" log -1 --format=%ct HEAD)
bash "$root/scripts/packages/build-figtree-fonts.sh"
dnf5 -y install "$root"/build/packages/figtree-fonts/RPMS/noarch/google-figtree-fonts-*.rpm
bash "$root/scripts/packages/build-gtk4.sh"
bash "$root/scripts/packages/build-libadwaita.sh"
bash "$root/scripts/packages/build-luma-developer-platform.sh"
git -c safe.directory="$root" -C "$root" rev-parse HEAD >"$root/build/SOURCE-REVISION"
