#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
source_url=https://gitlab.com/ubports/development/core/lomiri-keyboard.git
source_tag=1.1.0
source_commit=a01d50b160fc8d5375fb208b20ae5781b5ce912e
ubuntu_image=docker.io/library/ubuntu:questing
output_dir="$repo_root/build/mobile/keyboard-labs-fp6/lomiri-1.1.0"

for tool in git mktemp podman sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required Lomiri keyboard builder is missing: %s\n' "$tool" >&2
    exit 1
  }
done
if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  printf 'error: build the FP6 Lomiri lab on the canonical Linux/x86_64 builder\n' >&2
  exit 1
fi

work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
container=luma-lomiri-keyboard-110-$$
cleanup() {
  podman rm -f "$container" >/dev/null 2>&1 || true
  rm -rf "$work_dir"
}
trap cleanup EXIT

git clone --branch "$source_tag" --depth 1 "$source_url" "$work_dir/source"
actual_commit=$(git -C "$work_dir/source" rev-parse HEAD)
actual_tag=$(git -C "$work_dir/source" describe --tags --exact-match)
if [ "$actual_commit" != "$source_commit" ] || [ "$actual_tag" != "$source_tag" ]; then
  printf 'error: Lomiri source identity differs: tag=%s commit=%s\n' \
    "$actual_tag" "$actual_commit" >&2
  exit 1
fi

podman run --name "$container" --arch arm64 \
  -v "$work_dir:/work:Z" "$ubuntu_image" bash -lc '
    set -euo pipefail
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends \
      build-essential ca-certificates devscripts equivs fakeroot file
    cd /work/source
    yes | mk-build-deps --install --remove --tool apt-get debian/control
    dpkg-buildpackage -b -uc -us -j2
  '

stage_dir="$work_dir/stage"
mkdir -p "$stage_dir"
for package in \
  lomiri-keyboard \
  lomiri-keyboard-data \
  lomiri-keyboard-english \
  lomiri-keyboard-emoji; do
  artifact=$(find "$work_dir" -maxdepth 1 -type f \
    \( -name "${package}_1.1.0*_arm64.deb" -o \
       -name "${package}_1.1.0*_all.deb" \) | head -n 1)
  if [ -z "$artifact" ]; then
    printf 'error: build did not produce %s 1.1.0\n' "$package" >&2
    exit 1
  fi
  install -m 0644 "$artifact" "$stage_dir/"
done

podman run --rm --arch arm64 \
  -v "$stage_dir:/packages:Z" "$ubuntu_image" bash -lc '
    set -euo pipefail
    apt-get update >/dev/null
    apt-get install -y --no-install-recommends file >/dev/null
    mkdir -p /verify
    for package in /packages/*.deb; do
      dpkg-deb --info "$package" >/dev/null
      dpkg-deb --extract "$package" /verify
    done
    plugin=$(find /verify/usr -type f -name "liblomiri-keyboard-plugin.so" -print -quit)
    test -n "$plugin"
    file "$plugin" | grep -q "ARM aarch64"
    test -f /verify/usr/share/maliit/plugins/lomiri-keyboard/qml/Keyboard.qml
    find /verify/usr -type f -path "*lomiri-keyboard*emoji*" \
      -print -quit | grep -q .
    find /verify/usr/share -type f \
      \( -iname "*en*.db" -o -iname "en*.dic" -o -iname "en*.aff" \) \
      -print -quit | grep -q .
  '

rm -rf "$output_dir"
install -d -m 0755 "$output_dir"
install -m 0644 "$stage_dir"/*.deb "$output_dir/"
printf '%s\n' "$source_commit" >"$output_dir/SOURCE_COMMIT"
printf '%s\n' "$source_tag" >"$output_dir/SOURCE_TAG"
(
  cd "$output_dir"
  sha256sum ./*.deb SOURCE_COMMIT SOURCE_TAG >SHA256SUMS
)

printf 'Lomiri Keyboard 1.1.0 FP6 artifacts: %s\n' "$output_dir"
