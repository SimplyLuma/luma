#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build the Luma Emulator: the runtime, the Mac application bundle, and the
# `luma-emulator` command.
#
# Virtualization.framework refuses to create a virtual machine for a process
# that does not carry the com.apple.security.virtualization entitlement, and
# SwiftPM produces unsigned binaries. The signing step below is therefore not
# optional polish: without it the emulator cannot start a guest at all.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
package_root="$repo_root/tools/luma-emulator"
configuration=${LUMA_EMULATOR_CONFIGURATION:-release}
output_root="$repo_root/build/emulator"
app="$output_root/Luma Emulator.app"

if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
  printf 'error: the Luma Emulator builds on Apple Silicon macOS only (found %s/%s)\n' \
    "$(uname -s)" "$(uname -m)" >&2
  exit 1
fi

for tool in swift codesign plutil; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

printf '=== Building (%s)\n' "$configuration"
swift build --package-path "$package_root" -c "$configuration"
binary_directory=$(swift build --package-path "$package_root" -c "$configuration" --show-bin-path)

printf '=== Assembling %s\n' "$app"
rm -rf "$app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
install -m 0644 "$package_root/Resources/Info.plist" "$app/Contents/Info.plist"
plutil -lint "$app/Contents/Info.plist" >/dev/null
install -m 0755 "$binary_directory/LumaEmulatorHost" "$app/Contents/MacOS/LumaEmulatorHost"
install -m 0755 "$binary_directory/luma-emulator" "$app/Contents/MacOS/luma-emulator"
install -m 0644 "$repo_root/config/emulator/scenarios.json" \
  "$app/Contents/Resources/scenarios.json"
mkdir -p "$app/Contents/Resources/provision"
cp -R "$repo_root/config/emulator/provision/." "$app/Contents/Resources/provision/"

printf '=== Signing with the virtualization entitlement\n'
# Ad-hoc signing is enough for a local internal build. A public release replaces
# `-` with a Developer ID identity and adds notarization; nothing else changes.
identity=${LUMA_EMULATOR_SIGNING_IDENTITY:--}
codesign --force --sign "$identity" \
  --entitlements "$package_root/Resources/host.entitlements" \
  --options runtime \
  "$app/Contents/MacOS/LumaEmulatorHost"
codesign --force --sign "$identity" "$app/Contents/MacOS/luma-emulator"
codesign --force --sign "$identity" \
  --entitlements "$package_root/Resources/host.entitlements" \
  --options runtime \
  "$app"

printf '=== Verifying the entitlement actually landed\n'
if ! codesign -d --entitlements - "$app/Contents/MacOS/LumaEmulatorHost" 2>&1 |
     grep -q 'com.apple.security.virtualization'; then
  printf 'error: the runtime was signed without the virtualization entitlement\n' >&2
  exit 1
fi

ln -sfn "Luma Emulator.app/Contents/MacOS/luma-emulator" "$output_root/luma-emulator"

cat <<SUMMARY

Built:
  application  $app
  command      $output_root/luma-emulator

Put the command on your PATH with:
  ln -sfn "$output_root/luma-emulator" ~/.local/bin/luma-emulator
SUMMARY
