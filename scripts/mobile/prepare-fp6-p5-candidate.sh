#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Compose an offline, unauthorized Fedora userdata candidate around the exact
# partition layout, modules, and firmware of the verified FP6 control image.
# This script never invokes adb or fastboot and never opens a USB device.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-control/inputs.env"
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-physical/p5.env"
# shellcheck disable=SC1091
. "$repo_root/config/mobile/stevia-source.env"

build_root=${LUMA_FP6_PHYSICAL_BUILD_DIR:-$repo_root/build/mobile/fp6-physical}
control_dir="$build_root/downloads/postmarketos-$FP6_CONTROL_BUILD"
control_raw=${LUMA_FP6_CONTROL_RAW:-$control_dir/${FP6_CONTROL_ROOTFS_IMAGE%.img.xz}-raw.img}
fedora_dir=${LUMA_FP6_ROOTFS_OUTPUT:-$build_root/fedora-rootfs}
fedora_manifest="$fedora_dir/manifest.env"
fedora_archive="$fedora_dir/luma-fp6-fedora44-rootfs.tar.zst"
output_dir=${LUMA_FP6_P5_OUTPUT:-$build_root/p5-candidate}
output_raw="$output_dir/luma-fp6-fedora44-p5.raw"
output_sparse="$output_dir/luma-fp6-fedora44-p5.img"
output_manifest="$output_dir/manifest.env"
bridge_manifest="$output_dir/board-support-sha256.txt"
public_key_file=${LUMA_FP6_SSH_PUBLIC_KEY_FILE:-}
password_hash=${LUMA_FP6_LOGIN_PASSWORD_HASH:-}
policy="$repo_root/config/mobile/fp6-physical/board-support-paths.txt"
static_overlay="$repo_root/config/mobile/fp6-physical/overlay"
keyboard_dir="$build_root/luma-keyboard-aarch64"
keyboard_manifest="$keyboard_dir/manifest.env"
stevia_dir="$build_root/luma-stevia-aarch64"
stevia_manifest="$stevia_dir/manifest.env"
fingerprint_runtime="$build_root/fp6-fingerprint-runtime-accepted-v1"
fingerprint_manifest="$fingerprint_runtime/manifest.sha256"
carrier_pdc_qmicli="$repo_root/build/mobile/fp6-cellular/pdc-platform-v2/qmicli-pdc-typed"
carrier_pdc_profile="$build_root/fp6-qrel1695-non-hlos-files/image/modem_pr/mcfg/configs/mcfg_sw/generic/NA/ATT/VoLTE/mcfg_sw.mbn"
carrier_pdc_platform="$repo_root/build/mobile/fp6-cellular/pdc-platform-v2/mcfg_hw_dsds_milos.mbn"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "$(uname -s)" = Linux ] || die 'the P5 assembler requires Linux'
# shellcheck disable=SC1091
. /etc/os-release
[ "${ID:-}" = fedora ] && [ "${VERSION_ID:-}" = 44 ] || \
  die 'the P5 assembler requires Fedora 44'

for command in awk dconf guestfish img2simg mktemp sha256sum simg2img tar; do
  command -v "$command" >/dev/null || die "missing required command: $command"
done

[ -f "$control_raw" ] || die "missing expanded raw control disk: $control_raw"
[ -f "$fedora_manifest" ] || die "missing Fedora rootfs manifest: $fedora_manifest"
[ -f "$fedora_archive" ] || die "missing Fedora rootfs archive: $fedora_archive"
[ -d "$static_overlay" ] || die "missing P5 static overlay: $static_overlay"
[ -f "$policy" ] || die "missing board-support policy: $policy"
[ -f "$keyboard_manifest" ] || die "missing Luma keyboard manifest: $keyboard_manifest"
[ -x "$keyboard_dir/luma-latinime-decoder" ] || die 'missing Luma keyboard decoder'
[ -f "$keyboard_dir/main_en.dict" ] || die 'missing Luma keyboard dictionary'
[ -f "$stevia_manifest" ] || die "missing Luma Stevia manifest: $stevia_manifest"
[ -x "$stevia_dir/luma-phosh-osk-stevia" ] || die 'missing Luma Stevia binary'
[ -f "$fingerprint_manifest" ] || die 'missing accepted FP6 fingerprint runtime manifest'
[ -x "$carrier_pdc_qmicli" ] || die 'missing typed FP6 qmicli executable'
[ -f "$carrier_pdc_profile" ] || die 'missing accepted FP6 AT&T PDC profile'
[ -f "$carrier_pdc_platform" ] || die 'missing accepted FP6 DSDS PDC platform'
[ "$(sha256sum "$carrier_pdc_qmicli" | awk '{print $1}')" = \
  1b4adcd3d949e8d0f68065ba0022bbc35fe4462717deaaeae0ec244557f76b9c ] ||
  die 'typed FP6 qmicli executable checksum mismatch'
[ "$(sha256sum "$carrier_pdc_profile" | awk '{print $1}')" = \
  32e7f3d9059e63323f70cf113b1ecc040dc7e9c82238a1d5bde2b21ca2eb7d6e ] ||
  die 'accepted FP6 AT&T PDC profile checksum mismatch'
[ "$(sha256sum "$carrier_pdc_platform" | awk '{print $1}')" = \
  281cf305a5bcd98b709a0446f9733d1859cb423a791b2af17ab9776ffeaf0400 ] ||
  die 'accepted FP6 DSDS PDC platform checksum mismatch'
[ -x "$fingerprint_runtime/luma-fp6-wake-input" ] || die 'missing fingerprint wake helper'
[ -x "$fingerprint_runtime/luma-qcomtee-listener-supplicant" ] || die 'missing fingerprint listener'
[ -f "$fingerprint_runtime/qcomtee-freezer-v144.ko" ] || die 'missing freezer-aware fingerprint transport'
[ -n "$public_key_file" ] || die 'LUMA_FP6_SSH_PUBLIC_KEY_FILE is required'
[ -f "$public_key_file" ] || die "SSH public key file does not exist: $public_key_file"

[ "$(wc -l <"$public_key_file" | tr -d ' ')" -eq 1 ] || \
  die 'SSH public key file must contain exactly one line'
grep -Eq '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh.com|sk-ecdsa-sha2-nistp256@openssh.com) [A-Za-z0-9+/]+={0,3}([[:space:]].*)?$' \
  "$public_key_file" || die 'SSH public key is not a supported single OpenSSH public key'

case "$password_hash" in
  '$y$'*|'$6$'*) ;;
  *) die 'LUMA_FP6_LOGIN_PASSWORD_HASH must be a caller-supplied yescrypt or SHA-512 crypt hash' ;;
esac
case "$password_hash" in
  *:*|*$'\n'*) die 'login password hash contains a forbidden delimiter' ;;
esac

expected_policy='/lib/modules/7.1.2
/lib/firmware'
actual_policy=$(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' "$policy")
[ "$actual_policy" = "$expected_policy" ] || \
  die 'board-support policy contains an unreviewed control-rootfs path'
[ "$FP6_P5_MODULE_ABI" = 7.1.2 ] || die 'unexpected Milos module ABI'
[ "$FP6_P5_INSTALL_AUTHORIZED" = false ] || die 'P5 source configuration claims installation authorization'

# shellcheck disable=SC1090
. "$keyboard_manifest"
[ "${LUMA_KEYBOARD_BUNDLE_VERSION:-}" = 1 ] || die 'Luma keyboard manifest version mismatch'
[ "${ARCHITECTURE:-}" = aarch64 ] || die 'Luma keyboard architecture mismatch'
[ "$(sha256sum "$keyboard_dir/luma-latinime-decoder" | awk '{print $1}')" = \
  "${DECODER_SHA256:-}" ] || die 'Luma keyboard decoder checksum mismatch'
[ "$(sha256sum "$keyboard_dir/main_en.dict" | awk '{print $1}')" = \
  "${DICTIONARY_SHA256:-}" ] || die 'Luma keyboard dictionary checksum mismatch'

# shellcheck disable=SC1090
. "$stevia_manifest"
[ "${LUMA_STEVIA_BUNDLE_VERSION:-}" = 1 ] || die 'Luma Stevia manifest version mismatch'
[ "${ARCHITECTURE:-}" = aarch64 ] || die 'Luma Stevia architecture mismatch'
[ "${STEVIA_UPSTREAM_COMMIT:-}" = cd8cc1d33a701573af106c53ed4dc493ef68b88c ] || \
  die 'Luma Stevia source commit mismatch'
[ "$(sha256sum "$stevia_dir/luma-phosh-osk-stevia" | awk '{print $1}')" = \
  "${BINARY_SHA256:-}" ] || die 'Luma Stevia binary checksum mismatch'
(
  cd "$fingerprint_runtime"
  sha256sum -c manifest.sha256
) >/dev/null || die 'accepted FP6 fingerprint runtime checksum mismatch'

control_sha256=$(sha256sum "$control_raw" | awk '{print $1}')
[ "$control_sha256" = "$FP6_P5_CONTROL_RAW_SHA256" ] || \
  die 'decompressed control disk SHA-256 mismatch'

# shellcheck disable=SC1090
. "$fedora_manifest"
[ "${FEDORA_RELEASE:-}" = 44 ] || die 'Fedora rootfs manifest release mismatch'
[ "${ARCHITECTURE:-}" = aarch64 ] || die 'Fedora rootfs manifest architecture mismatch'
[ "${UI_INCLUDED:-}" = 1 ] || die 'Fedora rootfs does not contain the reviewed UI layer'
[ "${KERNEL_INCLUDED:-}" = false ] || die 'Fedora rootfs unexpectedly contains a kernel'
[ "${DEVICE_TREE_INCLUDED:-}" = false ] || die 'Fedora rootfs unexpectedly contains a device tree'
[ "${FIRMWARE_INCLUDED:-}" = false ] || die 'Fedora rootfs unexpectedly contains firmware'
[ "${INSTALL_AUTHORIZED:-}" = false ] || die 'Fedora rootfs claims installation authorization'

fedora_sha256=$(sha256sum "$fedora_archive" | awk '{print $1}')
[ "$fedora_sha256" = "${ARTIFACT_SHA256:-}" ] || die 'Fedora rootfs archive SHA-256 mismatch'
current_contract_sha256=$(
  sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' \
    "$repo_root/config/mobile/packages.txt" \
    "$repo_root/config/mobile/ui-packages.txt" \
    "$repo_root/config/shared/application-packages.txt" \
    "$repo_root/config/mobile/phone-capability-packages.txt" \
    | sha256sum | awk '{print $1}'
)
[ "${PACKAGE_CONTRACT_SHA256:-}" = "$current_contract_sha256" ] || \
  die 'Fedora rootfs artifact is stale; recompose it from the current package manifests'

partitions=$(guestfish --ro --blocksize=4096 -a "$control_raw" run : list-partitions)
[ "$partitions" = $'/dev/sda1\n/dev/sda2' ] || die 'control disk partition layout is not the reviewed two-partition GPT'
[ "$(guestfish --ro --blocksize=4096 -a "$control_raw" run : vfs-type /dev/sda1)" = vfat ] || \
  die 'control partition 1 is not VFAT'
[ "$(guestfish --ro --blocksize=4096 -a "$control_raw" run : vfs-type /dev/sda2)" = ext4 ] || \
  die 'control partition 2 is not ext4'
[ "$(guestfish --ro --blocksize=4096 -a "$control_raw" run : part-get-parttype /dev/sda)" = gpt ] || \
  die 'control disk does not use GPT'
[ "$(guestfish --ro --blocksize=4096 -a "$control_raw" run : part-get-gpt-type /dev/sda 1)" = \
  "$FP6_P5_BOOT_PARTITION_TYPE" ] || die 'control boot partition type changed'
[ "$(guestfish --ro --blocksize=4096 -a "$control_raw" run : part-get-gpt-type /dev/sda 2)" = \
  "$FP6_P5_ROOT_PARTITION_TYPE" ] || die 'control root partition type changed'

root_uuid=$(guestfish --ro --blocksize=4096 -a "$control_raw" run : vfs-uuid /dev/sda2)
root_label=$(guestfish --ro --blocksize=4096 -a "$control_raw" run : vfs-label /dev/sda2)
boot_before=$(guestfish --ro --blocksize=4096 -a "$control_raw" run : checksum-device sha256 /dev/sda1)
[ -n "$root_uuid" ] || die 'control root filesystem has no UUID'
[ -n "$root_label" ] || root_label=pmOS_root

[ ! -e "$output_raw" ] || die "refusing to replace existing candidate: $output_raw"
[ ! -e "$output_sparse" ] || die "refusing to replace existing candidate: $output_sparse"
[ ! -e "$output_manifest" ] || die "refusing to replace existing manifest: $output_manifest"
[ ! -e "$bridge_manifest" ] || die "refusing to replace existing bridge manifest: $bridge_manifest"
install -d -m 0700 "$output_dir"

temporary=$(mktemp -d "$output_dir/.p5.XXXXXX")
cleanup() {
  rm -rf -- "$temporary"
}
trap cleanup EXIT

control_export="$temporary/control"
overlay="$temporary/overlay"
account_root="$temporary/account-root"
roundtrip="$temporary/roundtrip.raw"
temporary_bridge_manifest="$temporary/board-support-sha256.txt"
install -d -m 0700 "$control_export" "$overlay" "$account_root"

guestfish --ro --blocksize=4096 -a "$control_raw" \
  run : mount-ro /dev/sda2 / : copy-out /lib/modules /lib/firmware "$control_export"
[ -d "$control_export/modules/$FP6_P5_MODULE_ABI" ] || \
  die "control image lacks module ABI $FP6_P5_MODULE_ABI"
for firmware in \
  "$FP6_P5_REQUIRED_FIRMWARE_1" \
  "$FP6_P5_REQUIRED_FIRMWARE_2" \
  "$FP6_P5_REQUIRED_FIRMWARE_3"; do
  [ -f "$control_export/firmware/$firmware" ] || \
    [ -f "$control_export/firmware/postmarketos/$firmware" ] || \
    die "control image lacks required firmware: $firmware"
done

cp -a "$static_overlay/." "$overlay/"
install -d -m 0755 "$overlay/usr/lib/modules" "$overlay/usr/lib/firmware"
cp -a "$control_export/modules/." "$overlay/usr/lib/modules/"
cp -a "$control_export/firmware/." "$overlay/usr/lib/firmware/"
install -D -m 0755 "$repo_root/scripts/mobile/luma-fp6-primary-gw-session" \
  "$overlay/usr/libexec/luma-fp6-primary-gw-session"
install -D -m 0644 "$repo_root/scripts/mobile/luma-fp6-primary-gw-session.service" \
  "$overlay/usr/lib/systemd/system/luma-fp6-primary-gw-session.service"
install -D -m 0755 "$repo_root/scripts/mobile/luma-fp6-carrier-pdc" \
  "$overlay/usr/libexec/luma-fp6-carrier-pdc"
install -D -m 0755 "$carrier_pdc_qmicli" \
  "$overlay/usr/libexec/luma-fp6-qmicli-pdc"
install -D -m 0644 "$repo_root/scripts/mobile/luma-fp6-carrier-pdc.service" \
  "$overlay/usr/lib/systemd/system/luma-fp6-carrier-pdc.service"
install -D -m 0644 "$carrier_pdc_profile" \
  "$overlay/usr/lib/firmware/luma/fp6-carrier-pdc/att-volte-qrel1695.mbn"
install -D -m 0644 "$carrier_pdc_platform" \
  "$overlay/usr/lib/firmware/luma/fp6-carrier-pdc/dsds-la-milos.mbn"
install -D -m 0755 "$repo_root/scripts/mobile/luma-fp6-modem-quiesce" \
  "$overlay/usr/libexec/luma-fp6-modem-quiesce"
install -D -m 0644 "$repo_root/scripts/mobile/luma-fp6-modem-quiesce.service" \
  "$overlay/usr/lib/systemd/system/luma-fp6-modem-quiesce.service"
install -D -m 0755 "$repo_root/scripts/mobile/luma-fp6-ims-pdn-up.sh" \
  "$overlay/usr/libexec/luma-fp6-ims-pdn-up"
install -D -m 0755 "$repo_root/scripts/mobile/luma-fp6-ims-state-guard" \
  "$overlay/usr/libexec/luma-fp6-ims-state-guard"
install -D -m 0644 "$repo_root/scripts/mobile/luma-fp6-imsd.service" \
  "$overlay/usr/lib/systemd/system/luma-fp6-imsd.service"
install -D -m 0755 "$repo_root/scripts/mobile/luma-fp6-cellular-resume" \
  "$overlay/usr/libexec/luma-fp6-cellular-resume"
install -D -m 0644 "$repo_root/scripts/mobile/luma-fp6-cellular-resume.service" \
  "$overlay/usr/lib/systemd/system/luma-fp6-cellular-resume.service"
install -D -m 0755 "$repo_root/scripts/mobile/luma-fp6-cellular-system-sleep" \
  "$overlay/usr/lib/systemd/system-sleep/luma-fp6-cellular"
install -d -m 0755 "$overlay/usr/lib/systemd/system/multi-user.target.wants"
ln -sfn ../luma-fp6-camera-gpu-guard.service \
  "$overlay/usr/lib/systemd/system/multi-user.target.wants/luma-fp6-camera-gpu-guard.service"
ln -sfn ../luma-fp6-bluetooth-address.service \
  "$overlay/usr/lib/systemd/system/multi-user.target.wants/luma-fp6-bluetooth-address.service"
ln -sfn ../luma-fp6-primary-gw-session.service \
  "$overlay/usr/lib/systemd/system/multi-user.target.wants/luma-fp6-primary-gw-session.service"
ln -sfn ../luma-fp6-carrier-pdc.service \
  "$overlay/usr/lib/systemd/system/multi-user.target.wants/luma-fp6-carrier-pdc.service"
ln -sfn ../luma-fp6-modem-quiesce.service \
  "$overlay/usr/lib/systemd/system/multi-user.target.wants/luma-fp6-modem-quiesce.service"
ln -sfn ../luma-fp6-imsd.service \
  "$overlay/usr/lib/systemd/system/multi-user.target.wants/luma-fp6-imsd.service"

# Keep the diagnostic Luma GNOME session available beside the selected Phosh
# handheld renderer. These files support the real desktop/docked renderer and
# rollback-safe comparison; greetd's product default remains Phosh.
install -D -m 0755 "$repo_root/scripts/mobile/luma-shell-session" \
  "$overlay/usr/local/bin/luma-shell-session"
install -D -m 0755 "$repo_root/scripts/mobile/luma-phosh-session" \
  "$overlay/usr/local/bin/luma-phosh-session"
install -D -m 0755 "$repo_root/scripts/mobile/release-stale-plymouth-drm.sh" \
  "$overlay/usr/local/libexec/luma-release-stale-plymouth-drm"
install -D -m 0644 \
  "$repo_root/config/mobile/fp6-physical/overlay/usr/lib/systemd/user/luma-shell-session.target" \
  "$overlay/usr/lib/systemd/user/luma-shell-session.target"
install -D -m 0644 "$repo_root/config/mobile/environment.d/90-luma-handheld.conf" \
  "$overlay/etc/environment.d/90-luma-handheld.conf"
install -D -m 0644 "$repo_root/config/mobile/systemd-user/luma-graphical-session.target" \
  "$overlay/usr/lib/systemd/user/luma-graphical-session.target"
install -D -m 0644 "$repo_root/config/mobile/systemd-preset/80-luma-handheld.preset" \
  "$overlay/usr/lib/systemd/system-preset/80-luma-handheld.preset"
install -D -m 0644 "$repo_root/config/mobile/luma-shell-handheld.dconf" \
  "$overlay/usr/share/luma/mobile/luma-shell-handheld.dconf"
install -D -m 0644 "$repo_root/config/desktop/dconf/profile/user" \
  "$overlay/etc/dconf/profile/user"
install -D -m 0644 "$repo_root/config/desktop/dconf/db/luma.d/00-luma-desktop" \
  "$overlay/etc/dconf/db/luma.d/00-luma-desktop"
install -D -m 0644 "$repo_root/config/mobile/luma-shell-handheld.dconf" \
  "$overlay/etc/dconf/db/luma.d/90-luma-handheld"
install -D -m 0755 "$keyboard_dir/luma-latinime-decoder" \
  "$overlay/usr/libexec/luma-latinime-decoder"
install -D -m 0644 "$keyboard_dir/main_en.dict" \
  "$overlay/usr/share/luma-keyboard/main_en.dict"
install -D -m 0755 "$repo_root/src/luma-keyboard/luma_ibus_engine.py" \
  "$overlay/usr/libexec/luma-ibus-latinime"
install -D -m 0644 "$repo_root/src/luma-keyboard/luma-latinime.xml" \
  "$overlay/usr/share/ibus/component/luma-latinime.xml"
install -D -m 0755 "$stevia_dir/luma-phosh-osk-stevia" \
  "$overlay/usr/local/libexec/luma-phosh-osk-stevia"
install -D -m 0755 "$repo_root/extensions/luma-handheld/luma-power-key-broker.py" \
  "$overlay/usr/libexec/luma-power-key-broker"
install -D -m 0644 "$repo_root/extensions/luma-handheld/luma-power-key-broker.service" \
  "$overlay/usr/lib/systemd/user/luma-power-key-broker.service"

# Install the exact physically accepted fingerprint adapter and the
# reproducible freezer-aware transport. The units are intentionally shipped
# disabled until suspend/thaw and cold-boot physical acceptance close.
fingerprint_target="$overlay/usr/lib/luma/fp6-fingerprint"
install -d -m 0755 "$fingerprint_target/adapter" "$fingerprint_target/firmware"
install -m 0644 "$fingerprint_runtime/qcomtee-freezer-v144.ko" \
  "$fingerprint_target/qcomtee-freezer-v144.ko"
install -m 0644 "$fingerprint_runtime/focaltech_fp.ko" \
  "$fingerprint_target/focaltech_fp.ko"
install -m 0755 "$fingerprint_runtime/luma-qcomtee-listener-supplicant" \
  "$fingerprint_target/luma-qcomtee-listener-supplicant"
install -m 0755 "$fingerprint_runtime/fp6-focal-driver-stage" \
  "$fingerprint_target/fp6-focal-driver-stage"
install -m 0644 "$fingerprint_runtime/libluma-servicemanager-access.so" \
  "$fingerprint_target/libluma-servicemanager-access.so"
for adapter in "$fingerprint_runtime/adapter/"*.so; do
  install -m 0644 "$adapter" "$fingerprint_target/adapter/"
done
for firmware in "$fingerprint_runtime/firmware/"*; do
  install -m 0644 "$firmware" "$fingerprint_target/firmware/"
done
install -m 0644 "$fingerprint_manifest" "$fingerprint_target/manifest.sha256"
install -D -m 0755 "$fingerprint_runtime/luma-fp6-wake-input" \
  "$overlay/usr/libexec/luma-fp6-wake-input"
install -D -m 0755 "$repo_root/scripts/mobile/luma-fp6-fingerprint-backend" \
  "$overlay/usr/libexec/luma-fp6-fingerprint-backend"
install -D -m 0755 "$repo_root/scripts/mobile/luma-fp6-fingerprint-backend-stop" \
  "$overlay/usr/libexec/luma-fp6-fingerprint-backend-stop"
install -D -m 0755 "$repo_root/scripts/mobile/luma-fp6-wait-graphical-session" \
  "$overlay/usr/libexec/luma-fp6-wait-graphical-session"
install -D -m 0755 "$repo_root/scripts/mobile/luma-fp6-fingerprint-lock-agent.sh" \
  "$overlay/usr/libexec/luma-fp6-fingerprint-lock-agent"
install -D -m 0755 "$repo_root/scripts/mobile/restore-fp6-stock-biometric-mounts-remote.sh" \
  "$overlay/usr/libexec/luma-fp6-fingerprint-restore-mounts"
install -D -m 0755 "$repo_root/scripts/mobile/run-fp6-stock-biometric-service-smoke-v1-remote.sh" \
  "$overlay/usr/libexec/luma-fp6-fingerprint-auth"
install -d -m 0755 "$overlay/usr/lib/systemd/user/luma-shell-session.target.wants"
ln -sfn ../luma-power-key-broker.service \
  "$overlay/usr/lib/systemd/user/luma-shell-session.target.wants/luma-power-key-broker.service"
dconf compile "$overlay/etc/dconf/db/luma" "$overlay/etc/dconf/db/luma.d"

install -d -m 0700 "$overlay/home/luma/.ssh"
install -m 0600 "$public_key_file" \
  "$overlay/home/luma/.ssh/authorized_keys"
chmod 0644 "$overlay/etc/greetd/config.toml" "$overlay/etc/luma/p5-candidate.env"
chmod 0600 "$overlay/etc/NetworkManager/system-connections/luma-usb-rescue.nmconnection"
chmod 0440 "$overlay/etc/sudoers.d/luma-diagnostic"

tar --zstd -xf "$fedora_archive" -C "$account_root" \
  ./etc/passwd ./etc/shadow ./etc/group ./etc/gshadow
chmod u+rw "$account_root/etc/passwd" "$account_root/etc/shadow" \
  "$account_root/etc/group" "$account_root/etc/gshadow"
awk -F: -v OFS=: -v hash="$password_hash" '
  $1 == "luma" { $2 = hash; found = 1 }
  { print }
  END { if (!found) exit 1 }
' "$account_root/etc/shadow" >"$account_root/etc/shadow.new" || \
  die 'Fedora rootfs does not contain the diagnostic account'
mv "$account_root/etc/shadow.new" "$account_root/etc/shadow"
install -d -m 0755 "$overlay/etc"
install -m 0644 "$account_root/etc/passwd" "$overlay/etc/passwd"
install -m 0644 "$account_root/etc/group" "$overlay/etc/group"
install -m 0600 "$account_root/etc/shadow" "$overlay/etc/shadow"
install -m 0600 "$account_root/etc/gshadow" "$overlay/etc/gshadow"

(
  cd "$overlay"
  find usr/lib/modules usr/lib/firmware -type f -print0 \
    | LC_ALL=C sort -z | xargs -0 sha256sum
) >"$temporary_bridge_manifest"
[ -s "$temporary_bridge_manifest" ] || die 'board-support bridge manifest is empty'
bridge_sha256=$(sha256sum "$temporary_bridge_manifest" | awk '{print $1}')

overlay_tar="$temporary/p5-overlay.tar"
tar --acls --xattrs --selinux --numeric-owner --sort=name \
  --owner=0 --group=0 --exclude=./home \
  -C "$overlay" -cf "$overlay_tar" .
tar --acls --xattrs --selinux --numeric-owner --sort=name \
  --owner=1000 --group=1000 \
  -C "$overlay" -rf "$overlay_tar" ./home/luma/.ssh

cp --reflink=auto --sparse=always "$control_raw" "$output_raw"
guestfish --rw --blocksize=4096 -a "$output_raw" \
  run : \
  mkfs ext4 /dev/sda2 blocksize:4096 "label:$root_label" : \
  set-uuid /dev/sda2 "$root_uuid" : \
  mount /dev/sda2 / : \
  tar-in "$fedora_archive" / compress:zstd xattrs:true selinux:true acls:true keepdirlink:true : \
  tar-in "$overlay_tar" / xattrs:true selinux:true acls:true keepdirlink:true : \
  setfiles /etc/selinux/targeted/contexts/files/file_contexts / force:true : \
  sync : umount-all

boot_after=$(guestfish --ro --blocksize=4096 -a "$output_raw" run : checksum-device sha256 /dev/sda1)
[ "$boot_after" = "$boot_before" ] || die 'candidate changed the embedded VFAT boot partition'
[ "$(guestfish --ro --blocksize=4096 -a "$output_raw" run : vfs-uuid /dev/sda2)" = "$root_uuid" ] || \
  die 'candidate changed the proven root filesystem UUID contract'

os_id=$(guestfish --ro --blocksize=4096 -a "$output_raw" \
  run : mount-ro /dev/sda2 / : grep '^ID=' /etc/os-release)
[ "$os_id" = ID=fedora ] || die 'candidate root is not Fedora'
marker=$(guestfish --ro --blocksize=4096 -a "$output_raw" \
  run : mount-ro /dev/sda2 / : grep '^INSTALL_AUTHORIZED=' /etc/luma/p5-candidate.env)
[ "$marker" = INSTALL_AUTHORIZED=false ] || die 'candidate authorization marker is not negative'
guestfish --ro --blocksize=4096 -a "$output_raw" \
  run : mount-ro /dev/sda2 / : is-dir "/usr/lib/modules/$FP6_P5_MODULE_ABI" \
  | grep -qx true || die 'candidate lost the proven Milos modules'
guestfish --ro --blocksize=4096 -a "$output_raw" \
  run : mount-ro /dev/sda2 / : is-file /home/luma/.ssh/authorized_keys \
  | grep -qx true || die 'candidate lost the key-only rescue credential'

img2simg "$output_raw" "$output_sparse"
simg2img "$output_sparse" "$roundtrip"
raw_sha256=$(sha256sum "$output_raw" | awk '{print $1}')
[ "$(sha256sum "$roundtrip" | awk '{print $1}')" = "$raw_sha256" ] || \
  die 'Android sparse round trip changed candidate bytes'
sparse_sha256=$(sha256sum "$output_sparse" | awk '{print $1}')
raw_bytes=$(stat -c '%s' "$output_raw")
sparse_bytes=$(stat -c '%s' "$output_sparse")

cat >"$output_manifest" <<EOF
LUMA_FP6_P5_MANIFEST_VERSION=1
SOURCE_CONTROL_BUILD=$FP6_P5_CONTROL_BUILD
SOURCE_CONTROL_RAW_SHA256=$control_sha256
PROVEN_BOOT_SHA256=$FP6_P5_PROVEN_BOOT_SHA256
FEDORA_ROOTFS_SHA256=$fedora_sha256
PACKAGE_CONTRACT_SHA256=$current_contract_sha256
MODULE_ABI=$FP6_P5_MODULE_ABI
BOOT_PARTITION_TYPE=$FP6_P5_BOOT_PARTITION_TYPE
ROOT_PARTITION_TYPE=$FP6_P5_ROOT_PARTITION_TYPE
ROOT_FILESYSTEM_UUID=$root_uuid
EMBEDDED_BOOT_PARTITION_SHA256=$boot_after
BOARD_SUPPORT_MANIFEST_SHA256=$bridge_sha256
RAW_FILENAME=$(basename "$output_raw")
RAW_BYTES=$raw_bytes
RAW_SHA256=$raw_sha256
SPARSE_FILENAME=$(basename "$output_sparse")
SPARSE_BYTES=$sparse_bytes
SPARSE_SHA256=$sparse_sha256
FEDORA_RELEASE=44
ARCHITECTURE=aarch64
PHYSICAL_TARGET=fairphone-fp6
LUMA_KEYBOARD_DECODER_SHA256=$DECODER_SHA256
LUMA_KEYBOARD_DICTIONARY_SHA256=$DICTIONARY_SHA256
LUMA_KEYBOARD_ANDROID_RUNTIME_REQUIRED=false
LUMA_KEYBOARD_SECOND_COMPOSITOR_REQUIRED=false
CONTROL_EXECUTABLES_IMPORTED=false
SSH_PASSWORD_AUTHENTICATION=false
SSH_PUBLIC_KEY_INJECTED=true
LOGIN_PASSWORD_DEFAULT=false
PHONE_ACCESSED=false
FASTBOOT_COMMANDS_RUN=false
BOOT_WRITE_REQUIRED=false
DTBO_WRITE_REQUIRED=false
P5_INSTALL_AUTHORIZED=false
EOF
install -m 0600 "$temporary_bridge_manifest" "$bridge_manifest"
chmod 0600 "$output_manifest" "$bridge_manifest" "$output_raw" "$output_sparse"

printf 'Offline FP6 Fedora P5 candidate prepared: %s\n' "$output_sparse"
printf 'SHA-256: %s\n' "$sparse_sha256"
printf 'No phone was accessed. Installation remains unauthorized.\n'
