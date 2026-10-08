#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build a persistent, non-installed GTK preview for system-level visual work.
# Release artifacts continue to come from the GTK RPM build script.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in cpio git mktemp podman rpm2cpio rsync sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required GTK preview tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  printf 'error: run the GTK preview builder on the canonical Linux/x86_64 host\n' >&2
  exit 1
fi

cache_dir="$repo_root/build/cache/srpm"
preview_root="$repo_root/build/dev/gtk4-preview"
source_root="$preview_root/sources/gtk4"
build_root="$preview_root/builds/gtk4"
mkdir -p "$preview_root"
sync_root=$(mktemp -d "$preview_root/sync.XXXXXX")

cleanup() {
  case "$sync_root" in
    "$preview_root"/sync.*) rm -rf -- "$sync_root" ;;
  esac
}
trap cleanup EXIT

srpm="$cache_dir/$GTK4_SRPM"
test -f "$srpm" || {
  printf 'error: missing cached source RPM: %s\n' "$srpm" >&2
  exit 1
}
printf '%s  %s\n' "$GTK4_SRPM_SHA256" "$srpm" |
  sha256sum --check --status || {
    printf 'error: GTK source RPM checksum mismatch\n' >&2
    exit 1
  }

mkdir -p "$sync_root/srpm"
(
  cd "$sync_root/srpm"
  rpm2cpio "$srpm" | cpio -idm --quiet
  tar -xf gtk-4.22.4.tar.xz -C "$sync_root"
)
mv "$sync_root/gtk-4.22.4" "$sync_root/gtk4"
(
  cd "$sync_root/gtk4"
  git apply "$sync_root/srpm/0001-gtkapplication-wayland-null-check.patch"
  git apply "$repo_root/patches/gtk4/0001-luma-calm-window-shadow.patch"
  git apply "$repo_root/patches/gtk4/0002-luma-native-window-elevation.patch"
  git apply "$repo_root/patches/gtk4/0003-luma-native-headerbar-chrome.patch"
)

mkdir -p "$source_root" "$(dirname -- "$build_root")"
rsync -rlp --checksum "$sync_root/gtk4/" "$source_root/"
install -m 0644 "$sync_root/srpm/gtk4.spec" "$preview_root/gtk4.spec"

# Release tarballs carry precompiled theme CSS. Preserve the persistent source
# tree for warm Ninja builds, but explicitly invalidate GTK's resource target
# whenever that generated light theme changes; tarball mtimes alone are not a
# reliable dependency signal after the first build.
theme_stamp="$preview_root/.luma-light-theme.sha256"
theme_checksum=$(sha256sum \
  "$source_root/gtk/theme/Default/_common.scss" \
  "$source_root/gtk/theme/Default/Default-light.css" | sha256sum | cut -d' ' -f1)
theme_previous=$(test -f "$theme_stamp" && cat "$theme_stamp" || true)
if [ "$theme_checksum" != "$theme_previous" ]; then
  touch "$source_root/gtk/theme/Default/Default-light.css"
  touch "$source_root/gtk/gtk.gresources.xml"
fi

builder_command='set -euo pipefail
if [ ! -f .dependencies-ready ]; then
  dnf5 -y install rpm-build dnf5-plugins meson ninja-build gcc gcc-c++ sassc
  dnf5 -y builddep gtk4.spec
  touch .dependencies-ready
fi

if [ ! -d builds/gtk4/meson-private ]; then
  meson setup --buildtype=debugoptimized --prefix=/usr --libdir=lib64 \
    builds/gtk4 sources/gtk4 \
    -Dbroadway-backend=false -Dsysprof=disabled -Dtracker=disabled \
    -Dcolord=disabled -Ddocumentation=false -Dman-pages=false \
    -Dbuild-testsuite=false -Dbuild-tests=false -Dbuild-examples=false
fi
ninja -C builds/gtk4 gtk/libgtk-4.so.1.2200.4'

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$preview_root" "$FEDORA_RPM_BUILD_CONTAINER" "$builder_command"

printf '%s\n' "$theme_checksum" >"$theme_stamp"

printf 'GTK preview build is ready at %s\n' "$preview_root"
