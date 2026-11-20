#!/usr/bin/bash
# SPDX-License-Identifier: Apache-2.0
# luma-openh264 against a local stand-in for Cisco's file: download, checks,
# disable/enable, a tampered download and an unreachable server.
set -euo pipefail
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
tool=${1:-$here/../luma-openh264}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
head -c 65536 /dev/urandom >"$tmp/libopenh264.so.8"
bzip2 -k -c "$tmp/libopenh264.so.8" >"$tmp/cisco.so.bz2"
write_conf() {  # write_conf URL ARCHIVE_SHA256
  cat >"$tmp/conf" <<CONF
OPENH264_VERSION=2.6.0
OPENH264_SONAME=libopenh264.so.8
OPENH264_FLATPAK_BRANCH=44
OPENH264_X86_64_URL=$1
OPENH264_X86_64_SIZE=$(stat -c %s "$tmp/cisco.so.bz2")
OPENH264_X86_64_SHA256=$2
OPENH264_X86_64_LIBRARY_SHA256=$(sha256sum <"$tmp/libopenh264.so.8" | cut -d' ' -f1)
CONF
}
export LUMA_OPENH264_CONF=$tmp/conf LUMA_OPENH264_STATE=$tmp/state LUMA_OPENH264_DISABLED=$tmp/etc/disabled
export LUMA_OPENH264_PROTOCOLS==file LUMA_OPENH264_ARCH=x86_64 LUMA_OPENH264_FLATPAK=
good=$(sha256sum <"$tmp/cisco.so.bz2" | cut -d' ' -f1)
fail() { echo "FAIL: $*" >&2; exit 1; }
same() { [ "$(sha256sum <"$1")" = "$(sha256sum <"$2")" ]; }

write_conf "file://$tmp/cisco.so.bz2" "$good"
"$tool" sync
same "$tmp/libopenh264.so.8" "$tmp/state/libopenh264.so.8" || fail 'sync did not install the library'
grep -qx 'version=2.6.0' "$tmp/state/source" || fail 'no source record'
report=$("$tool" status)
grep -qx 'OpenH264 Video Codec provided by Cisco Systems, Inc.' <<<"$report" || fail 'no attribution'
grep -q '^System: *OpenH264 2.6.0 downloaded from Cisco' <<<"$report" || fail 'status'
before=$(stat -c %Y "$tmp/state/libopenh264.so.8"); sleep 1
"$tool" sync
[ "$(stat -c %Y "$tmp/state/libopenh264.so.8")" = "$before" ] || fail 'a current library was downloaded again'

"$tool" disable
[ -e "$tmp/etc/disabled" ] || fail 'disable left no setting'
[ ! -e "$tmp/state/libopenh264.so.8" ] || fail 'disable left the library'
"$tool" sync
[ ! -e "$tmp/state/libopenh264.so.8" ] || fail 'sync downloaded while disabled'
report=$("$tool" status)
grep -q '^Setting: *disabled' <<<"$report" || fail 'status while disabled'
"$tool" enable
[ ! -e "$tmp/etc/disabled" ] || fail 'enable kept the setting'
same "$tmp/libopenh264.so.8" "$tmp/state/libopenh264.so.8" || fail 'enable did not download'

rm "$tmp/state/libopenh264.so.8"
write_conf "file://$tmp/cisco.so.bz2" "$(printf '0%.0s' $(seq 64))"
if "$tool" sync 2>"$tmp/err"; then fail 'a download with the wrong SHA-256 was accepted'; fi
grep -q 'does not match the recorded SHA-256' "$tmp/err" || fail 'mismatch message'
[ ! -e "$tmp/state/libopenh264.so.8" ] || fail 'a mismatched download was installed'
[ -z "$(find "$tmp/state" -name '.download.*')" ] || fail 'a failed download left its work directory'

write_conf "file://$tmp/missing.so.bz2" "$good"
status=0; "$tool" sync >/dev/null || status=$?
[ "$status" = 75 ] || fail "an unreachable server returned $status, not 75"
[ ! -e "$tmp/state/libopenh264.so.8" ] || fail 'nothing should be installed'

status=0
LUMA_OPENH264_PROTOCOLS==https "$tool" sync >/dev/null 2>&1 || status=$?
[ "$status" = 75 ] || fail 'a non-HTTPS download was attempted'
echo 'luma-openh264 tests passed'
