#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

if [[ $# -ne 1 ]]; then
  printf 'usage: %s /absolute/path/to/new-bundle-directory\n' "$0" >&2
  exit 2
fi

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

output=$1
case $output in
  /*) ;;
  *) output="$PWD/$output" ;;
esac
[[ ! -e $output ]] || {
  printf 'error: refusing to replace existing preview bundle: %s\n' "$output" >&2
  exit 1
}

rpm_paths=(
  "build/packages/gnome-shell/RPMS/x86_64/$GNOME_SHELL_NEVRA.rpm"
  "build/packages/gnome-shell/RPMS/noarch/$GNOME_SHELL_COMMON_NEVRA.rpm"
  "build/packages/gnome-control-center/RPMS/x86_64/gnome-control-center-50.4-${GNOME_CONTROL_CENTER_LUMA_RELEASE}.fc44.x86_64.rpm"
  "build/packages/gnome-control-center/RPMS/noarch/gnome-control-center-filesystem-50.4-${GNOME_CONTROL_CENTER_LUMA_RELEASE}.fc44.noarch.rpm"
  "build/packages/gtk4/RPMS/x86_64/$GTK4_NEVRA.rpm"
  "build/packages/gtk3/RPMS/x86_64/$GTK3_NEVRA.rpm"
  "build/packages/libhandy/RPMS/x86_64/$LIBHANDY_NEVRA.rpm"
  "build/packages/libadwaita/RPMS/x86_64/$LIBADWAITA_NEVRA.rpm"
  "build/packages/nautilus/RPMS/x86_64/$NAUTILUS_NEVRA.rpm"
  "build/packages/nautilus/RPMS/x86_64/$NAUTILUS_EXTENSIONS_NEVRA.rpm"
  "build/packages/gnome-calculator/RPMS/x86_64/$GNOME_CALCULATOR_NEVRA.rpm"
  "build/packages/gnome-text-editor/RPMS/x86_64/$GNOME_TEXT_EDITOR_NEVRA.rpm"
  "build/packages/gnome-calendar/RPMS/x86_64/$GNOME_CALENDAR_NEVRA.rpm"
  "build/packages/tiling/RPMS/noarch/$TILING_SHELL_NEVRA.rpm"
  "build/packages/tiling/RPMS/noarch/$LUMA_TILING_TOGGLE_NEVRA.rpm"
  "build/packages/backgrounds/RPMS/noarch/$LUMA_BACKGROUNDS_NEVRA.rpm"
  "build/packages/boot-theme/RPMS/noarch/$LUMA_BOOT_THEME_NEVRA.rpm"
  "build/packages/prairie-icon-theme/RPMS/noarch/$PRAIRIE_ICON_THEME_NEVRA.rpm"
  "build/packages/figtree-fonts/RPMS/noarch/$FIGTREE_NEVRA.rpm"
  "build/packages/prairie-core-apps/RPMS/noarch/$PRAIRIE_CORE_APPS_NEVRA.rpm"
  "build/packages/luma-developer-platform/x86_64/RPMS/$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA.rpm"
  "build/packages/luma-android-runtime/RPMS/noarch/$LUMA_ANDROID_RUNTIME_NEVRA.rpm"
  "build/packages/luma-relay/RPMS/noarch/$LUMA_RELAY_NEVRA.rpm"
  "build/packages/luma-application-installer/RPMS/noarch/$LUMA_APPLICATION_INSTALLER_NEVRA.rpm"
  "build/packages/wine-relay-integration/RPMS/x86_64/$WINE_RELAY_EXPLORER_NEVRA.rpm"
  "build/packages/plymouth/RPMS/x86_64/$PLYMOUTH_NEVRA.rpm"
  "build/packages/plymouth/RPMS/x86_64/$PLYMOUTH_CORE_LIBS_NEVRA.rpm"
  "build/packages/plymouth/RPMS/x86_64/$PLYMOUTH_GRAPHICS_LIBS_NEVRA.rpm"
  "build/packages/plymouth/RPMS/x86_64/$PLYMOUTH_PLUGIN_SCRIPT_NEVRA.rpm"
  "build/packages/plymouth/RPMS/x86_64/$PLYMOUTH_SCRIPTS_NEVRA.rpm"
  "build/packages/plymouth/RPMS/x86_64/$PLYMOUTH_PLUGIN_LABEL_NEVRA.rpm"
  "build/packages/plymouth/RPMS/x86_64/$PLYMOUTH_PLUGIN_TWO_STEP_NEVRA.rpm"
  "build/packages/plymouth/RPMS/x86_64/$PLYMOUTH_SYSTEM_THEME_NEVRA.rpm"
  "build/packages/plymouth/RPMS/x86_64/$PLYMOUTH_THEME_SPINNER_NEVRA.rpm"
)

for relative in "${rpm_paths[@]}"; do
  [[ -s "$repo_root/$relative" ]] || {
    printf 'error: required preview artifact is missing: %s\n' "$relative" >&2
    exit 1
  }
done

install -d -m 0755 "$output/rpms" "$output/config"
for relative in "${rpm_paths[@]}"; do
  install -m 0644 "$repo_root/$relative" "$output/rpms/"
done

install -m 0644 "$repo_root/config/desktop/dconf/profile/user" \
  "$output/config/dconf-profile-user"
install -m 0644 \
  "$repo_root/config/desktop/dconf/db/luma.d/00-luma-desktop" \
  "$output/config/dconf-luma"
install -m 0644 \
  "$repo_root/config/desktop/dconf/db/gdm.d/00-prairie-login" \
  "$output/config/dconf-gdm"
install -m 0644 "$repo_root/config/boot/plymouthd.conf" \
  "$output/config/plymouthd.conf"
install -m 0755 "$repo_root/config/boot/apply-grub-policy.sh" \
  "$output/config/apply-grub-policy.sh"
install -m 0755 "$repo_root/scripts/native/deploy-workstation-preview.sh" \
  "$output/deploy-workstation-preview.sh"

source_revision=${LUMA_SOURCE_REVISION:-}
if [[ -z $source_revision ]]; then
  source_revision=$(git -C "$repo_root" rev-parse --verify HEAD 2>/dev/null || true)
fi
[[ -n $source_revision ]] || {
  printf 'error: set LUMA_SOURCE_REVISION when assembling from a source export\n' >&2
  exit 1
}

{
  printf 'bundle_format=1\n'
  printf 'source_revision=%s\n' "$source_revision"
  printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'base=fedora-workstation-44-native-preview\n'
  printf '\n[sha256]\n'
  (
    cd "$output"
    find config rpms -type f -print0 |
      sort -z |
      xargs -0 sha256sum
    sha256sum deploy-workstation-preview.sh
  )
} >"$output/manifest.txt"

printf 'Luma Workstation preview bundle: %s\n' "$output"
