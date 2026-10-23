#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build both Fedora GTK ABIs from the admitted source RPM and upstream patch.
# Usage: build-webkitgtk-luma.sh [builder-image] [admitted-source-rpm]
set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo_root/config/desktop/inputs.env"
. "$repo_root/config/shared/webkitgtk-inputs.env"
arch=$(uname -m)
# Explicit job budget: ordinary builders retain two CPUs; native CI may admit
# four without changing source, checks, or the memory-aware spec build limit.
export LUMA_WEBKIT_BUILD_NCPUS=${RPM_BUILD_NCPUS:-2}
[[ "$LUMA_WEBKIT_BUILD_NCPUS" =~ ^[1-4]$ ]] || { echo 'WebKit CPU budget must be between one and four' >&2; exit 2; }
export LUMA_WEBKIT_DWZ=${LUMA_WEBKIT_DWZ:-1}
case "$LUMA_WEBKIT_DWZ" in 0|1) ;; *) echo 'WebKit DWZ mode must be zero or one' >&2; exit 2 ;; esac
case "$arch" in
  x86_64) builder=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) echo 'Unsupported WebKit architecture' >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$arch
mkdir -p "$repo_root/build"
work_dir=$(mktemp -d "$repo_root/build/webkitgtk.work.XXXXXX")
chmod 0755 "$work_dir"
trap 'rm -rf "$work_dir"' EXIT INT TERM
mkdir -p "$work_dir/rpmbuild"/{SOURCES,SPECS}
srpm="$work_dir/webkitgtk.src.rpm"
if [ -n "${2:-}" ]; then
  cp -- "$2" "$srpm"
else
  curl --fail --location --retry 2 --proto '=https' --tlsv1.2 \
    "$WEBKITGTK_SOURCE_RPM_URL" --output "$srpm"
fi
printf '%s  %s\n' "$WEBKITGTK_SOURCE_RPM_SHA256" "$srpm" | sha256sum --check --strict
# The admitted SRPM digest fixes every member, including upstream signing keys.
# Extraction runs in the ordinary build workspace, never an installed image.
rpm2cpio "$srpm" > "$work_dir/source.cpio"
(cd "$work_dir/rpmbuild/SOURCES" && cpio -idm --no-absolute-filenames < "$work_dir/source.cpio")
for member in webkitgtk-2.54.1.tar.xz webkitgtk-2.54.1.tar.xz.asc webkitgtk-keys.gpg skia-s390x.patch; do
  test -f "$work_dir/rpmbuild/SOURCES/$member"
done
prerequisite="$repo_root/patches/webkitgtk/0000-native-touch-constructor-prerequisite.patch"
printf '%s  %s\n' "$WEBKITGTK_NATIVE_TOUCH_PREREQUISITE_SHA256" "$prerequisite" | sha256sum --check --strict
cp "$prerequisite" "$work_dir/rpmbuild/SOURCES/"
patch="$repo_root/patches/webkitgtk/0001-native-touch-surface-transform-and-cancel.patch"
printf '%s  %s\n' "$WEBKITGTK_NATIVE_TOUCH_PATCH_SHA256" "$patch" | sha256sum --check --strict
cp "$patch" "$work_dir/rpmbuild/SOURCES/"
cp "$repo_root/packaging/rpm/webkitgtk-luma.spec" "$work_dir/rpmbuild/SPECS/"
bash "$repo_root/scripts/packages/run-in-rpm-builder.sh" "$work_dir/rpmbuild" "${1:-$builder}" "
  export LUMA_WEBKIT_BUILD_NCPUS=$LUMA_WEBKIT_BUILD_NCPUS
  export LUMA_WEBKIT_DWZ=$LUMA_WEBKIT_DWZ
"'
  set -euo pipefail
  dnf5 -q -y install rpm-build dnf5-plugins
  dnf5 -q -y builddep SPECS/webkitgtk-luma.spec
  sha256sum SOURCES/* SPECS/webkitgtk-luma.spec > ADMITTED-INPUT-SHA256SUMS
  id conform >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash conform
  work=$PWD
  debug_options=()
  if [ "$LUMA_WEBKIT_DWZ" = 0 ]; then
    # Preserve full DWARF and debug RPM generation. Only optional DWZ
    # deduplication is omitted on explicitly resource-bounded workers.
    debug_options+=(--define "_find_debuginfo_dwz_opts %{nil}")
    rpm "${debug_options[@]}" --eval "%{__debug_install_post}" > ADMITTED-RESOURCE-OPTIONS.txt
    grep -Fq /usr/bin/find-debuginfo ADMITTED-RESOURCE-OPTIONS.txt
    grep -Fq debugsourcefiles.list ADMITTED-RESOURCE-OPTIONS.txt
    ! grep -Fq -- --run-dwz ADMITTED-RESOURCE-OPTIONS.txt
    printf "Full DWARF/debug packages retained; optional DWZ deduplication disabled\n" >> ADMITTED-RESOURCE-OPTIONS.txt
  else
    printf "Default Fedora debug generation and DWZ deduplication\n" > ADMITTED-RESOURCE-OPTIONS.txt
  fi
  printf "Build CPU budget: %s\n" "$LUMA_WEBKIT_BUILD_NCPUS" >> ADMITTED-RESOURCE-OPTIONS.txt
  cat ADMITTED-RESOURCE-OPTIONS.txt
  chown -R conform:conform "$work"
  runuser -u conform -- env CMAKE_BUILD_PARALLEL_LEVEL="$LUMA_WEBKIT_BUILD_NCPUS" rpmbuild -ba "${debug_options[@]}" --without docs \
    --define "_topdir $work" --define "_smp_build_ncpus $LUMA_WEBKIT_BUILD_NCPUS" \
    --define "_smp_mflags -j$LUMA_WEBKIT_BUILD_NCPUS" --define "_default_patch_flags -p1 --fuzz=0" SPECS/webkitgtk-luma.spec
'
# A successful transaction must produce both engine/JavaScriptCore ABI pairs.
for family in webkitgtk6.0 javascriptcoregtk6.0 webkit2gtk4.1 javascriptcoregtk4.1; do
  count=$(find "$work_dir/rpmbuild/RPMS/$arch" -name "$family-[0-9]*.rpm" -type f | wc -l)
  [ "$count" -eq 1 ] || { printf 'Missing or ambiguous WebKit output %s: %s\n' "$family" "$count" >&2; exit 1; }
done
output_dir="$repo_root/build/packages/webkitgtk/$arch"
mkdir -p "$output_dir/RPMS/$arch" "$output_dir/SRPMS"
# Refuse overwrite: one NEVRA can never mean two different builds.
for source in "$work_dir/rpmbuild/RPMS/$arch/"*.rpm "$work_dir/rpmbuild/SRPMS/"*.rpm; do
  relative=${source#"$work_dir/rpmbuild/"}
  destination="$output_dir/$relative"
  if [ -e "$destination" ]; then cmp "$source" "$destination"; else install -m0644 "$source" "$destination"; fi
done
cp "$work_dir/rpmbuild/ADMITTED-INPUT-SHA256SUMS" "$output_dir/ADMITTED-INPUT-SHA256SUMS"
cp "$work_dir/rpmbuild/ADMITTED-RESOURCE-OPTIONS.txt" "$output_dir/ADMITTED-RESOURCE-OPTIONS.txt"
(cd "$output_dir" && find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS)
printf 'WebKit packages built: %s\n' "$output_dir"
