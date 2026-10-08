#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in cpio curl git mktemp podman rpm rpm2cpio sha256sum strings; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required Wine build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = x86_64 ] || {
  printf 'error: build the Relay Wine integration on Linux/x86_64\n' >&2
  exit 1
}

cache_dir="$repo_root/build/cache/srpm"
output_dir="$repo_root/build/packages/wine-relay-integration"
mkdir -p "$cache_dir" "$output_dir"
srpm="$cache_dir/$WINE_SRPM"
if [ ! -f "$srpm" ]; then
  curl -fL --retry 3 -o "$srpm" "$WINE_SRPM_URL"
fi
printf '%s  %s\n' "$WINE_SRPM_SHA256" "$srpm" | sha256sum --check --status || {
  printf 'error: Wine source RPM checksum mismatch\n' >&2
  exit 1
}

# Wine is by far the most expensive package in the desktop batch. Reuse it
# only when the complete reviewed input identity and the recorded output
# checksums both match. Bump the recipe version whenever build behavior (not
# merely verification or cache plumbing) changes.
wine_cache_recipe=1
cache_identity=$(
  {
    printf 'recipe=%s\n' "$wine_cache_recipe"
    printf 'container=%s\n' "$FEDORA_RPM_BUILD_CONTAINER"
    printf 'srpm=%s\n' "$WINE_SRPM_SHA256"
    printf 'integration_nevra=%s\n' "$WINE_RELAY_EXPLORER_NEVRA"
    sha256sum \
      "$repo_root/patches/wine/0000-luma-fedora-spec.patch" \
      "$repo_root/patches/wine/0001-luma-relay-native-notifications.patch"
  } | sha256sum | awk '{ print $1 }'
)
cache_identity_file="$output_dir/BUILD-INPUT-SHA256"
integration_output="$output_dir/RPMS/x86_64/$WINE_RELAY_EXPLORER_NEVRA.rpm"
source_output="$output_dir/SRPMS/wine-11.0-${WINE_LUMA_RELEASE}.fc44.src.rpm"

verify_integration_payload() {
  integration_rpm=$1
  verify_dir=$(mktemp -d "$output_dir/verify.XXXXXX")
  verify_status=0
  (
    set -e
    cd "$verify_dir"
    rpm2cpio "$integration_rpm" | cpio -idm --quiet
    test -x usr/libexec/luma-relay/wine/x86_64-windows/explorer.exe
    # PE DLLs are data payloads to the Linux filesystem and Fedora correctly
    # ships them without Unix execute bits.
    test -f usr/lib64/wine-wow64/wine/x86_64-windows/luma_relay.dll
    test -f usr/lib64/wine-wow64/wine/i386-windows/luma_relay.dll
    test -x usr/lib64/wine-wow64/wine/x86_64-unix/luma_relay.so
    strings usr/libexec/luma-relay/wine/x86_64-windows/explorer.exe >explorer.strings
    grep -Fxq LUMA_RELAY_NOTIFY_SOCKET explorer.strings
    grep -Fxq LUMA_RELAY_NOTIFICATIONS explorer.strings
    strings usr/lib64/wine-wow64/wine/x86_64-unix/luma_relay.so >relay-unix.strings
    grep -Fxq LUMA_RELAY_NOTIFY_SOCKET relay-unix.strings
  ) || verify_status=$?
  rm -rf -- "$verify_dir"
  return "$verify_status"
}

if [ -f "$integration_output" ] && [ -f "$source_output" ] && \
  [ -f "$output_dir/SHA256SUMS" ] && \
  (cd "$output_dir" && sha256sum --check --status SHA256SUMS); then
  recorded_identity=$(cat "$cache_identity_file" 2>/dev/null || true)
  if [ "$recorded_identity" = "$cache_identity" ]; then
    printf 'Relay Wine integration (verified cache): %s\n' "$output_dir"
    exit 0
  fi
  if [ "${LUMA_ADMIT_EXISTING_WINE_BUILD:-0}" = 1 ] && \
    [ -z "$recorded_identity" ]; then
    verify_integration_payload "$integration_output"
    printf '%s\n' "$cache_identity" >"$cache_identity_file"
    printf 'Relay Wine integration admitted to verified cache: %s\n' \
      "$output_dir"
    exit 0
  fi
fi

# If rpmbuild completed but a historical verifier rejected only its file-mode
# assertion, admit that exact retained work directory without recompilation.
# The path is constrained to this package's work tree and both RPM identities
# and the complete integration payload are checked again before promotion.
if [ -n "${LUMA_ADMIT_COMPLETED_WINE_RPMBUILD:-}" ]; then
  completed_rpmbuild=$(realpath "$LUMA_ADMIT_COMPLETED_WINE_RPMBUILD")
  case "$completed_rpmbuild" in
    "$output_dir"/work.*/rpmbuild) ;;
    *)
      printf 'error: completed Wine rpmbuild is outside the guarded work tree\n' >&2
      exit 1
      ;;
  esac
  completed_integration="$completed_rpmbuild/RPMS/x86_64/$WINE_RELAY_EXPLORER_NEVRA.rpm"
  completed_source="$completed_rpmbuild/SRPMS/wine-11.0-${WINE_LUMA_RELEASE}.fc44.src.rpm"
  test -f "$completed_integration" && test -f "$completed_source" || {
    printf 'error: completed Wine rpmbuild is missing its expected outputs\n' >&2
    exit 1
  }
  [ "$(rpm -qp --qf '%{NEVRA}' "$completed_integration")" = \
    "$WINE_RELAY_EXPLORER_NEVRA" ] || {
    printf 'error: completed Wine integration has the wrong package identity\n' >&2
    exit 1
  }
  if [ "$(rpm -qp --qf '%{NEVRA}' "$completed_source")" != \
      "wine-11.0-${WINE_LUMA_RELEASE}.fc44.x86_64" ] || \
    [ "$(rpm -qp --qf '%{SOURCEPACKAGE}' "$completed_source")" != 1 ]; then
    printf 'error: completed Wine source RPM has the wrong package identity\n' >&2
    exit 1
  fi
  verify_integration_payload "$completed_integration"
  install -d -m 0755 "$output_dir/RPMS/x86_64" "$output_dir/SRPMS"
  install -m 0644 "$completed_integration" "$integration_output"
  install -m 0644 "$completed_source" "$source_output"
  (
    cd "$output_dir"
    find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
  )
  printf '%s\n' "$cache_identity" >"$cache_identity_file"
  printf 'Relay Wine integration admitted from completed rpmbuild: %s\n' \
    "$output_dir"
  exit 0
fi

work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
extract_dir="$work_dir/srpm"
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$extract_dir" "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
(
  cd "$extract_dir"
  rpm2cpio "$srpm" | cpio -idm --quiet
)
mv "$extract_dir/wine.spec" "$rpmbuild_dir/SPECS/"
find "$extract_dir" -maxdepth 1 -type f -exec mv -t "$rpmbuild_dir/SOURCES" {} +
install -m 0644 "$repo_root/patches/wine/0001-luma-relay-native-notifications.patch" \
  "$rpmbuild_dir/SOURCES/"
(
  cd "$rpmbuild_dir/SPECS"
  git apply "$repo_root/patches/wine/0000-luma-fedora-spec.patch"
)

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$FEDORA_RPM_BUILD_CONTAINER" '
    set -euo pipefail
    dnf5 -y install rpm-build dnf5-plugins
    dnf5 -y builddep SPECS/wine.spec
    rpmbuild -ba --define "_topdir $PWD" SPECS/wine.spec
    integration=$(find RPMS/x86_64 -name "wine-luma-relay-explorer-*.rpm" -print -quit)
    test -n "$integration"
    verify=$(mktemp -d)
    cd "$verify"
    rpm2cpio "$OLDPWD/$integration" >integration.cpio
    cpio -idm --quiet <integration.cpio
    rm -f integration.cpio
    test -x usr/libexec/luma-relay/wine/x86_64-windows/explorer.exe
    test -f usr/lib64/wine-wow64/wine/x86_64-windows/luma_relay.dll
    test -f usr/lib64/wine-wow64/wine/i386-windows/luma_relay.dll
    test -x usr/lib64/wine-wow64/wine/x86_64-unix/luma_relay.so
    strings usr/libexec/luma-relay/wine/x86_64-windows/explorer.exe >explorer.strings
    grep -Fxq LUMA_RELAY_NOTIFY_SOCKET explorer.strings
    grep -Fxq LUMA_RELAY_NOTIFICATIONS explorer.strings
    strings usr/lib64/wine-wow64/wine/x86_64-unix/luma_relay.so >relay-unix.strings
    grep -Fxq LUMA_RELAY_NOTIFY_SOCKET relay-unix.strings
  '

stable_rpms="$output_dir/RPMS/x86_64"
stable_srpms="$output_dir/SRPMS"
install -d -m 0755 "$stable_rpms" "$stable_srpms"
install -m 0644 "$rpmbuild_dir/RPMS/x86_64/$WINE_RELAY_EXPLORER_NEVRA.rpm" \
  "$stable_rpms/"
install -m 0644 "$rpmbuild_dir/SRPMS/wine-11.0-${WINE_LUMA_RELEASE}.fc44.src.rpm" \
  "$stable_srpms/"
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)
printf '%s\n' "$cache_identity" >"$cache_identity_file"
printf 'Relay Wine integration: %s\n' "$output_dir"
