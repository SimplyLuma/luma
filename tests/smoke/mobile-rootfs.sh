#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
# shellcheck disable=SC1091
. "$repo_root/config/mobile/luma-greeter.env"
# shellcheck disable=SC1091
. "$repo_root/config/mobile/cage-source.env"
rootfs=${LUMA_FP6_ROOTFS:?LUMA_FP6_ROOTFS is required}
output_dir=${LUMA_FP6_ROOTFS_OUTPUT:?LUMA_FP6_ROOTFS_OUTPUT is required}
packages_file=${LUMA_FP6_PACKAGES_FILE:?LUMA_FP6_PACKAGES_FILE is required}
ui_packages_file=${LUMA_FP6_UI_PACKAGES_FILE:?LUMA_FP6_UI_PACKAGES_FILE is required}
shared_mod_packages_file=${LUMA_FP6_SHARED_MOD_PACKAGES_FILE:-$repo_root/config/shared/mod-packages.txt}
shared_packages_file=${LUMA_FP6_SHARED_PACKAGES_FILE:-$repo_root/config/shared/application-packages.txt}
platform_packages_file=${LUMA_FP6_PLATFORM_PACKAGES_FILE:-$repo_root/config/shared/platform-packages.txt}
capability_packages_file=${LUMA_FP6_CAPABILITY_PACKAGES_FILE:-$repo_root/config/mobile/phone-capability-packages.txt}
manifest="$output_dir/manifest.env"
rpm_manifest="$output_dir/rpm-manifest.txt"
local_rpm_manifest="$output_dir/local-rpm-manifest.txt"

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

check 'Fedora 44 rootfs' bash -c \
  'source "$1/etc/os-release" && [ "$ID" = fedora ] && [ "$VERSION_ID" = 44 ]' _ "$rootfs"
check 'mobile-v1 rootfs marker' grep -qx 'LUMA_ROOTFS_CONTRACT=mobile-v1' \
  "$rootfs/etc/luma-mobile-release"
check 'explicit handheld device class' grep -qx 'handheld' \
  "$rootfs/etc/luma-device-class"
check 'physical candidate is negative' grep -qx 'LUMA_PHYSICAL_DEVICE=false' \
  "$rootfs/etc/luma-mobile-release"
check 'installation remains unauthorized' grep -qx 'INSTALL_AUTHORIZED=false' \
  "$rootfs/etc/luma-mobile-release"
check 'external Milos kernel contract' grep -qx 'LUMA_KERNEL_CONTRACT=milos-external' \
  "$rootfs/etc/luma-mobile-release"
check 'no kernel RPM' bash -c '! rpm --root "$1" -q kernel >/dev/null 2>&1' _ "$rootfs"
check 'no kernel module payload' bash -c \
  '[ ! -d "$1/usr/lib/modules" ] || ! find "$1/usr/lib/modules" -type f -print -quit | grep -q .' _ "$rootfs"
check 'no boot image payload' bash -c \
  '! find "$1/boot" -type f \( -name "vmlinuz*" -o -name "*.dtb" -o -name "*.img" \) -print -quit 2>/dev/null | grep -q .' _ "$rootfs"
check 'no SSH host keys' bash -c \
  '! find "$1/etc/ssh" -type f -name "ssh_host_*" -print -quit 2>/dev/null | grep -q .' _ "$rootfs"
check 'empty first-boot machine ID' test ! -s "$rootfs/etc/machine-id"
check 'root account locked' awk -F: \
  '$1 == "root" { found=1; locked=($2 ~ /^!/) } END { exit(found && locked ? 0 : 1) }' \
  "$rootfs/etc/shadow"
check 'diagnostic account is fixed and locked' bash -c \
  'passwd=$(awk -F: '\''$1 == "luma" { print $2 }'\'' "$1/etc/shadow"); [ "$(awk -F: '\''$1 == "luma" { print $3 ":" $4 ":" $6 ":" $7 }'\'' "$1/etc/passwd")" = "1000:1000:/home/luma:/bin/bash" ] && case "$passwd" in !*) true ;; *) false ;; esac' _ "$rootfs"
check 'passwd database is readable by services' test "$(stat -c '%a' "$rootfs/etc/passwd")" = 644
check 'group database is readable by services' test "$(stat -c '%a' "$rootfs/etc/group")" = 644
check 'shadow database remains root-only' test "$(stat -c '%a' "$rootfs/etc/shadow")" = 600
check 'gshadow database remains root-only' test "$(stat -c '%a' "$rootfs/etc/gshadow")" = 600
check 'no diagnostic authorized key in generic rootfs' test ! -e \
  "$rootfs/home/luma/.ssh/authorized_keys"
check 'SSH is public-key only' grep -qx 'AuthenticationMethods publickey' \
  "$rootfs/etc/ssh/sshd_config.d/40-luma-diagnostic.conf"
check 'no GDM in physical rootfs' bash -c '! rpm --root "$1" -q gdm >/dev/null 2>&1' _ "$rootfs"
check 'Phosh session installed' test -f "$rootfs/usr/share/wayland-sessions/phosh.desktop"
check 'Phosh session D-Bus launcher installed' test -x "$rootfs/usr/bin/dbus-run-session"
check 'shared Luma shell-state broker installed' rpm --root "$rootfs" -q \
  "$LUMA_SHELL_STATE_NEVRA"
check 'exact native Luma Phosh renderer installed' rpm --root "$rootfs" -q \
  "$LUMA_PHOSH_AARCH64_NEVRA"
check 'exact native Luma Phoc compositor installed' rpm --root "$rootfs" -q \
  "$LUMA_PHOC_AARCH64_NEVRA"
check 'native Luma Phosh binary installed below rollback-safe prefix' test -x \
  "$rootfs/opt/luma/phosh/libexec/phosh"
check 'exact shared Prairie icon theme installed' rpm --root "$rootfs" -q \
  "$PRAIRIE_ICON_THEME_NEVRA"
check 'exact shared GTK 4 downstream installed' rpm --root "$rootfs" -q \
  "$GTK4_AARCH64_NEVRA"
check 'exact shared GTK 3 downstream installed' rpm --root "$rootfs" -q \
  "$GTK3_AARCH64_NEVRA"
check 'exact shared libhandy downstream installed' rpm --root "$rootfs" -q \
  "$LIBHANDY_AARCH64_NEVRA"
check 'exact shared libadwaita downstream installed' rpm --root "$rootfs" -q \
  "$LIBADWAITA_AARCH64_NEVRA"
check 'shared shell-state broker is session-bound' test -L \
  "$rootfs/usr/lib/systemd/user/graphical-session.target.wants/luma-shell-state.service"
check 'shared Luma Phosh presentation installed' test -f \
  "$rootfs/usr/share/themes/Luma/gtk-3.0/gtk.css"
check 'exact shared Luma boot theme installed' rpm --root "$rootfs" -q \
  "$LUMA_BOOT_THEME_NEVRA"
check 'exact shared Luma Prism package installed' rpm --root "$rootfs" -q \
  "$LUMA_BACKGROUNDS_NEVRA"
check 'canonical Prism wallpaper installed byte-for-byte' cmp -s \
  "$repo_root/assets/wallpapers/luma-prism.png" \
  "$rootfs/usr/share/backgrounds/luma/luma-prism.png"
check 'exact native Presence greeter installed' rpm --root "$rootfs" -q \
  "$LUMA_GREETER_NEVRA"
check 'exact scanout-safe Cage compositor installed' rpm --root "$rootfs" -q \
  "$LUMA_CAGE_AARCH64_NEVRA"
check 'native Presence greeter binary installed' test -x \
  "$rootfs/usr/bin/luma-greeter"
check 'native Presence compositor initializer installed' test -x \
  "$rootfs/usr/libexec/luma-greeter-session"
check 'isolated Cage compositor installed' test -x "$rootfs/usr/bin/cage"
check 'greetd has no authenticated initial-session bypass' bash -c \
  '! grep -Fq "[initial_session]" "$1"' _ "$rootfs/etc/greetd/config.toml"
check 'greetd launches the native Presence greeter' grep -Fxq \
  'command = "/usr/bin/env LUMA_CAGE_SCANOUT_HANDOFF=1 LUMA_GREETER_OUTPUT=DSI-1 LUMA_GREETER_SCALE=3 /usr/bin/cage -s -- /usr/libexec/luma-greeter-session >/dev/null 2>&1"' \
  "$rootfs/etc/greetd/config.toml"
check 'Presence greeter is in exact local RPM provenance manifest' grep -Eq \
  "^[0-9a-f]{64}  ${LUMA_GREETER_NEVRA}\\.rpm$" "$local_rpm_manifest"
check 'scanout-safe Cage is in exact local RPM provenance manifest' grep -Eq \
  "^[0-9a-f]{64}  ${LUMA_CAGE_AARCH64_NEVRA}\\.rpm$" "$local_rpm_manifest"
check 'Plymouth control client installed' test -x "$rootfs/usr/bin/plymouth"
check 'Plymouth script renderer installed' rpm --root "$rootfs" -q plymouth-plugin-script
check 'handheld Plymouth theme selected' grep -Fxq 'Theme=luma-loading-handheld' \
  "$rootfs/etc/plymouth/plymouthd.conf"
check 'handheld Plymouth descriptor installed' test -f \
  "$rootfs/usr/share/plymouth/themes/luma-loading-handheld/luma-loading-handheld.plymouth"
check 'handheld Plymouth script installed' test -f \
  "$rootfs/usr/share/plymouth/themes/luma-loading-handheld/luma-loading-handheld.script"
check 'canonical boot wordmark source installed' cmp -s \
  "$repo_root/website/public/brand/luma-wordmark.svg" \
  "$rootfs/usr/share/luma/boot/brand/luma-wordmark.svg"
check 'handheld boot wordmark raster installed' test -s \
  "$rootfs/usr/share/plymouth/themes/luma-loading-handheld/luma-wordmark.png"
check 'handheld loader dot raster installed' test -s \
  "$rootfs/usr/share/plymouth/themes/luma-loading-handheld/luma-loading-dot.png"
check 'handheld normal boot has no ordinary status copy' bash -c \
  '! grep -Eqi "Image.Text\\(\"(Loading|Starting|Welcome|Advanced)" "$1"' _ \
  "$rootfs/usr/share/plymouth/themes/luma-loading-handheld/luma-loading-handheld.script"
check 'handheld boot keeps native password prompt callback' grep -Fq \
  'Plymouth.SetDisplayPasswordFunction(display_password_callback);' \
  "$rootfs/usr/share/plymouth/themes/luma-loading-handheld/luma-loading-handheld.script"
check 'handheld boot keeps native question prompt callback' grep -Fq \
  'Plymouth.SetDisplayQuestionFunction(display_question_callback);' \
  "$rootfs/usr/share/plymouth/themes/luma-loading-handheld/luma-loading-handheld.script"
check 'boot theme is in exact local RPM provenance manifest' grep -Eq \
  "^[0-9a-f]{64}  ${LUMA_BOOT_THEME_NEVRA}\\.rpm$" "$local_rpm_manifest"
check 'Plymouth quit unit installed' test -f \
  "$rootfs/usr/lib/systemd/system/plymouth-quit.service"
check 'Plymouth quit wired into multi-user startup' test -L \
  "$rootfs/usr/lib/systemd/system/multi-user.target.wants/plymouth-quit.service"
check 'Plymouth quit wait wired into multi-user startup' test -L \
  "$rootfs/usr/lib/systemd/system/multi-user.target.wants/plymouth-quit-wait.service"
check 'NetworkManager enabled' systemctl --root="$rootfs" is-enabled --quiet NetworkManager.service
check 'ModemManager enabled' systemctl --root="$rootfs" is-enabled --quiet ModemManager.service
check 'Bluetooth enabled' systemctl --root="$rootfs" is-enabled --quiet bluetooth.service
check 'SSH enabled' systemctl --root="$rootfs" is-enabled --quiet sshd.service
check 'greetd enabled' systemctl --root="$rootfs" is-enabled --quiet greetd.service
check 'systemd PAM module installed' test -f "$rootfs/usr/lib64/security/pam_systemd.so"
check 'exact shared Android runtime installed' rpm --root "$rootfs" -q \
  "$LUMA_ANDROID_RUNTIME_NEVRA"
check 'Prairie core has no Android runtime dependency' bash -c \
  '! rpm --root "$1" -q --requires prairie-core-apps | grep -Eqi "luma-android-runtime|waydroid"' _ "$rootfs"
check 'native IMS has no Android service dependency' bash -c \
  '! grep -Ei "^(Requires|Wants|After|Before)=.*(android|waydroid)" "$1/usr/lib/systemd/system/luma-fp6-imsd.service"' _ "$rootfs"
check 'exact native IMS package installed' rpm --root "$rootfs" -q \
  "$LUMA_IMSD_AARCH64_NEVRA"
check 'native IMS executable installed' test -x "$rootfs/usr/sbin/imsd"
check 'pre-session account receives only restricted emergency IMS methods' bash -c '
  policy="$1/usr/share/dbus-1/system.d/net.catcrafts.IMS1.conf"
  block=$(sed -n "/<policy user=\"greetd\">/,/<\\/policy>/p" "$policy")
  printf "%s" "$block" | grep -Fq "send_member=\"DialEmergency\"" &&
  printf "%s" "$block" | grep -Fq "send_member=\"GetEmergencyCallState\"" &&
  printf "%s" "$block" | grep -Fq "send_member=\"HangUpEmergency\"" &&
  ! printf "%s" "$block" | grep -Eq "<allow send_destination=\"net.catcrafts.IMS1\"/>"
' _ "$rootfs"
check 'stock Android telephony payload absent' bash -c \
  '[ ! -e "$1/vendor/bin/qcrilNrd" ] && [ ! -e "$1/system_ext/priv-app/ims/ims.apk" ] && [ ! -e "$1/system/priv-app/TeleService/TeleService.apk" ]' _ "$rootfs"
check 'AArch64 Waydroid engine installed' rpm --root "$rootfs" -q waydroid
check 'Waydroid Pulse compatibility installed' rpm --root "$rootfs" -q pipewire-pulseaudio
check 'Android-aware shared Filer installed' rpm --root "$rootfs" -q \
  "$NAUTILUS_AARCH64_NEVRA"
check 'Android-aware shared Filer extensions installed' rpm --root "$rootfs" -q \
  "$NAUTILUS_EXTENSIONS_AARCH64_NEVRA"
check 'retired Layouts app is not installed' test ! -e \
  "$rootfs/usr/share/applications/org.projectluma.Layouts.desktop"
check 'exact shared Darkroom package installed' rpm --root "$rootfs" -q \
  "$LUMA_DARKROOM_NEVRA"
check 'Darkroom handheld launcher installed from the shared package' test -f \
  "$rootfs/usr/share/applications/org.projectluma.Darkroom.desktop"
check 'handheld Android profile is on-demand' grep -qx 'KEEP_WARM=false' \
  "$rootfs/usr/share/luma/android/profiles/handheld.conf"
check 'handheld Android CPU ceiling is packaged' grep -qx 'CPU_QUOTA_PERCENT=300' \
  "$rootfs/usr/share/luma/android/profiles/handheld.conf"
check 'Android broker is session-activated' test -L \
  "$rootfs/usr/lib/systemd/user/graphical-session.target.wants/luma-android.service"
check 'only AArch64/noarch RPMs' bash -c \
  'rpm --root "$1" -qa --qf "%{NAME} %{ARCH}\n" | while read -r name arch; do case "$name:$arch" in "gpg-pubkey:(none)"|*:aarch64|*:noarch) ;; *) exit 1 ;; esac; done' _ "$rootfs"

while IFS= read -r package; do
  case "$package" in ''|'#'*) continue ;; esac
  check "package: $package" rpm --root "$rootfs" -q "$package"
done < <(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' \
  "$packages_file" "$ui_packages_file" "$platform_packages_file" "$shared_packages_file" \
  "$capability_packages_file")

check 'artifact manifest present' test -s "$manifest"
check 'RPM manifest present' test -s "$rpm_manifest"
check 'local RPM provenance manifest present' test -s "$local_rpm_manifest"
check 'artifact contract flags' bash -c \
  'source "$1" && [ "$ARCHITECTURE" = aarch64 ] && [ -n "$PACKAGE_CONTRACT_SHA256" ] && [ "$KERNEL_INCLUDED" = false ] && [ "$DEVICE_TREE_INCLUDED" = false ] && [ "$FIRMWARE_INCLUDED" = false ] && [ "$INSTALL_AUTHORIZED" = false ]' _ "$manifest"
check 'package contract hash' bash -c \
  'source "$1"; actual=$({ sed -e '\''/^[[:space:]]*#/d'\'' -e '\''/^[[:space:]]*$/d'\'' "$2" "$3" "$4" "$5" "$6"; cat "$7"; } | sha256sum | awk '\''{print $1}'\''); [ "$PACKAGE_CONTRACT_SHA256" = "$actual" ]' _ \
  "$manifest" "$packages_file" "$ui_packages_file" "$platform_packages_file" \
  "$shared_packages_file" "$capability_packages_file" "$local_rpm_manifest"
check 'RPM manifest count' bash -c \
  'source "$1" && [ "$RPM_COUNT" -eq "$(wc -l <"$2")" ]' _ "$manifest" "$rpm_manifest"
check 'artifact byte length' bash -c \
  'source "$1" && [ "$ARTIFACT_BYTES" -eq "$(stat -c %s "$2/$ARTIFACT_FILENAME")" ]' _ "$manifest" "$output_dir"
check 'artifact SHA-256' bash -c \
  'source "$1" && printf "%s  %s/%s\n" "$ARTIFACT_SHA256" "$2" "$ARTIFACT_FILENAME" | sha256sum -c - >/dev/null' _ "$manifest" "$output_dir"
check 'zstd stream integrity' bash -c \
  'source "$1" && zstd -q -t "$2/$ARTIFACT_FILENAME"' _ "$manifest" "$output_dir"
check 'tar root marker retained' bash -c \
  'source "$1" && zstd -q -dc "$2/$ARTIFACT_FILENAME" | tar -tf - | grep -qx "./etc/luma-mobile-release"' _ "$manifest" "$output_dir"

if [ "$failures" -ne 0 ]; then
  printf '\nFedora FP6 rootfs smoke test: FAIL (%s checks)\n' "$failures"
  exit 1
fi

printf '\nFedora FP6 rootfs smoke test: PASS\n'
