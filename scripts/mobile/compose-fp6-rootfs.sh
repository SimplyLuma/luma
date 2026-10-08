#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
# shellcheck disable=SC1091
. "$repo_root/config/android/weston-fp6-source.env"
# shellcheck disable=SC1091
. "$repo_root/config/mobile/luma-greeter.env"
# shellcheck disable=SC1091
. "$repo_root/config/mobile/greetd-source.env"
output_dir=${1:-$repo_root/build/mobile/fp6-rootfs}
rootfs="$output_dir/rootfs"
include_ui=${LUMA_FP6_INCLUDE_UI:-1}
source_date_epoch=${SOURCE_DATE_EPOCH:-1786406400}
zstd_level=${LUMA_FP6_ZSTD_LEVEL:-10}
zstd_threads=${LUMA_FP6_ZSTD_THREADS:-4}
finalize_only=${LUMA_FP6_FINALIZE_ONLY:-0}
resume_after_dnf=${LUMA_FP6_RESUME_AFTER_DNF:-0}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "$(id -u)" -eq 0 ] || die 'run as root on the isolated Fedora AArch64 builder'
[ "$(uname -m)" = aarch64 ] || die 'the physical userspace must be composed natively on AArch64'

# shellcheck disable=SC1091
. /etc/os-release
[ "${ID:-}" = fedora ] || die 'the composer host must run Fedora'
[ "${VERSION_ID:-}" = 44 ] || die 'the composer host must run Fedora 44'

case "$include_ui" in
  0|1) ;;
  *) die 'LUMA_FP6_INCLUDE_UI must be 0 or 1' ;;
esac
case "$finalize_only" in
  0|1) ;;
  *) die 'LUMA_FP6_FINALIZE_ONLY must be 0 or 1' ;;
esac
case "$resume_after_dnf" in
  0|1) ;;
  *) die 'LUMA_FP6_RESUME_AFTER_DNF must be 0 or 1' ;;
esac
[ "$finalize_only:$resume_after_dnf" != 1:1 ] || \
  die 'finalize-only and post-DNF resume are mutually exclusive'
case "$zstd_level" in
  ''|*[!0-9]*) die 'LUMA_FP6_ZSTD_LEVEL must be an integer from 1 through 19' ;;
esac
[ "$zstd_level" -ge 1 ] && [ "$zstd_level" -le 19 ] || \
  die 'LUMA_FP6_ZSTD_LEVEL must be an integer from 1 through 19'
case "$zstd_threads" in
  ''|*[!0-9]*) die 'LUMA_FP6_ZSTD_THREADS must be an integer from 1 through 8' ;;
esac
[ "$zstd_threads" -ge 1 ] && [ "$zstd_threads" -le 8 ] || \
  die 'LUMA_FP6_ZSTD_THREADS must be an integer from 1 through 8'

for command in dconf dnf groupadd journalctl rpm systemctl systemd-hwdb tar useradd usermod zstd sha256sum; do
  command -v "$command" >/dev/null || die "missing required command: $command"
done

artifact="$output_dir/luma-fp6-fedora44-rootfs.tar.zst"
local_rpm_manifest="$output_dir/local-rpm-manifest.txt"
filer_bundle=${LUMA_FP6_FILER_BUNDLE_DIR:-$repo_root/build/mobile/fp6-physical/luma-apps-aarch64/filer/bundle}
android_runtime_bundle=${LUMA_FP6_ANDROID_RUNTIME_BUNDLE_DIR:-$repo_root/build/mobile/fp6-physical/luma-apps-aarch64/android-runtime/bundle}
weston_bundle=${LUMA_FP6_WESTON_BUNDLE_DIR:-$repo_root/build/mobile/fp6-physical/luma-apps-aarch64/weston-rdp-native-touch/bundle}
local_rpms=(
  "$repo_root/build/packages/luma-messages-e2ee/RPMS/aarch64/$LUMA_MESSAGES_E2EE_AARCH64_NEVRA.rpm"
  "$repo_root/build/packages/luma-apk-metadata/aarch64/$LUMA_APK_METADATA_AARCH64_NEVRA.rpm"
  "$repo_root/build/packages/luma-terminal/RPMS/noarch/$LUMA_TERMINAL_NEVRA.rpm"
  "$repo_root/build/packages/luma-calculator/RPMS/noarch/$LUMA_CALCULATOR_NEVRA.rpm"
  "$repo_root/build/packages/gtk4/RPMS/aarch64/$GTK4_AARCH64_NEVRA.rpm"
  "$repo_root/build/packages/gtk3/RPMS/aarch64/$GTK3_AARCH64_NEVRA.rpm"
  "$repo_root/build/packages/libhandy/RPMS/aarch64/$LIBHANDY_AARCH64_NEVRA.rpm"
  "$repo_root/build/packages/libadwaita/RPMS/aarch64/$LIBADWAITA_AARCH64_NEVRA.rpm"
  "$repo_root/build/packages/gnome-control-center/RPMS/aarch64/gnome-control-center-50.4-${GNOME_CONTROL_CENTER_LUMA_RELEASE}.fc44.aarch64.rpm"
  "$repo_root/build/packages/gnome-control-center/RPMS/noarch/gnome-control-center-filesystem-50.4-${GNOME_CONTROL_CENTER_LUMA_RELEASE}.fc44.noarch.rpm"
  "$repo_root/build/packages/boot-theme/RPMS/noarch/$LUMA_BOOT_THEME_NEVRA.rpm"
  "$repo_root/build/packages/backgrounds/RPMS/noarch/$LUMA_BACKGROUNDS_NEVRA.rpm"
  "$repo_root/build/packages/greetd-session/aarch64/$LUMA_GREETD_SESSION_NEVRA.rpm"
  "$repo_root/build/packages/luma-greeter/aarch64/$LUMA_GREETER_NEVRA.rpm"
  "$repo_root/build/packages/luma-shell-state/RPMS/noarch/$LUMA_SHELL_STATE_NEVRA.rpm"
  "$repo_root/build/packages/luma-search/RPMS/noarch/$LUMA_SEARCH_NEVRA.rpm"
  "$repo_root/build/packages/phoc/aarch64/RPMS/$LUMA_PHOC_AARCH64_NEVRA.rpm"
  "$repo_root/build/packages/luma-phosh/aarch64/RPMS/$LUMA_PHOSH_AARCH64_NEVRA.rpm"
  "$repo_root/build/packages/prairie-icon-theme/RPMS/noarch/$PRAIRIE_ICON_THEME_NEVRA.rpm"
  "$repo_root/build/packages/luma-mods/RPMS/noarch/$LUMA_MODS_NEVRA.rpm"
  "$repo_root/build/packages/figtree-fonts/RPMS/noarch/$FIGTREE_NEVRA.rpm"
  "$repo_root/build/packages/prairie-core-apps/RPMS/noarch/$PRAIRIE_CORE_APPS_NEVRA.rpm"
  "$repo_root/build/packages/luma-developer-platform/aarch64/RPMS/$LUMA_DEVELOPER_PLATFORM_AARCH64_NEVRA.rpm"
  "$repo_root/build/packages/luma-tide/RPMS/noarch/$LUMA_TIDE_NEVRA.rpm"
  "$repo_root/build/packages/luma-darkroom/RPMS/noarch/$LUMA_DARKROOM_NEVRA.rpm"
  "$repo_root/build/packages/luma-imsd/aarch64/RPMS/$LUMA_IMSD_AARCH64_NEVRA.rpm"
  "$android_runtime_bundle/$LUMA_ANDROID_RUNTIME_NEVRA.rpm"
  "$filer_bundle/$NAUTILUS_AARCH64_NEVRA.rpm"
  "$filer_bundle/$NAUTILUS_EXTENSIONS_AARCH64_NEVRA.rpm"
  "$weston_bundle/$LUMA_WESTON_NEVRA.rpm"
  "$weston_bundle/$LUMA_WESTON_LIBS_NEVRA.rpm"
  "$weston_bundle/$LUMA_WESTON_RDP_NEVRA.rpm"
  "$weston_bundle/$LUMA_WESTON_VNC_NEVRA.rpm"
)

for local_rpm in "${local_rpms[@]}"; do
  [ -f "$local_rpm" ] || die "missing shared Luma RPM: $local_rpm"
  rpm -Kv "$local_rpm" >/dev/null || die "invalid shared Luma RPM: $local_rpm"
done

install -d -m 0755 "$output_dir"
: >"$local_rpm_manifest"
for local_rpm in "${local_rpms[@]}"; do
  printf '%s  %s\n' "$(sha256sum "$local_rpm" | awk '{print $1}')" \
    "$(basename "$local_rpm")" >>"$local_rpm_manifest"
done

if [ "$finalize_only" -eq 0 ] && [ "$resume_after_dnf" -eq 0 ]; then
  if [ -e "$rootfs" ]; then
    die "refusing to replace existing rootfs: $rootfs"
  fi

  install -d -m 0755 "$output_dir" "$rootfs"

  mapfile -t packages < <(
    sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' \
      "$repo_root/config/mobile/packages.txt" \
      "$repo_root/config/shared/platform-packages.txt" \
      "$repo_root/config/shared/mod-packages.txt" \
      "$repo_root/config/shared/application-packages.txt" \
      "$repo_root/config/mobile/phone-capability-packages.txt"
  )

  if [ "$include_ui" -eq 1 ]; then
    mapfile -t ui_packages < <(
      sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' \
        "$repo_root/config/mobile/ui-packages.txt"
    )
    packages+=("${ui_packages[@]}")
  fi

  for package in "${packages[@]}"; do
    case "$package" in
      *[!A-Za-z0-9._:+-]*) die "invalid package input: $package" ;;
    esac
  done

  dnf install -y \
    --installroot="$rootfs" \
    --releasever=44 \
    --use-host-config \
    --setopt=install_weak_deps=False \
    "${packages[@]}" \
    "${local_rpms[@]}"

  dnf clean all --installroot="$rootfs" --releasever=44 --use-host-config

elif [ "$resume_after_dnf" -eq 1 ]; then
  [ -d "$rootfs" ] || die "missing existing post-DNF rootfs: $rootfs"
  [ ! -e "$artifact" ] || die "refusing to resume with existing artifact: $artifact"
  grep -qx 'ID=fedora' "$rootfs/etc/os-release" || \
    die 'post-DNF rootfs is not Fedora'
  grep -qx 'VERSION_ID=44' "$rootfs/etc/os-release" || \
    die 'post-DNF rootfs is not Fedora 44'
  rpm --root "$rootfs" -q bash chrony dbus-daemon greetd phosh plymouth systemd-pam \
    tqftpserv >/dev/null || \
    die 'post-DNF rootfs lacks required completed packages'
fi

if [ "$finalize_only" -eq 0 ]; then
  if ! grep -q '^luma:' "$rootfs/etc/group"; then
    groupadd --root "$rootfs" --gid 1000 luma
  fi
  if ! grep -q '^luma:' "$rootfs/etc/passwd"; then
    useradd --root "$rootfs" --uid 1000 --gid luma --groups wheel,luma-display,audio \
      --create-home --shell /bin/bash --comment 'Project Luma diagnostic user' luma
  fi
  # The handheld's authenticated compositor can coexist briefly with the
  # root-owned presence greeter. In that overlap logind cannot grant seat0's
  # dynamic sound ACL to the user session, so the single local handset owner
  # needs stable access to the native ALSA card. This is also what keeps call
  # audio available to the always-on native telephony service while locked.
  usermod --root "$rootfs" --append --groups luma-display,audio luma
  usermod --root "$rootfs" --lock luma

  install -d -m 0700 -o 1000 -g 1000 "$rootfs/home/luma/.ssh"
  install -d -m 0755 "$rootfs/etc/ssh/sshd_config.d"
  cat >"$rootfs/etc/ssh/sshd_config.d/40-luma-diagnostic.conf" <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AuthenticationMethods publickey
EOF

  install -d -m 0755 "$rootfs/etc"
  printf 'handheld\n' >"$rootfs/etc/luma-device-class"
  install -D -m 0644 "$repo_root/config/mobile/plymouthd.conf" \
    "$rootfs/etc/plymouth/plymouthd.conf"
  install -D -m 0644 "$repo_root/config/mobile/environment.d/90-luma-handheld.conf" \
    "$rootfs/etc/environment.d/90-luma-handheld.conf"
  install -D -m 0755 "$repo_root/scripts/mobile/luma-phosh-session" \
    "$rootfs/usr/local/bin/luma-phosh-session"
  cat >"$rootfs/etc/luma-mobile-release" <<EOF
LUMA_ROOTFS_CONTRACT=mobile-v1
LUMA_ROOTFS_BASE=fedora-44-aarch64
LUMA_HARDWARE_BACKEND=physical-candidate
LUMA_KERNEL_CONTRACT=milos-external
LUMA_UI_INCLUDED=$include_ui
LUMA_PHYSICAL_DEVICE=false
INSTALL_AUTHORIZED=false
EOF

  install -D -m 0644 "$repo_root/config/desktop/dconf/profile/user" \
    "$rootfs/etc/dconf/profile/user"
  install -D -m 0644 "$repo_root/config/desktop/dconf/db/luma.d/00-luma-desktop" \
    "$rootfs/etc/dconf/db/luma.d/00-luma-desktop"
  install -D -m 0644 "$repo_root/config/mobile/luma-shell-handheld.dconf" \
    "$rootfs/etc/dconf/db/luma.d/90-luma-handheld"
  dconf compile "$rootfs/etc/dconf/db/luma" "$rootfs/etc/dconf/db/luma.d"

  : >"$rootfs/etc/machine-id"
  install -d -m 0755 "$rootfs/var/lib/dbus"
  ln -sfn /etc/machine-id "$rootfs/var/lib/dbus/machine-id"
else
  [ -d "$rootfs" ] || die "missing existing rootfs: $rootfs"
  grep -qx 'LUMA_ROOTFS_CONTRACT=mobile-v1' "$rootfs/etc/luma-mobile-release" || \
    die 'existing rootfs does not carry the mobile-v1 marker'
  grep -qx 'LUMA_PHYSICAL_DEVICE=false' "$rootfs/etc/luma-mobile-release" || \
    die 'existing rootfs does not carry the negative physical-device marker'
  grep -qx 'INSTALL_AUTHORIZED=false' "$rootfs/etc/luma-mobile-release" || \
    die 'existing rootfs does not carry the negative installation marker'
fi

# Fedora's installroot account tools may create shadow databases with mode
# 000. Normalize all four databases after account mutation and on every
# finalize-only pass so service-visible identities and root-only secrets are
# deterministic in the archived rootfs.
chmod 0644 "$rootfs/etc/passwd" "$rootfs/etc/group"
chmod 0600 "$rootfs/etc/shadow" "$rootfs/etc/gshadow"

[ ! -e "$artifact" ] || die "refusing to replace existing artifact: $artifact"

systemd-hwdb --root="$rootfs" update
journalctl --root="$rootfs" --update-catalog

for unit in NetworkManager.service bluetooth.service chronyd.service greetd.service ModemManager.service \
  rmtfs.service sshd.service tqftpserv.service; do
  if [ -e "$rootfs/usr/lib/systemd/system/$unit" ]; then
    systemctl --root="$rootfs" enable "$unit"
  fi
done

rpm --root "$rootfs" -qa --qf '%{NAME} %{EPOCHNUM}:%{VERSION}-%{RELEASE} %{ARCH}\n' \
  | LC_ALL=C sort >"$output_dir/rpm-manifest.txt"

tar --acls --xattrs --selinux --numeric-owner --sort=name \
  --mtime="@$source_date_epoch" -C "$rootfs" -cf - . \
  | zstd "-T$zstd_threads" "-$zstd_level" -o "$artifact"

artifact_sha256=$(sha256sum "$artifact" | awk '{print $1}')
artifact_bytes=$(stat -c '%s' "$artifact")
rpm_count=$(wc -l <"$output_dir/rpm-manifest.txt" | tr -d ' ')
contract_sha256=$({
  sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' \
    "$repo_root/config/mobile/packages.txt" \
    "$repo_root/config/mobile/ui-packages.txt" \
    "$repo_root/config/shared/platform-packages.txt" \
    "$repo_root/config/shared/mod-packages.txt" \
    "$repo_root/config/shared/application-packages.txt" \
    "$repo_root/config/mobile/phone-capability-packages.txt"
  cat "$local_rpm_manifest"
} | sha256sum | awk '{print $1}')

cat >"$output_dir/manifest.env" <<EOF
ARTIFACT_FILENAME=$(basename "$artifact")
ARTIFACT_BYTES=$artifact_bytes
ARTIFACT_SHA256=$artifact_sha256
FEDORA_RELEASE=44
ARCHITECTURE=aarch64
RPM_COUNT=$rpm_count
UI_INCLUDED=$include_ui
SOURCE_DATE_EPOCH=$source_date_epoch
ZSTD_LEVEL=$zstd_level
ZSTD_THREADS=$zstd_threads
PACKAGE_CONTRACT_SHA256=$contract_sha256
KERNEL_INCLUDED=false
DEVICE_TREE_INCLUDED=false
FIRMWARE_INCLUDED=false
INSTALL_AUTHORIZED=false
EOF

printf 'Fedora FP6 userspace artifact composed: %s\n' "$artifact"
printf 'SHA-256: %s\n' "$artifact_sha256"
printf 'This artifact is not a bootable or authorized phone image.\n'
