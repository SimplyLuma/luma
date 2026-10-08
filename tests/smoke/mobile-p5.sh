#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
assembler="$repo_root/scripts/mobile/prepare-fp6-p5-candidate.sh"
smoke="$repo_root/scripts/mobile/smoke-fp6-p5-candidate.sh"
p5_env="$repo_root/config/mobile/fp6-physical/p5.env"
policy="$repo_root/config/mobile/fp6-physical/board-support-paths.txt"
packages="$repo_root/config/mobile/packages.txt"
overlay="$repo_root/config/mobile/fp6-physical/overlay"
preset="$repo_root/config/mobile/systemd-preset/80-luma-handheld.preset"

bash -n "$assembler" "$smoke"
bash "$repo_root/tests/smoke/native-core-independence.sh"

for package in dbus-daemon greetd plymouth qrtr rmtfs selinux-policy-targeted sudo systemd-pam tqftpserv; do
  grep -qx "$package" "$packages"
done

grep -qx 'FP6_P5_INSTALL_AUTHORIZED=false' "$p5_env"
grep -qx 'FP6_P5_MODULE_ABI=7.1.2' "$p5_env"
grep -qx 'FP6_P5_CONTROL_RAW_SHA256=d68dd378799b34c9b6fe9fbfde9a379b5404250c424b9aa524911174edc1a48a' "$p5_env"
grep -qx 'FP6_P5_BOOT_PARTITION_TYPE=C12A7328-F81F-11D2-BA4B-00A0C93EC93B' "$p5_env"
grep -qx 'FP6_P5_ROOT_PARTITION_TYPE=B921B045-1DF0-41C3-AF44-4C6F280D3FAE' "$p5_env"
[ "$(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' "$policy")" = \
  $'/lib/modules/7.1.2\n/lib/firmware' ]

grep -qx 'INSTALL_AUTHORIZED=false' "$overlay/etc/luma/p5-candidate.env"
grep -qx 'luma ALL=(ALL) NOPASSWD: ALL' "$overlay/etc/sudoers.d/luma-diagnostic"
grep -qx 'interface-name=usb0' \
  "$overlay/etc/NetworkManager/system-connections/luma-usb-rescue.nmconnection"
[ ! -e "$overlay/home/luma/.ssh/authorized_keys" ]
grep -qx 'disable waydroid-container.service' "$preset"
wireplumber_camera="$overlay/etc/systemd/user/wireplumber.service.d/50-luma-fp6-camera.conf"
grep -qx 'Environment=LIBCAMERA_SOFTISP_MODE=cpu' "$wireplumber_camera"
grep -qx 'TimeoutStopFailureMode=kill' "$wireplumber_camera"
grep -qx 'LimitCORE=0' "$wireplumber_camera"
wireplumber_audio="$overlay/etc/wireplumber/wireplumber.conf.d/51-luma-fp6-audio.conf"
grep -qx '        api.alsa.disable-mmap = true' "$wireplumber_audio"
grep -qx '        api.alsa.period-size = 2400' "$wireplumber_audio"
grep -qx '        api.alsa.period-num = 4' "$wireplumber_audio"
audio_route="$overlay/usr/libexec/luma-fp6-audio-route"
[ -x "$audio_route" ]
grep -Fq "luma-fp6-audio-route {speaker|earpiece|mute|status}" "$audio_route"
audio_ucm="$overlay/usr/share/alsa/ucm2/Fairphone/fp6/HiFi.conf"
grep -Fq 'SectionDevice."Speaker"' "$audio_ucm"
grep -Fq 'SectionDevice."Mic"' "$audio_ucm"
grep -Fq 'CapturePCM "hw:${CardId},1"' "$audio_ucm"
osk_completion="$overlay/etc/systemd/user/mobi.phosh.OSK.service.d/50-luma-completion.conf"
grep -qx 'Environment=POS_DEBUG=force-completion' "$osk_completion"
grep -qx 'ExecStart=/usr/local/libexec/luma-phosh-osk-stevia --allow-replacement' \
  "$osk_completion"
camera_guard="$overlay/usr/local/libexec/luma-fp6-camera-gpu-guard"
camera_guard_unit="$overlay/usr/lib/systemd/system/luma-fp6-camera-gpu-guard.service"
[ -x "$camera_guard" ]
grep -Fq 'hangcheck detected gpu lockup' "$camera_guard"
grep -Fq 'Timeout waiting for GMU' "$camera_guard"
grep -Fq "a8xx_recover: cx gdsc didn't collapse" "$camera_guard"
grep -Fq 'ExecStart=/usr/local/libexec/luma-fp6-camera-gpu-guard' "$camera_guard_unit"
grep -Fq 'ProtectSystem=strict' "$camera_guard_unit"
bluetooth_address="$overlay/usr/libexec/luma-fp6-bluetooth-address"
bluetooth_address_unit="$overlay/usr/lib/systemd/system/luma-fp6-bluetooth-address.service"
[ -x "$bluetooth_address" ]
grep -Fq 'The Fairphone (Gen. 6)' "$bluetooth_address"
grep -Fq '/dev/disk/by-partlabel/persist' "$bluetooth_address"
grep -Fq 'trace_info/bt_macaddr' "$bluetooth_address"
grep -Fq 'mount -t ext4 -o ro,noload' "$bluetooth_address"
grep -Fq 'btmgmt --index hci0 public-addr' "$bluetooth_address"
grep -Fq 'ExecStart=/usr/libexec/luma-fp6-bluetooth-address' "$bluetooth_address_unit"
grep -Fq 'PrivateMounts=yes' "$bluetooth_address_unit"
grep -Fq 'CapabilityBoundingSet=CAP_NET_ADMIN CAP_SYS_ADMIN' "$bluetooth_address_unit"
primary_gw="$repo_root/scripts/mobile/luma-fp6-primary-gw-session"
primary_gw_unit="$repo_root/scripts/mobile/luma-fp6-primary-gw-session.service"
ims_guard="$repo_root/scripts/mobile/luma-fp6-ims-state-guard"
ims_unit="$repo_root/scripts/mobile/luma-fp6-imsd.service"
modem_quiesce="$repo_root/scripts/mobile/luma-fp6-modem-quiesce"
modem_quiesce_unit="$repo_root/scripts/mobile/luma-fp6-modem-quiesce.service"
[ -x "$primary_gw" ]
[ -x "$ims_guard" ]
[ -x "$modem_quiesce" ]
grep -Fq 'Primary GW:' "$primary_gw"
grep -Fq 'identifiers_logged=0' "$primary_gw"
grep -Fq 'Before=ModemManager.service' "$primary_gw_unit"
grep -Fq 'ExecStartPre=/usr/libexec/luma-fp6-ims-state-guard' "$ims_unit"
grep -Fq 'protected_contents_logged=0' "$ims_guard"
grep -Fq 'rmtfs_shadow=active' "$modem_quiesce"
grep -Fq 'After=rmtfs.service tqftpserv.service ModemManager.service' \
  "$modem_quiesce_unit"
grep -Fq 'ExecStop=/usr/libexec/luma-fp6-modem-quiesce' "$modem_quiesce_unit"
grep -qx 'enable luma-fp6-primary-gw-session.service' "$preset"
grep -qx 'enable luma-fp6-modem-quiesce.service' "$preset"
grep -Fq 'ALLOWED_MODES=' "$overlay/etc/luma/fp6-cellular-link.conf"
grep -Fq -- '--set-allowed-modes=' \
  "$overlay/usr/libexec/luma-fp6-cellular-link"

grep -q "LUMA_FP6_SSH_PUBLIC_KEY_FILE is required" "$assembler"
! grep -q 'FP6_CONTROL_ROOTFS_IMAGE%.xz' "$assembler"
grep -q "LUMA_FP6_LOGIN_PASSWORD_HASH" "$assembler"
grep -q 'CONTROL_EXECUTABLES_IMPORTED=false' "$assembler"
grep -q 'luma-stevia-aarch64' "$assembler"
grep -q 'luma-phosh-osk-stevia' "$assembler"
grep -q 'LUMA_STEVIA_BUNDLE_VERSION' "$assembler"
grep -q 'setfiles /etc/selinux/targeted/contexts/files/file_contexts / force:true' "$assembler"
grep -q 'part-get-gpt-type /dev/sda 1' "$assembler"
grep -q 'part-get-gpt-type /dev/sda 2' "$assembler"
grep -q 'install -m 0644.*passwd' "$assembler"
grep -q 'install -m 0644.*group' "$assembler"
grep -q 'multi-user.target.wants/luma-fp6-camera-gpu-guard.service' "$assembler"
grep -q 'multi-user.target.wants/luma-fp6-bluetooth-address.service' "$assembler"
grep -q 'multi-user.target.wants/luma-fp6-primary-gw-session.service' "$assembler"
grep -q 'multi-user.target.wants/luma-fp6-modem-quiesce.service' "$assembler"
grep -q 'multi-user.target.wants/luma-fp6-imsd.service' "$assembler"
grep -q 'chmod 0644.*passwd.*group' "$repo_root/scripts/mobile/compose-fp6-rootfs.sh"
grep -q 'chmod 0600.*shadow.*gshadow' "$repo_root/scripts/mobile/compose-fp6-rootfs.sh"
grep -q 'plymouth-quit.service' "$smoke"
grep -q 'multi-user.target.wants/plymouth-quit-wait.service' "$smoke"
grep -q 'Bluetooth factory-address unit enabled' "$smoke"
grep -q 'PHONE_ACCESSED=false' "$assembler"
grep -q 'P5_INSTALL_AUTHORIZED=false' "$assembler"
! grep -Eq '^[[:space:]]*(adb|fastboot)([[:space:]]|$)' "$assembler"

printf 'Fedora FP6 P5 source contract smoke test: PASS\n'
