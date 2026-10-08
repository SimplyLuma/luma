#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build a persistent, non-installed Calculator preview. Release artifacts still
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
preview_root="$repo_root/build/dev/calculator-preview"
source_root="$preview_root/sources/gnome-calculator"
build_root="$preview_root/builds/gnome-calculator"
stage_root="$preview_root/builds/stage"
guest_prefix=/var/home/luma/.local/share/project-luma/calculator-preview/runtime
mkdir -p "$preview_root"
sync_root=$(mktemp -d "$preview_root/sync.XXXXXX")

cleanup() {
  case "$sync_root" in
    "$preview_root"/sync.*) rm -rf -- "$sync_root" ;;
  esac
}
trap cleanup EXIT

mkdir -p "$cache_dir" "$sync_root/srpm"
srpm="$cache_dir/$GNOME_CALCULATOR_SRPM"
test -f "$srpm" || {
  printf 'error: missing cached source RPM: %s\n' "$srpm" >&2
  exit 1
}

printf '%s  %s\n' "$GNOME_CALCULATOR_SRPM_SHA256" "$srpm" |
  sha256sum --check --status || {
    printf 'error: Calculator source RPM checksum mismatch\n' >&2
    exit 1
  }

(
  cd "$sync_root/srpm"
  rpm2cpio "$srpm" | cpio -idm --quiet
  tar -xf gnome-calculator-50.0.tar.xz -C "$sync_root"
)
mv "$sync_root/gnome-calculator-50.0" "$sync_root/gnome-calculator"
(
  cd "$sync_root/gnome-calculator"
  # This temporary source sits beneath the Project Luma Git worktree. Keep Git
  # from discovering that parent repository or `git apply` will resolve paths
  # against the repository root instead of this extracted upstream tree.
  GIT_CEILING_DIRECTORIES="$preview_root" \
    git apply "$repo_root/patches/gnome-calculator/0001-luma-calculator-aesthetic-v1.patch"
  GIT_CEILING_DIRECTORIES="$preview_root" \
    git apply "$repo_root/patches/gnome-calculator/0002-prairie-basic-product.patch"
  GIT_CEILING_DIRECTORIES="$preview_root" \
    git apply "$repo_root/patches/gnome-calculator/0003-prairie-calculator-polish.patch"
  GIT_CEILING_DIRECTORIES="$preview_root" \
    git apply "$repo_root/patches/gnome-calculator/0004-prairie-readout-presentation.patch"
  GIT_CEILING_DIRECTORIES="$preview_root" \
    git apply "$repo_root/patches/gnome-calculator/0005-prairie-readout-input-boundary.patch"
  GIT_CEILING_DIRECTORIES="$preview_root" \
    git apply "$repo_root/patches/gnome-calculator/0006-luma-appkit-convergence.patch"
)

# Preserve Meson's dependency graph. Fresh SRPM extraction changes every mtime,
# so compare file contents and update only genuinely changed source.
mkdir -p "$source_root" "$build_root"
rsync -rlpt --delete --ignore-times "$sync_root/gnome-calculator/" "$source_root/"
cmp "$sync_root/gnome-calculator/src/math-window.vala" \
  "$source_root/src/math-window.vala"
cmp "$sync_root/gnome-calculator/src/ui/math-window.blp" \
  "$source_root/src/ui/math-window.blp"
grep -Fq "Prairie's product surface is Basic-only" \
  "$source_root/src/math-buttons.vala"
grep -Fq "default-width: 320" "$source_root/src/ui/math-window.blp"
grep -Fq '"luma-identity-button"' "$source_root/src/ui/math-window.blp"
grep -Fq 'decoration-layout: ":minimize,maximize,close"' \
  "$source_root/src/ui/math-window.blp"
grep -Fq "padding: 18px 16px 6px" "$source_root/src/ui/style.css"
grep -Fq "hscrollbar-policy: never" "$source_root/src/ui/math-display.blp"
grep -Fq "Label result_label" "$source_root/src/ui/math-display.blp"
grep -Fq "visible: false" "$source_root/src/ui/math-display.blp"
grep -Fq "focusable: true" "$source_root/src/ui/math-display.blp"
grep -Fq "vexpand: true" "$source_root/src/ui/math-display.blp"
grep -Fq 'result_label.label = equation.is_empty ? "0" : equation.display' \
  "$source_root/src/math-display.vala"
grep -Fq "Shared Luma Application Kit composition" \
  "$source_root/src/ui/style.css"
install -m 0644 "$sync_root/srpm/gnome-calculator.spec" \
  "$preview_root/gnome-calculator.spec"

builder_command='set -euo pipefail
if [ ! -f .dependencies-ready ]; then
  dnf5 -y install rpm-build dnf5-plugins meson ninja-build gcc gettext \
    blueprint-compiler vala
  dnf5 -y builddep gnome-calculator.spec
  touch .dependencies-ready
fi

if [ ! -d builds/gnome-calculator/meson-private ]; then
  meson setup --buildtype=debugoptimized \
    --prefix='"$guest_prefix"' --libdir=lib \
    builds/gnome-calculator sources/gnome-calculator \
    -Ddoc=false -Ddisable-introspection=true -Dui-tests=false
fi

# Blueprint batch compilation is exposed to Ninja as a directory output. Track
# the actual source content so an edited .blp is embedded on this same build.
blueprint_stamp=builds/gnome-calculator/.luma-blueprints.sha256
blueprint_checksum=$(
  find sources/gnome-calculator/src/ui -type f -name "*.blp" -print0 |
    sort -z |
    xargs -0 sha256sum |
    sha256sum |
    cut -d" " -f1
)
blueprint_previous=$(test -f "$blueprint_stamp" && cat "$blueprint_stamp" || true)
if [ "$blueprint_checksum" != "$blueprint_previous" ]; then
  find sources/gnome-calculator/src/ui -type f -name "*.blp" -exec touch {} +
  touch sources/gnome-calculator/src/ui/gnome-calculator.gresource.xml
fi

meson compile -C builds/gnome-calculator
rm -rf builds/stage
DESTDIR="$PWD/builds/stage" meson install --no-rebuild -C builds/gnome-calculator
if [ "$blueprint_checksum" != "$blueprint_previous" ]; then
  printf "%s\n" "$blueprint_checksum" >"$blueprint_stamp"
fi'

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$preview_root" "$FEDORA_RPM_BUILD_CONTAINER" "$builder_command"

test -x "$stage_root$guest_prefix/bin/gnome-calculator"
printf 'Calculator preview build is ready at %s\n' "$preview_root"
