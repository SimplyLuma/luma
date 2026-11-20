#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Lay out org.projectluma.Platform.openh264 for one architecture and runtime
# branch, ready for flatpak build-export --runtime:
#
#   openh264-extension.sh OUTPUT_DIR ARCH BRANCH
#
# An extra-data extension: it carries no codec. Its metadata names Cisco's
# binary (src/luma-openh264/cisco-openh264.conf, the file the OS downloads);
# flatpak downloads it from Cisco onto the device when the extension is
# installed, checks the recorded size and SHA-256, and runs apply_extra in the
# Platform runtime to unpack it to lib64/openh264/extra/libopenh264.so.8.
set -euo pipefail

[ "$#" -eq 3 ] || { printf 'usage: %s OUTPUT_DIR ARCH BRANCH\n' "$0" >&2; exit 2; }
out=$1 arch=$2 branch=$3
conf=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)/src/luma-openh264/cisco-openh264.conf
# shellcheck disable=SC1090
. "$conf"
case $arch in
  x86_64) key=X86_64 ;;
  aarch64) key=AARCH64 ;;
  *) printf 'error: Cisco publishes no OpenH264 binary for %s\n' "$arch" >&2; exit 1 ;;
esac
url_var=OPENH264_${key}_URL size_var=OPENH264_${key}_SIZE
sha_var=OPENH264_${key}_SHA256 library_sha_var=OPENH264_${key}_LIBRARY_SHA256
url=${!url_var} size=${!size_var} sha256=${!sha_var} library_sha256=${!library_sha_var}
name=${url##*/}
[ "$branch" = "$OPENH264_FLATPAK_BRANCH" ] ||
  { printf 'error: cisco-openh264.conf is for runtime branch %s\n' "$OPENH264_FLATPAK_BRANCH" >&2; exit 1; }
[ ! -e "$out" ] || { printf 'error: %s exists\n' "$out" >&2; exit 1; }

install -d -m 0755 "$out/files/bin" "$out/files/share/metainfo"
# Runs in a sandbox with org.projectluma.Platform as /usr, the extension as
# /app and the downloaded file in the working directory /app/extra.
cat >"$out/files/bin/apply_extra" <<SH
#!/bin/sh
set -eu
bunzip2 -c '$name' >$OPENH264_SONAME.partial
echo '$library_sha256  $OPENH264_SONAME.partial' | sha256sum -c --status
rm -f '$name'
chmod 0644 $OPENH264_SONAME.partial
mv -f $OPENH264_SONAME.partial $OPENH264_SONAME
SH
chmod 0755 "$out/files/bin/apply_extra"
cat >"$out/files/share/metainfo/org.projectluma.Platform.openh264.metainfo.xml" <<XML
<?xml version="1.0" encoding="UTF-8"?>
<component type="runtime">
  <id>org.projectluma.Platform.openh264</id>
  <metadata_license>CC0-1.0</metadata_license>
  <project_license>LicenseRef-proprietary=https://www.openh264.org/BINARY_LICENSE.txt</project_license>
  <name>OpenH264</name>
  <summary>OpenH264 Video Codec provided by Cisco Systems, Inc.</summary>
  <description>
    <p>Cisco's OpenH264 $OPENH264_VERSION binary for the Luma Platform, downloaded from Cisco when this extension is installed.</p>
  </description>
  <url type="homepage">https://www.openh264.org/</url>
</component>
XML
cat >"$out/metadata" <<META
[Runtime]
name=org.projectluma.Platform.openh264
runtime=org.projectluma.Platform/$arch/$branch
sdk=org.projectluma.Sdk/$arch/$branch

[ExtensionOf]
ref=runtime/org.projectluma.Platform/$arch/$branch

[Extra Data]
name=$name
checksum=$sha256
size=$size
uri=$url
META
printf '%s\n' "$out"
