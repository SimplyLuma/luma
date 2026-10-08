#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
source "$repo_root/config/desktop/inputs.env"
arch=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$arch" in
  x86_64) builder=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) printf 'error: no pinned Android images for %s\n' "$arch" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$arch
pin="$repo_root/config/android/images-$arch.env"
source "$pin"
cache=${LUMA_ANDROID_IMAGE_CACHE:-"$repo_root/build/cache/android/$arch"}
# Fetching is an explicit build step, never hidden inside the package builder
# or a user's application launch. Verify both immutable cached archives first.
for kind in SYSTEM VENDOR; do
  name_var="LUMA_ANDROID_${kind}_FILENAME"
  size_var="LUMA_ANDROID_${kind}_SIZE"
  hash_var="LUMA_ANDROID_${kind}_SHA256"
  file="$cache/${!name_var}"
  test -f "$file" && test ! -L "$file"
  test "$(stat -c %s "$file")" = "${!size_var}"
  printf '%s  %s\n' "${!hash_var}" "$file" | sha256sum --check --strict
done
output="$repo_root/build/packages/luma-android-images/$arch"
mkdir -p "$output"
# The uncompressed pair exists in both %%prep and BUILDROOT. RPM/SRPM payloads
# and their exported copies can each exist concurrently as well. Account for
# incompressible inputs instead of budgeting only the downloaded ZIPs.
scratch_bytes=$(python3 - "$cache/$LUMA_ANDROID_SYSTEM_FILENAME" \
  "$cache/$LUMA_ANDROID_VENDOR_FILENAME" <<'PY'
from pathlib import Path
import sys, zipfile
raw = 0
archives = 0
for name, filename in zip(('system.img', 'vendor.img'), sys.argv[1:], strict=True):
    path = Path(filename)
    archives += path.stat().st_size
    with zipfile.ZipFile(path) as archive:
        members = [m for m in archive.infolist() if m.filename == name and not m.is_dir()]
        if len(members) != 1 or members[0].file_size <= 0:
            raise SystemExit(f'Expected exactly one image: {name}')
        raw += members[0].file_size
print(raw * 4 + archives * 3 + 1073741824)
PY
)
available_bytes=$(df --output=avail -B1 "$output" | tail -n 1 | tr -d ' ')
if [ "$available_bytes" -lt "$scratch_bytes" ]; then
  printf 'error: Android image packaging needs %s bytes of scratch space; %s available\n' \
    "$scratch_bytes" "$available_bytes" >&2
  exit 1
fi
work=$(mktemp -d "$output/work.XXXXXX")
mkdir -p "$work"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
cp --reflink=auto "$cache/$LUMA_ANDROID_SYSTEM_FILENAME" "$work/SOURCES/android-system.zip"
cp --reflink=auto "$cache/$LUMA_ANDROID_VENDOR_FILENAME" "$work/SOURCES/android-vendor.zip"
install -m0644 "$pin" "$work/SOURCES/images.env"
install -m0644 "$repo_root/docs/research/android-offline-images-20261006.md" "$work/SOURCES/ANDROID-IMAGES.md"
install -m0644 "$repo_root/scripts/android/package-images.py" "$work/SOURCES/package-images.py"
install -m0644 "$repo_root/packaging/rpm/luma-android-images-reference.spec" "$work/SPECS/luma-android-images.spec"
chmod -R a+rX "$work"
"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$work" "$builder" '
  set -euo pipefail
  dnf5 -y install rpm-build python3
  rpmbuild -ba --define "_topdir $PWD" SPECS/luma-android-images.spec
  test "$(find RPMS -name "luma-android-images-*.rpm" | wc -l)" -eq 1
  test "$(find SRPMS -name "luma-android-images-*.src.rpm" | wc -l)" -eq 1
'
mkdir -p "$output/RPMS" "$output/SRPMS"
find "$work/RPMS" -name 'luma-android-images-*.rpm' -exec cp --reflink=auto -t "$output/RPMS" {} +
find "$work/SRPMS" -name 'luma-android-images-*.src.rpm' -exec cp --reflink=auto -t "$output/SRPMS" {} +
find "$output/RPMS" "$output/SRPMS" -type f -name '*.rpm' -print0 |
  sort -z | xargs -0 sha256sum > "$output/SHA256SUMS"
test -s "$output/SHA256SUMS"
