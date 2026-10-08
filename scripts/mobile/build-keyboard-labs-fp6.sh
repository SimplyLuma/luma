#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in curl mktemp podman sha256sum tar zstd; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required keyboard-lab builder is missing: %s\n' "$tool" >&2
    exit 1
  }
done
if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  printf 'error: build FP6 keyboard labs on the canonical Linux/x86_64 builder\n' >&2
  exit 1
fi

output_dir="$repo_root/build/mobile/keyboard-labs-fp6"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
mkdir -p "$work_dir/squeekboard" "$output_dir"

squeekboard_rpm="$work_dir/squeekboard.rpm"
curl -L --fail --silent --show-error \
  -o "$squeekboard_rpm" "$SQUEEKBOARD_LAB_RPM_URL"
printf '%s  %s\n' "$SQUEEKBOARD_LAB_RPM_SHA256" "$squeekboard_rpm" |
  sha256sum --check --status
podman run --rm --arch arm64 \
  -v "$work_dir:/work:Z" "$FEDORA_RPM_BUILD_CONTAINER" bash -lc '
    set -euo pipefail
    dnf5 -y install rpm-build cpio >/dev/null
    cd /work/squeekboard
    rpm2cpio /work/squeekboard.rpm | cpio -idm --quiet
    test -x usr/bin/squeekboard
    test -f usr/share/glib-2.0/schemas/sm.puri.Squeekboard.gschema.xml
    install -D -m 0755 usr/bin/squeekboard /work/squeekboard.bin
    install -D -m 0644 \
      usr/share/glib-2.0/schemas/sm.puri.Squeekboard.gschema.xml \
      /work/sm.puri.Squeekboard.gschema.xml
  '

podman run --rm --arch arm64 \
  -v "$work_dir:/work:Z" docker.io/library/debian:trixie bash -lc '
    set -euo pipefail
    apt-get update >/dev/null
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
      "wvkbd='"$WVKBD_LAB_VERSION"'" >/dev/null
    install -m 0755 /usr/bin/wvkbd-mobintl /work/wvkbd-mobintl
  '
printf '%s  %s\n' "$WVKBD_LAB_BINARY_SHA256" "$work_dir/wvkbd-mobintl" |
  sha256sum --check --status

container=luma-keyboard-labs-fp6-$$
cleanup() {
  podman rm -f "$container" >/dev/null 2>&1 || true
  rm -rf "$work_dir"
}
trap cleanup EXIT
podman create --name "$container" --arch arm64 \
  "$FEDORA_RPM_BUILD_CONTAINER" bash -lc '
    set -euo pipefail
    dnf5 -y install kwin plasma-keyboard maliit-keyboard gtk3 \
      python3-gobject-base >/dev/null
    rpm -q kwin plasma-keyboard maliit-keyboard
  ' >/dev/null
podman start --attach "$container"

kde_bundle="$work_dir/luma-keyboard-kde-f44-aarch64.tar.zst"
podman export "$container" | zstd -T0 -5 -o "$kde_bundle"

rm -rf "$output_dir"
install -d -m 0755 "$output_dir"
install -m 0755 "$work_dir/squeekboard.bin" "$output_dir/squeekboard"
install -m 0644 "$work_dir/sm.puri.Squeekboard.gschema.xml" "$output_dir/"
install -m 0755 "$work_dir/wvkbd-mobintl" "$output_dir/"
install -m 0644 "$kde_bundle" "$output_dir/"
(
  cd "$output_dir"
  sha256sum squeekboard sm.puri.Squeekboard.gschema.xml wvkbd-mobintl \
    luma-keyboard-kde-f44-aarch64.tar.zst >SHA256SUMS
)

printf 'FP6 keyboard lab artifacts: %s\n' "$output_dir"
