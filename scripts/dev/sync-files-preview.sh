#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build a persistent, non-installed Files/libadwaita preview. This is an
# iteration lane only: release artifacts continue to come from the RPM scripts.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

component=${1:-all}
case "$component" in
  all|libadwaita|nautilus) ;;
  *)
    printf 'usage: %s [all|libadwaita|nautilus]\n' "$0" >&2
    exit 2
    ;;
esac

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
preview_root="$repo_root/build/dev/files-preview"
source_root="$preview_root/sources"
build_root="$preview_root/builds"
mkdir -p "$preview_root"
sync_root=$(mktemp -d "$preview_root/sync.XXXXXX")

cleanup() {
  case "$sync_root" in
    "$preview_root"/sync.*) rm -rf -- "$sync_root" ;;
  esac
}
trap cleanup EXIT

mkdir -p "$source_root" "$build_root" "$sync_root/libadwaita-srpm" \
  "$sync_root/nautilus-srpm"

libadwaita_srpm="$cache_dir/$LIBADWAITA_SRPM"
nautilus_srpm="$cache_dir/$NAUTILUS_SRPM"
test -f "$libadwaita_srpm" || {
  printf 'error: missing cached source RPM: %s\n' "$libadwaita_srpm" >&2
  exit 1
}
test -f "$nautilus_srpm" || {
  printf 'error: missing cached source RPM: %s\n' "$nautilus_srpm" >&2
  exit 1
}

(
  cd "$sync_root/libadwaita-srpm"
  rpm2cpio "$libadwaita_srpm" | cpio -idm --quiet
  tar -xf libadwaita-1.9.3.tar.xz -C "$sync_root"
)
mv "$sync_root/libadwaita-1.9.3" "$sync_root/libadwaita"
(
  cd "$sync_root/libadwaita"
  export GIT_CEILING_DIRECTORIES="$preview_root"
  git apply "$repo_root/patches/libadwaita/0001-luma-light-design-system.patch"
  git apply "$repo_root/patches/libadwaita/0002-luma-title-label-metrics.patch"
  git apply "$repo_root/patches/libadwaita/0003-luma-title-label-baseline.patch"
  git apply "$repo_root/patches/libadwaita/0004-prairie-generic-headerbar-contract.patch"
  git apply "$repo_root/patches/libadwaita/0005-luma-application-components.patch"
  git apply "$repo_root/patches/libadwaita/0006-stylesheet-add-shared-Luma-work-islands.patch"
  git apply "$repo_root/patches/libadwaita/0007-luma-application-window-elevation.patch"
  git apply "$repo_root/patches/libadwaita/0008-luma-connected-window-controls.patch"
  git apply "$repo_root/patches/libadwaita/0009-luma-appkit-component-boundaries.patch"
  git apply "$repo_root/patches/libadwaita/0010-luma-native-surface-integrity-and-global-chrome.patch"
)

(
  cd "$sync_root/nautilus-srpm"
  rpm2cpio "$nautilus_srpm" | cpio -idm --quiet
  tar -xf nautilus-50.2.2.tar.xz -C "$sync_root"
)
mv "$sync_root/nautilus-50.2.2" "$sync_root/nautilus"
(
  cd "$sync_root/nautilus"
  export GIT_CEILING_DIRECTORIES="$preview_root"
  git apply "$sync_root/nautilus-srpm/default-terminal.patch"
  git apply "$repo_root/patches/nautilus/0001-luma-single-global-search-action.patch"
  git apply "$repo_root/patches/nautilus/0002-luma-blank-sidebar-title.patch"
  git apply "$repo_root/patches/nautilus/0003-luma-files-aesthetic-v1.patch"
  git apply "$repo_root/patches/nautilus/0004-luma-filer-identity-and-devices.patch"
  git apply "$repo_root/patches/nautilus/0005-luma-sidebar-divider-contract.patch"
  git apply "$repo_root/patches/nautilus/0006-luma-window-template-types.patch"
  git apply "$repo_root/patches/nautilus/0007-luma-natural-attached-breadcrumb.patch"
  git apply "$repo_root/patches/nautilus/0008-luma-pathbar-natural-measure.patch"
  git apply "$repo_root/patches/nautilus/0009-luma-remove-redundant-tooltips.patch"
  git apply "$repo_root/patches/nautilus/0010-prairie-empty-folder-state.patch"
  git apply "$repo_root/patches/nautilus/0011-luma-title-label-metrics.patch"
  git apply "$repo_root/patches/nautilus/0012-luma-responsive-filer.patch"
  git apply "$repo_root/patches/nautilus/0013-luma-applications-location.patch"
  git apply "$repo_root/patches/nautilus/0014-luma-personal-storage.patch"
  git apply "$repo_root/patches/nautilus/0015-luma-application-installer-drop.patch"
  git apply "$repo_root/patches/nautilus/0016-luma-production-filer.patch"
  git apply "$repo_root/patches/nautilus/0017-nautilus-implement-Atlas-islands-and-native-Columns-.patch"
  git apply "$repo_root/patches/nautilus/0018-nautilus-align-Filer-with-the-running-simulator.patch"
  git apply "$repo_root/patches/nautilus/0019-luma-title-and-native-control-convergence.patch"
  git apply "$repo_root/patches/nautilus/0020-luma-shared-component-boundaries.patch"
  git apply "$repo_root/patches/nautilus/0021-luma-remove-duplicate-title-divider.patch"
  git apply "$repo_root/patches/nautilus/0022-luma-final-island-spacing-and-stroke.patch"
)

# Preserve the stable source directories and their Meson dependency graphs.
# rsync updates only files whose patched source actually changed.
# Patched scratch trees receive fresh mtimes on every run. Compare content so
# unchanged source does not invalidate Ninja's dependency graph.
# Deliberately do not propagate scratch-tree timestamps: unchanged source
# avoids broad recompilation even when Meson refreshes lightweight generators.
rsync -rlp --checksum "$sync_root/libadwaita/" "$source_root/libadwaita/"
rsync -rlp --checksum "$sync_root/nautilus/" "$source_root/nautilus/"
install -m 0644 "$sync_root/libadwaita-srpm/libadwaita.spec" \
  "$preview_root/libadwaita.spec"
install -m 0644 "$sync_root/nautilus-srpm/nautilus.spec" \
  "$preview_root/nautilus.spec"

builder_command='set -euo pipefail
if [ ! -f .dependencies-ready ]; then
  dnf5 -y install rpm-build dnf5-plugins meson ninja-build gcc gettext \
    blueprint-compiler sassc
  dnf5 -y builddep libadwaita.spec nautilus.spec
  touch .dependencies-ready
fi

build_libadwaita() {
  if [ ! -d builds/libadwaita/meson-private ]; then
    meson setup --buildtype=debugoptimized --prefix=/usr --libdir=lib64 \
      builds/libadwaita sources/libadwaita \
      -Ddocumentation=false -Dtests=false -Dexamples=false \
      -Dintrospection=disabled -Dvapi=false
  fi
  meson compile -C builds/libadwaita
}

build_nautilus() {
  if [ ! -d builds/nautilus/meson-private ]; then
    meson setup --buildtype=debugoptimized --prefix=/usr --libdir=lib64 \
      builds/nautilus sources/nautilus \
      -Dtests=none -Ddocs=false -Dextensions=false -Dintrospection=false
  fi

  # The blueprint-compiler batch target is represented by its output directory.
  # Meson reconfiguration can make that directory newer than changed .blp
  # inputs, hiding a real UI edit from Ninja. Content-track the Blueprint set
  # and refresh only those source mtimes when its contents actually changed.
  # Unchanged runs skip the Blueprint batch even if Meson refreshes lighter
  # generated resource bookkeeping.
  blueprint_stamp=builds/nautilus/.luma-blueprint-resources-v3.sha256
  blueprint_checksum=$(
    find sources/nautilus/src/resources/ui -type f -name "*.blp" -print0 |
      sort -z |
      xargs -0 sha256sum |
      sha256sum |
      cut -d" " -f1
  )
  blueprint_previous=$(test -f "$blueprint_stamp" && cat "$blueprint_stamp" || true)
  if [ "$blueprint_checksum" != "$blueprint_previous" ]; then
    # Meson models the Blueprint batch as one directory output. Merely touching
    # a changed .blp can leave that directory newer than its inputs, so Ninja
    # may legally reuse stale generated .ui files. Run the same deterministic
    # batch compiler directly when the content hash changes; the following
    # compile then re-embeds the fresh UI in this same invocation.
    mapfile -d "" blueprint_inputs < <(
      find sources/nautilus/src/resources/ui -type f -name "*.blp" -print0 |
        sort -z
    )
    blueprint-compiler batch-compile --minify \
      builds/nautilus/src/resources/. \
      sources/nautilus/src/resources \
      "${blueprint_inputs[@]}"
    touch sources/nautilus/src/resources/nautilus.gresource.xml.in
  fi
  meson compile -C builds/nautilus
  if [ "$blueprint_checksum" != "$blueprint_previous" ]; then
    printf "%s\n" "$blueprint_checksum" >"$blueprint_stamp"
  fi
}

case "'"$component"'" in
  all) build_libadwaita; build_nautilus ;;
  libadwaita) build_libadwaita ;;
  nautilus) build_nautilus ;;
esac'

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$preview_root" "$FEDORA_RPM_BUILD_CONTAINER" "$builder_command"

printf 'Files preview build is ready at %s\n' "$preview_root"
