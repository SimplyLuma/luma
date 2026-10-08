#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Assemble the validated Lomiri packages into the same Ubuntu ARM64 runtime as
# KWin/Maliit, without installing anything on the physical phone.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
package_dir="$repo_root/build/mobile/keyboard-labs-fp6/lomiri-1.1.0"
output_dir="$repo_root/build/mobile/keyboard-labs-fp6"
base_image=localhost/luma-lomiri-runtime:questing
runtime_image=localhost/luma-lomiri-runtime:1.1.0
bundle="$output_dir/luma-keyboard-lomiri-1.1.0-aarch64.tar.zst"

for tool in podman sha256sum tar zstd; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required Lomiri runtime packager is missing: %s\n' "$tool" >&2
    exit 1
  }
done
if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  printf 'error: package the FP6 Lomiri lab on the canonical Linux/x86_64 builder\n' >&2
  exit 1
fi

for metadata in SHA256SUMS SOURCE_COMMIT SOURCE_TAG; do
  test -f "$package_dir/$metadata" || {
    printf 'error: missing validated Lomiri metadata: %s\n' "$metadata" >&2
    exit 1
  }
done
test "$(cat "$package_dir/SOURCE_TAG")" = 1.1.0
test "$(cat "$package_dir/SOURCE_COMMIT")" = \
  a01d50b160fc8d5375fb208b20ae5781b5ce912e
(cd "$package_dir" && sha256sum --check --status SHA256SUMS)

for package in lomiri-keyboard lomiri-keyboard-data lomiri-keyboard-english \
  lomiri-keyboard-emoji; do
  find "$package_dir" -maxdepth 1 -type f -name "${package}_1.1.0*.deb" \
    -print -quit | grep -q . || {
      printf 'error: missing validated Lomiri package: %s\n' "$package" >&2
      exit 1
    }
done

container=luma-lomiri-runtime-110-$$
cleanup() {
  podman rm -f "$container" >/dev/null 2>&1 || true
}
trap cleanup EXIT

podman create --name "$container" --arch arm64 \
  -v "$package_dir:/packages:ro,Z" "$base_image" bash -lc '
    set -euo pipefail
    export DEBIAN_FRONTEND=noninteractive
    apt-get update >/dev/null
    apt-get install -y /packages/lomiri-keyboard_1.1.0*.deb \
      /packages/lomiri-keyboard-data_1.1.0*.deb \
      /packages/lomiri-keyboard-english_1.1.0*.deb \
      /packages/lomiri-keyboard-emoji_1.1.0*.deb \
      qml-module-qtmultimedia session-migration >/dev/null
    dpkg-query -W -f="\${Package}\t\${Version}\t\${Architecture}\n" \
      lomiri-keyboard lomiri-keyboard-data lomiri-keyboard-english \
      lomiri-keyboard-emoji >/LOMIRI_KEYBOARD_PACKAGES
    grep -Eq "^lomiri-keyboard[[:space:]]+1.1.0.*[[:space:]]+arm64$" \
      /LOMIRI_KEYBOARD_PACKAGES
    grep -Eq "^lomiri-keyboard-data[[:space:]]+1.1.0.*[[:space:]]+all$" \
      /LOMIRI_KEYBOARD_PACKAGES
    grep -Eq "^lomiri-keyboard-english[[:space:]]+1.1.0.*[[:space:]]+arm64$" \
      /LOMIRI_KEYBOARD_PACKAGES
    grep -Eq "^lomiri-keyboard-emoji[[:space:]]+1.1.0.*[[:space:]]+arm64$" \
      /LOMIRI_KEYBOARD_PACKAGES
    plugin=$(find /usr -type f -name liblomiri-keyboard-plugin.so -print -quit)
    test "$(dpkg --print-architecture)" = arm64
    test -s "$plugin"
    dpkg-query -W qml-module-qtmultimedia session-migration >/dev/null
    test -x /usr/bin/kwin_wayland
    test -x /usr/bin/maliit-server
    test -x /usr/bin/dbus-run-session
  ' >/dev/null
podman start --attach "$container"
podman commit "$container" "$runtime_image" >/dev/null

probe_container=${container}-probe
podman create --name "$probe_container" --arch arm64 "$runtime_image" \
  bash -lc 'cat /LOMIRI_KEYBOARD_PACKAGES' >/dev/null
podman start --attach "$probe_container"
podman export "$probe_container" | zstd -T0 -5 -f -o "$bundle"
podman rm "$probe_container" >/dev/null

sha256sum "$bundle" >"$bundle.sha256"
printf '%s\n' 1.1.0 >"$bundle.source-tag"
printf '%s\n' a01d50b160fc8d5375fb208b20ae5781b5ce912e \
  >"$bundle.source-commit"
printf 'Lomiri Keyboard 1.1.0 FP6 runtime: %s\n' "$bundle"
