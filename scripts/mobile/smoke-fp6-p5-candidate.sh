#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Independent, read-only acceptance checks for an already composed P5 image.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-physical/p5.env"

output_dir=${LUMA_FP6_P5_OUTPUT:-$repo_root/build/mobile/fp6-physical/p5-candidate}
manifest="$output_dir/manifest.env"
bridge_manifest="$output_dir/board-support-sha256.txt"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

for command in guestfish mktemp sha256sum simg2img; do
  command -v "$command" >/dev/null || die "missing required command: $command"
done
[ -f "$manifest" ] || die "missing P5 manifest: $manifest"
[ -f "$bridge_manifest" ] || die "missing board-support manifest: $bridge_manifest"

# shellcheck disable=SC1090
. "$manifest"
raw="$output_dir/$RAW_FILENAME"
sparse="$output_dir/$SPARSE_FILENAME"
[ -f "$raw" ] || die "missing P5 raw image: $raw"
[ -f "$sparse" ] || die "missing P5 sparse image: $sparse"

temporary=$(mktemp -d "$output_dir/.smoke.XXXXXX")
cleanup() {
  rm -rf -- "$temporary"
}
trap cleanup EXIT
roundtrip="$temporary/roundtrip.raw"

failures=0
check() {
  description=$1
  shift
  if "$@"; then
    printf 'PASS  %s\n' "$description"
  else
    printf 'FAIL  %s\n' "$description"
    failures=$((failures + 1))
  fi
}

check 'manifest remains unauthorized' test "$P5_INSTALL_AUTHORIZED" = false
check 'composer never accessed phone' test "$PHONE_ACCESSED:$FASTBOOT_COMMANDS_RUN" = false:false
check 'boot and dtbo writes are unnecessary' test \
  "$BOOT_WRITE_REQUIRED:$DTBO_WRITE_REQUIRED" = false:false
check 'target and architecture' test \
  "$PHYSICAL_TARGET:$FEDORA_RELEASE:$ARCHITECTURE" = fairphone-fp6:44:aarch64
check 'proven external boot hash retained' test \
  "$PROVEN_BOOT_SHA256" = "$FP6_P5_PROVEN_BOOT_SHA256"
check 'partition type contract retained' test \
  "$BOOT_PARTITION_TYPE:$ROOT_PARTITION_TYPE" = \
  "$FP6_P5_BOOT_PARTITION_TYPE:$FP6_P5_ROOT_PARTITION_TYPE"
check 'raw image byte length' test "$RAW_BYTES" -eq "$(stat -c '%s' "$raw")"
check 'raw image SHA-256' test "$RAW_SHA256" = "$(sha256sum "$raw" | awk '{print $1}')"
check 'sparse image byte length' test "$SPARSE_BYTES" -eq "$(stat -c '%s' "$sparse")"
check 'sparse image SHA-256' test "$SPARSE_SHA256" = "$(sha256sum "$sparse" | awk '{print $1}')"
check 'sparse image expands to the accepted raw bytes' bash -c \
  'simg2img "$1" "$2" && test "$3" = "$(sha256sum "$2" | awk '\''{print $1}'\'')"' _ \
  "$sparse" "$roundtrip" "$RAW_SHA256"
check 'board-support manifest SHA-256' test "$BOARD_SUPPORT_MANIFEST_SHA256" = \
  "$(sha256sum "$bridge_manifest" | awk '{print $1}')"
check 'embedded VFAT boot partition unchanged' test \
  "$EMBEDDED_BOOT_PARTITION_SHA256" = \
  "$(guestfish --ro --blocksize=4096 -a "$raw" run : checksum-device sha256 /dev/sda1)"
check 'root filesystem UUID retained' test "$ROOT_FILESYSTEM_UUID" = \
  "$(guestfish --ro --blocksize=4096 -a "$raw" run : vfs-uuid /dev/sda2)"
check 'boot partition GPT type retained' test "$BOOT_PARTITION_TYPE" = \
  "$(guestfish --ro --blocksize=4096 -a "$raw" run : part-get-gpt-type /dev/sda 1)"
check 'root partition GPT type retained' test "$ROOT_PARTITION_TYPE" = \
  "$(guestfish --ro --blocksize=4096 -a "$raw" run : part-get-gpt-type /dev/sda 2)"
check 'Fedora root marker' test ID=fedora = \
  "$(guestfish --ro --blocksize=4096 -a "$raw" run : mount-ro /dev/sda2 / : grep '^ID=' /etc/os-release)"
check 'negative in-image authorization marker' test INSTALL_AUTHORIZED=false = \
  "$(guestfish --ro --blocksize=4096 -a "$raw" run : mount-ro /dev/sda2 / : grep '^INSTALL_AUTHORIZED=' /etc/luma/p5-candidate.env)"
check 'Milos module ABI present' bash -c \
  'guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : is-dir "/usr/lib/modules/$2" | grep -qx true' _ \
  "$raw" "$FP6_P5_MODULE_ABI"
check 'SSH public key present' bash -c \
  'guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : is-file /home/luma/.ssh/authorized_keys | grep -qx true' _ \
  "$raw"
check 'SSH rescue key owned by diagnostic account' bash -c \
  'value=$(guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : stat /home/luma/.ssh/authorized_keys); test "$(printf "%s\n" "$value" | awk '\''$1 == "uid:" { print $2 }'\''):$(printf "%s\n" "$value" | awk '\''$1 == "gid:" { print $2 }'\'')" = 1000:1000' _ \
  "$raw"
check 'SSH password authentication disabled' test 'AuthenticationMethods publickey' = \
  "$(guestfish --ro --blocksize=4096 -a "$raw" run : mount-ro /dev/sda2 / : grep '^AuthenticationMethods ' /etc/ssh/sshd_config.d/40-luma-diagnostic.conf)"
check 'SSH rescue key has Fedora SELinux type' bash -c \
  'guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : getxattr /home/luma/.ssh/authorized_keys security.selinux | tr -d "\000" | grep -aq ":ssh_home_t:s0$"' _ \
  "$raw"
check 'shadow file has Fedora SELinux type' bash -c \
  'guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : getxattr /etc/shadow security.selinux | tr -d "\000" | grep -aq ":shadow_t:s0$"' _ \
  "$raw"
check 'passwd database is service-readable' bash -c \
  'mode=$(guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : stat /etc/passwd | awk '\''$1 == "mode:" { print $2 }'\''); test $((mode & 511)) -eq 420' _ \
  "$raw"
check 'group database is service-readable' bash -c \
  'mode=$(guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : stat /etc/group | awk '\''$1 == "mode:" { print $2 }'\''); test $((mode & 511)) -eq 420' _ \
  "$raw"
check 'systemd PAM module present' bash -c \
  'guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : is-file /usr/lib64/security/pam_systemd.so | grep -qx true' _ \
  "$raw"
check 'Plymouth control client present' bash -c \
  'guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : is-file /usr/bin/plymouth | grep -qx true' _ \
  "$raw"
check 'Luma handheld Plymouth theme selected' test 'Theme=luma-loading-handheld' = \
  "$(guestfish --ro --blocksize=4096 -a "$raw" run : mount-ro /dev/sda2 / : grep '^Theme=' /etc/plymouth/plymouthd.conf)"
check 'Luma handheld Plymouth descriptor present' bash -c \
  'guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : is-file /usr/share/plymouth/themes/luma-loading-handheld/luma-loading-handheld.plymouth | grep -qx true' _ \
  "$raw"
check 'Luma handheld Plymouth script present' bash -c \
  'guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : is-file /usr/share/plymouth/themes/luma-loading-handheld/luma-loading-handheld.script | grep -qx true' _ \
  "$raw"
check 'Luma handheld boot wordmark present' bash -c \
  'guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : is-file /usr/share/plymouth/themes/luma-loading-handheld/luma-wordmark.png | grep -qx true' _ \
  "$raw"
check 'Luma canonical wordmark source present' bash -c \
  'guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : is-file /usr/share/luma/boot/brand/luma-wordmark.svg | grep -qx true' _ \
  "$raw"
check 'Plymouth quit unit present' bash -c \
  'guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : is-file /usr/lib/systemd/system/plymouth-quit.service | grep -qx true' _ \
  "$raw"
check 'Plymouth quit wired into multi-user startup' bash -c \
  'guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : is-symlink /usr/lib/systemd/system/multi-user.target.wants/plymouth-quit.service | grep -qx true' _ \
  "$raw"
check 'Plymouth quit wait wired into multi-user startup' bash -c \
  'guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : is-symlink /usr/lib/systemd/system/multi-user.target.wants/plymouth-quit-wait.service | grep -qx true' _ \
  "$raw"
check 'greetd display manager enabled' test /usr/lib/systemd/system/greetd.service = \
  "$(guestfish --ro --blocksize=4096 -a "$raw" run : mount-ro /dev/sda2 / : readlink /etc/systemd/system/display-manager.service)"
check 'FP6 Bluetooth factory-address helper present' bash -c \
  'guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : is-file /usr/libexec/luma-fp6-bluetooth-address | grep -qx true' _ \
  "$raw"
check 'FP6 Bluetooth factory-address unit present' bash -c \
  'guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : is-file /usr/lib/systemd/system/luma-fp6-bluetooth-address.service | grep -qx true' _ \
  "$raw"
check 'FP6 Bluetooth factory-address unit enabled' bash -c \
  'guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : is-symlink /usr/lib/systemd/system/multi-user.target.wants/luma-fp6-bluetooth-address.service | grep -qx true' _ \
  "$raw"
for service in rmtfs sshd tqftpserv; do
  check "$service service enabled" bash -c \
    'guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : is-symlink "/etc/systemd/system/multi-user.target.wants/$2.service" | grep -qx true' _ \
    "$raw" "$service"
done
for firmware in \
  /usr/lib/firmware/postmarketos/gen80300_sqe.fw \
  /usr/lib/firmware/postmarketos/gen80300_gmu.bin \
  /usr/lib/firmware/qcom/milos/fairphone/fp6/gen80300_zap.mbn; do
  check "required firmware $firmware" bash -c \
    'guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : is-file "$2" | grep -qx true' _ \
    "$raw" "$firmware"
done
check 'diagnostic login is not locked or empty' bash -c \
  'value=$(guestfish --ro --blocksize=4096 -a "$1" run : mount-ro /dev/sda2 / : grep "^luma:" /etc/shadow); field=${value#*:}; field=${field%%:*}; [ -n "$field" ] && case "$field" in \!*|\*) false ;; *) true ;; esac' _ \
  "$raw"

statvfs=$(guestfish --ro --blocksize=4096 -a "$raw" \
  run : mount-ro /dev/sda2 / : statvfs /)
available_blocks=$(printf '%s\n' "$statvfs" | awk '$1 == "bavail:" { print $2 }')
fragment_size=$(printf '%s\n' "$statvfs" | awk '$1 == "frsize:" { print $2 }')
check 'at least 512 MiB root space remains' test \
  "$((available_blocks * fragment_size))" -ge 536870912

if [ "$failures" -ne 0 ]; then
  printf '\nFedora FP6 P5 candidate smoke test: FAIL (%s checks)\n' "$failures"
  exit 1
fi

printf '\nFedora FP6 P5 candidate smoke test: PASS\n'
printf 'Evidence only; this result does not authorize installation.\n'
