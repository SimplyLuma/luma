#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build a persistent, non-installed Calendar preview. Release artifacts still
# come from the RPM build; this lane exists solely for low-latency visual work.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in cpio git mktemp podman rpm2cpio rsync tar; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required preview tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  printf 'error: run the preview builder on the canonical Linux/x86_64 host\n' >&2
  exit 1
fi

cache_dir="$repo_root/build/cache/srpm"
preview_root="$repo_root/build/dev/calendar-preview"
source_root="$preview_root/builds/gnome-calendar/sources/gnome-calendar"
build_root="$preview_root/builds/gnome-calendar/build"
stage_root="$preview_root/builds/stage"
guest_prefix=/var/home/luma/.local/share/project-luma/calendar-preview/runtime
mkdir -p "$preview_root"
sync_root=$(mktemp -d "$preview_root/sync.XXXXXX")

cleanup() {
  case "$sync_root" in
    "$preview_root"/sync.*) rm -rf -- "$sync_root" ;;
  esac
}
trap cleanup EXIT

mkdir -p "$cache_dir" "$sync_root/srpm"
srpm="$cache_dir/$GNOME_CALENDAR_SRPM"
test -f "$srpm" || {
  printf 'error: missing cached source RPM: %s\n' "$srpm" >&2
  exit 1
}

printf '%s  %s\n' "$GNOME_CALENDAR_SRPM_SHA256" "$srpm" |
  sha256sum --check --status || {
    printf 'error: Calendar source RPM checksum mismatch\n' >&2
    exit 1
  }

(
  cd "$sync_root/srpm"
  rpm2cpio "$srpm" | cpio -idm --quiet
  tar -xf gnome-calendar-50.0.tar.xz -C "$sync_root"
)
mv "$sync_root/gnome-calendar-50.0" "$sync_root/gnome-calendar"
(
  cd "$sync_root/gnome-calendar"
  export GIT_CEILING_DIRECTORIES="$preview_root"
  git apply "$repo_root/patches/gnome-calendar/0001-luma-calendar-aesthetic-v1.patch"
  git apply "$repo_root/patches/gnome-calendar/0002-luma-title-label-metrics.patch"
)

# Preserve Meson's dependency graph. Fresh SRPM extraction changes every mtime,
# so compare file contents and update only genuinely changed source.
mkdir -p "$source_root" "$build_root"
rsync -rlp --checksum "$sync_root/gnome-calendar/" "$source_root/"
install -m 0644 "$sync_root/srpm/gnome-calendar.spec" \
  "$preview_root/gnome-calendar.spec"

builder_command='set -euo pipefail
if [ ! -f .dependencies-ready ]; then
  dnf5 -y install rpm-build dnf5-plugins meson ninja-build gcc gettext \
    blueprint-compiler
  dnf5 -y builddep gnome-calendar.spec
  touch .dependencies-ready
fi

if [ ! -d builds/gnome-calendar/build/meson-private ]; then
  mkdir -p builds/gnome-calendar/build
  (
    cd builds/gnome-calendar/build
    meson setup --buildtype=debugoptimized \
      --prefix='"$guest_prefix"' --libdir=lib \
      . ../sources/gnome-calendar
  )
fi

# Blueprint batch compilation is exposed to Ninja as directory outputs. Track
# source content and touch only resource manifests when the templates change.
blueprint_stamp=builds/gnome-calendar/build/.luma-blueprints.sha256
blueprint_checksum=$(
  find builds/gnome-calendar/sources/gnome-calendar/src -type f -name "*.blp" -print0 |
    sort -z |
    xargs -0 sha256sum |
    sha256sum |
    cut -d" " -f1
)
blueprint_previous=$(test -f "$blueprint_stamp" && cat "$blueprint_stamp" || true)
if [ "$blueprint_checksum" != "$blueprint_previous" ]; then
  find builds/gnome-calendar/sources/gnome-calendar/src -type f -name "*.blp" -exec touch {} +
  find builds/gnome-calendar/sources/gnome-calendar/src -type f -name "*.gresource.xml" -exec touch {} +
fi

meson compile -C builds/gnome-calendar/build
rm -rf builds/stage
DESTDIR="$PWD/builds/stage" meson install --no-rebuild -C builds/gnome-calendar/build
if [ "$blueprint_checksum" != "$blueprint_previous" ]; then
  printf "%s\n" "$blueprint_checksum" >"$blueprint_stamp"
fi'

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$preview_root" "$FEDORA_RPM_BUILD_CONTAINER" "$builder_command"

test -x "$stage_root$guest_prefix/bin/gnome-calendar"
printf 'Calendar preview build is ready at %s\n' "$preview_root"
