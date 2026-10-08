#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Gather the Luma RPMs the runtime is assembled from into one dnf repository.
#
# The packages are the ones scripts/packages/build-*.sh produce from this
# repository (GTK 4, libadwaita, the developer platform, the Prairie icon
# theme and Luma's fonts), plus flatpak-runtime-config from
# build-flatpak-runtime-config.sh. Their NEVRAs are checked against
# config/desktop/inputs.env so the runtime can only carry what the operating
# system pins, and the resulting manifest records every file hash.
#
#   collect-luma-rpms.sh [BUILD_PACKAGES_DIR]
set -euo pipefail
. "$(dirname -- "$0")/lib.sh"
. "$depot_repo_root/config/desktop/inputs.env"

packages_dirs=("$@")
[ "${#packages_dirs[@]}" -gt 0 ] || packages_dirs=("$depot_repo_root/build/packages")
for directory in "${packages_dirs[@]}"; do
  [ -d "$directory" ] || depot_die "no package build output at $directory"
done

# Architecture-specific pins are authoritative; the desktop release variable
# must never silently substitute an x86_64 toolkit on an ARM runtime.
case "$DEPOT_ARCH" in
  x86_64) gtk=$GTK4_NEVRA; adwaita=$LIBADWAITA_NEVRA; platform=$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA ;;
  aarch64) gtk=$GTK4_AARCH64_NEVRA; adwaita=$LIBADWAITA_AARCH64_NEVRA; platform=$LUMA_DEVELOPER_PLATFORM_AARCH64_NEVRA ;;
  *) depot_die "unsupported runtime architecture: $DEPOT_ARCH" ;;
esac
required=("$gtk" "${gtk/gtk4-/gtk4-devel-}" "${gtk/gtk4-/gtk4-devel-tools-}"
  "$adwaita" "${adwaita/libadwaita-/libadwaita-devel-}"
  "$platform" "${platform/luma-developer-platform-/luma-developer-platform-devel-}"
  "$LUMA_DEVELOPER_PLATFORM_SDK_NEVRA" "$FIGTREE_NEVRA" "$CAVEAT_NEVRA"
  "$PRAIRIE_ICON_THEME_NEVRA" "flatpak-runtime-config-${DEPOT_FEDORA_RELEASE}-1.luma.1.fc${DEPOT_FEDORA_RELEASE}.${DEPOT_ARCH}")

# The owner-patched WebKit pairs are runtime inputs, including their matching
# SDK headers. Supplying just a package directory cannot silently omit them.
if [ -n "${WEBKITGTK6_NEVRA:-}${JAVASCRIPTCOREGTK6_NEVRA:-}${WEBKIT2GTK41_NEVRA:-}${JAVASCRIPTCOREGTK41_NEVRA:-}" ]; then
  : "${WEBKITGTK6_NEVRA:?the coherent owner WebKit6 pin is required}"
  : "${JAVASCRIPTCOREGTK6_NEVRA:?the coherent owner JavaScriptCore6 pin is required}"
  : "${WEBKIT2GTK41_NEVRA:?the coherent owner WebKit4.1 pin is required}"
  : "${JAVASCRIPTCOREGTK41_NEVRA:?the coherent owner JavaScriptCore4.1 pin is required}"
  for owner_identity in "$WEBKITGTK6_NEVRA" "$JAVASCRIPTCOREGTK6_NEVRA" "$WEBKIT2GTK41_NEVRA" "$JAVASCRIPTCOREGTK41_NEVRA"; do
    [[ "$owner_identity" == *".$DEPOT_ARCH" ]] || depot_die "owner WebKit tuple does not match runtime architecture $DEPOT_ARCH"
  done
  required+=("$WEBKITGTK6_NEVRA" "${WEBKITGTK6_NEVRA/webkitgtk6.0-/webkitgtk6.0-devel-}"
    "$JAVASCRIPTCOREGTK6_NEVRA" "${JAVASCRIPTCOREGTK6_NEVRA/javascriptcoregtk6.0-/javascriptcoregtk6.0-devel-}"
    "$WEBKIT2GTK41_NEVRA" "${WEBKIT2GTK41_NEVRA/webkit2gtk4.1-/webkit2gtk4.1-devel-}"
    "$JAVASCRIPTCOREGTK41_NEVRA" "${JAVASCRIPTCOREGTK41_NEVRA/javascriptcoregtk4.1-/javascriptcoregtk4.1-devel-}")
fi

install -d -m 0755 "$(dirname "$depot_rpms")" "$DEPOT_ROOT/locks"
exec 9>"$DEPOT_ROOT/locks/runtime-rpms-$DEPOT_ARCH.lock"
flock -w 120 9 || depot_die "another runtime repository operation is active"
staging=$(mktemp -d "${depot_rpms}.partial.XXXXXX")
trap 'rm -rf "$staging"' EXIT
for identity in "${required[@]}"; do
  mapfile -d '' matches < <(find "${packages_dirs[@]}" -type f -name "$identity.rpm" -print0)
  [ "${#matches[@]}" -gt 0 ] || depot_die "missing exact runtime RPM $identity in the supplied package output directories"
  expected_sha=
  for match in "${matches[@]}"; do
    actual=$(rpm -qp --nosignature --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}' "$match") || depot_die "invalid RPM: $match"
    [ "$actual" = "$identity" ] || depot_die "RPM filename/header mismatch: $match carries $actual"
    sha=$(sha256sum "$match" | cut -d' ' -f1)
    [ -z "$expected_sha" ] || [ "$sha" = "$expected_sha" ] || depot_die "conflicting bytes for $identity"
    expected_sha=$sha
  done
  install -m 0644 "${matches[0]}" "$staging/$identity.rpm"
  depot_log "collected exact $identity ($expected_sha)"
done
depot_in_tools createrepo_c --quiet "$staging"
(cd "$staging" && sha256sum ./*.rpm) >"$staging/SHA256SUMS"
# A missing or rejected input leaves the previous coherent runtime repository
# available. Once complete, retain it for recovery rather than deleting it.
if [ -e "$depot_rpms" ]; then
  previous="${depot_rpms}.previous.$(date -u +%Y%m%dT%H%M%S)-$$"
  mv "$depot_rpms" "$previous"
fi
mv "$staging" "$depot_rpms"
depot_log "repository ready: $depot_rpms"
