#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Initialize and sync the official LineageOS/Waydroid source tree used for
# Luma's vendor images. Build output stays outside the repository; the exact
# resolved manifest is captured after sync and becomes the immutable input.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck source=/dev/null
source "$repo_root/config/android/waydroid-source.env"

source_root=${LUMA_WAYDROID_SOURCE_ROOT:-$repo_root/build/android/lineage-20}
jobs=${LUMA_WAYDROID_SYNC_JOBS:-8}

command -v repo >/dev/null || {
  printf 'error: Android repo tool is required\n' >&2
  exit 1
}
command -v git-lfs >/dev/null || {
  printf 'error: git-lfs is required\n' >&2
  exit 1
}

install -d -m 0755 "$source_root"
cd "$source_root"

if [ ! -d .repo ]; then
  repo init \
    -u https://github.com/LineageOS/android.git \
    -b "$LUMA_LINEAGE_MANIFEST_REVISION" \
    --depth=1 \
    --repo-rev=97dc5c1bd9527c2abe2183b16a4b7ef037dc34a7 \
    --git-lfs \
    --no-clone-bundle
fi

repo sync build/make --current-branch --no-clone-bundle --jobs="$jobs"

manifest_base="https://raw.githubusercontent.com/waydroid/android_vendor_waydroid/$LUMA_WAYDROID_VENDOR_REVISION/manifest_scripts"
manifest_directory=$(mktemp -d)
manifest_script="$manifest_directory/generate-manifest.sh"
trap 'rm -rf "$manifest_directory"' EXIT
curl --fail --location --silent --show-error \
  --output "$manifest_script" "$manifest_base/generate-manifest.sh"
printf '%s  %s\n' \
  "$LUMA_WAYDROID_MANIFEST_GENERATOR_SHA256" "$manifest_script" | sha256sum --check --status
mkdir "$manifest_directory/manifests-33"
for fragment in 00-remotes.xml 01-removes.xml 02-waydroid.xml; do
  curl --fail --location --silent --show-error \
    --output "$manifest_directory/manifests-33/$fragment" \
    "$manifest_base/manifests-33/$fragment"
done
TERM=${TERM:-xterm} bash "$manifest_script"
rm -rf "$manifest_directory"
trap - EXIT

cat > .repo/local_manifests/99-luma-pins.xml <<EOF
<manifest>
  <extend-project name="WayDroid/android_hardware_waydroid" revision="$LUMA_WAYDROID_HARDWARE_REVISION" />
  <extend-project name="WayDroid/android_vendor_waydroid" revision="$LUMA_WAYDROID_VENDOR_REVISION" />
  <extend-project name="WayDroid/android_device_waydroid_waydroid" revision="$LUMA_WAYDROID_DEVICE_REVISION" />
  <extend-project name="LineageOS/android_frameworks_base" revision="$LUMA_ANDROID_FRAMEWORKS_BASE_REVISION" />
  <extend-project name="LineageOS/android_hardware_libhardware" revision="$LUMA_ANDROID_LIBHARDWARE_REVISION" />
</manifest>
EOF

repo sync --current-branch --no-clone-bundle --jobs="$jobs"

test "$(git -C hardware/waydroid rev-parse HEAD)" = \
  "$LUMA_WAYDROID_HARDWARE_REVISION"
test "$(git -C vendor/extra rev-parse HEAD)" = \
  "$LUMA_WAYDROID_VENDOR_REVISION"

install -d -m 0755 "$repo_root/build/android/manifests"
repo manifest -r -o \
  "$repo_root/build/android/manifests/lineage-20-resolved.xml"

printf 'Waydroid source synchronized at %s\n' "$source_root"
