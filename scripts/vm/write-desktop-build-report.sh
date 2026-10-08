#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

if [ "$#" -ne 2 ]; then
  printf 'usage: %s /absolute/path/to/base.qcow2 /absolute/path/to/report\n' "$0" >&2
  exit 2
fi

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)

git_repo() {
  git -c "safe.directory=$repo_root" -C "$repo_root" "$@"
}
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
base_image=$(realpath "$1")
report=$2

for tool in awk date git realpath sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required desktop-report tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

source_revision=$(git_repo rev-parse --verify HEAD 2>/dev/null ||
  printf 'unborn')
if [ -n "$(git_repo status --porcelain)" ]; then
  source_state=dirty
else
  source_state=clean
fi

hash_input() {
  key=$1
  path=$2
  if [ ! -f "$path" ]; then
    printf 'error: desktop-report input is missing: %s\n' "$path" >&2
    return 1
  fi
  printf '%s=%s\n' "$key" "$(sha256sum "$path" | awk '{print $1}')"
}

mkdir -p "$(dirname -- "$report")"
{
  printf 'image=luma-desktop\n'
  printf 'started_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'source_revision=%s\n' "$source_revision"
  printf 'source_state=%s\n' "$source_state"
  hash_input base_sha256 "$base_image"
  hash_input settings_rpm_sha256 \
    "$repo_root/build/packages/gnome-control-center/RPMS/x86_64/gnome-control-center-50.4-${GNOME_CONTROL_CENTER_LUMA_RELEASE}.fc44.x86_64.rpm"
  hash_input shell_rpm_sha256 \
    "$repo_root/build/packages/gnome-shell/RPMS/x86_64/$GNOME_SHELL_NEVRA.rpm"
  hash_input shell_common_rpm_sha256 \
    "$repo_root/build/packages/gnome-shell/RPMS/noarch/$GNOME_SHELL_COMMON_NEVRA.rpm"
  hash_input gtk4_rpm_sha256 \
    "$repo_root/build/packages/gtk4/RPMS/x86_64/$GTK4_NEVRA.rpm"
  hash_input gtk3_rpm_sha256 \
    "$repo_root/build/packages/gtk3/RPMS/x86_64/$GTK3_NEVRA.rpm"
  hash_input libhandy_rpm_sha256 \
    "$repo_root/build/packages/libhandy/RPMS/x86_64/$LIBHANDY_NEVRA.rpm"
  hash_input libadwaita_rpm_sha256 \
    "$repo_root/build/packages/libadwaita/RPMS/x86_64/$LIBADWAITA_NEVRA.rpm"
  hash_input files_rpm_sha256 \
    "$repo_root/build/packages/nautilus/RPMS/x86_64/$NAUTILUS_NEVRA.rpm"
  hash_input files_extensions_rpm_sha256 \
    "$repo_root/build/packages/nautilus/RPMS/x86_64/$NAUTILUS_EXTENSIONS_NEVRA.rpm"
  hash_input android_runtime_rpm_sha256 \
    "$repo_root/build/packages/luma-android-runtime/RPMS/noarch/$LUMA_ANDROID_RUNTIME_NEVRA.rpm"
  hash_input relay_rpm_sha256 \
    "$repo_root/build/packages/luma-relay/RPMS/noarch/$LUMA_RELAY_NEVRA.rpm"
  hash_input application_installer_rpm_sha256 \
    "$repo_root/build/packages/luma-application-installer/RPMS/noarch/$LUMA_APPLICATION_INSTALLER_NEVRA.rpm"
  hash_input wine_relay_explorer_rpm_sha256 \
    "$repo_root/build/packages/wine-relay-integration/RPMS/x86_64/$WINE_RELAY_EXPLORER_NEVRA.rpm"
  hash_input calculator_rpm_sha256 \
    "$repo_root/build/packages/gnome-calculator/RPMS/x86_64/$GNOME_CALCULATOR_NEVRA.rpm"
  hash_input tiling_shell_rpm_sha256 \
    "$repo_root/build/packages/tiling/RPMS/noarch/$TILING_SHELL_NEVRA.rpm"
  hash_input tiling_toggle_rpm_sha256 \
    "$repo_root/build/packages/tiling/RPMS/noarch/$LUMA_TILING_TOGGLE_NEVRA.rpm"
  hash_input handheld_extension_rpm_sha256 \
    "$repo_root/build/packages/luma-handheld/RPMS/noarch/$LUMA_HANDHELD_NEVRA.rpm"
  hash_input luma_mods_rpm_sha256 \
    "$repo_root/build/packages/luma-mods/RPMS/noarch/$LUMA_MODS_NEVRA.rpm"
  hash_input luma_update_rpm_sha256 \
    "$repo_root/build/packages/luma-update/RPMS/noarch/$LUMA_UPDATE_NEVRA.rpm"
  hash_input backgrounds_rpm_sha256 \
    "$repo_root/build/packages/backgrounds/RPMS/noarch/$LUMA_BACKGROUNDS_NEVRA.rpm"
  hash_input figtree_fonts_rpm_sha256 \
    "$repo_root/build/packages/figtree-fonts/RPMS/noarch/$FIGTREE_NEVRA.rpm"
  hash_input plymouth_rpm_sha256 \
    "$repo_root/build/packages/plymouth/RPMS/x86_64/$PLYMOUTH_NEVRA.rpm"
  hash_input plymouth_core_libs_rpm_sha256 \
    "$repo_root/build/packages/plymouth/RPMS/x86_64/$PLYMOUTH_CORE_LIBS_NEVRA.rpm"
  hash_input plymouth_graphics_libs_rpm_sha256 \
    "$repo_root/build/packages/plymouth/RPMS/x86_64/$PLYMOUTH_GRAPHICS_LIBS_NEVRA.rpm"
  hash_input plymouth_plugin_script_rpm_sha256 \
    "$repo_root/build/packages/plymouth/RPMS/x86_64/$PLYMOUTH_PLUGIN_SCRIPT_NEVRA.rpm"
  hash_input plymouth_scripts_rpm_sha256 \
    "$repo_root/build/packages/plymouth/RPMS/x86_64/$PLYMOUTH_SCRIPTS_NEVRA.rpm"
  hash_input plymouth_plugin_label_rpm_sha256 \
    "$repo_root/build/packages/plymouth/RPMS/x86_64/$PLYMOUTH_PLUGIN_LABEL_NEVRA.rpm"
  hash_input plymouth_plugin_two_step_rpm_sha256 \
    "$repo_root/build/packages/plymouth/RPMS/x86_64/$PLYMOUTH_PLUGIN_TWO_STEP_NEVRA.rpm"
  hash_input plymouth_system_theme_rpm_sha256 \
    "$repo_root/build/packages/plymouth/RPMS/x86_64/$PLYMOUTH_SYSTEM_THEME_NEVRA.rpm"
  hash_input plymouth_theme_spinner_rpm_sha256 \
    "$repo_root/build/packages/plymouth/RPMS/x86_64/$PLYMOUTH_THEME_SPINNER_NEVRA.rpm"
  hash_input boot_theme_rpm_sha256 \
    "$repo_root/build/packages/boot-theme/RPMS/noarch/$LUMA_BOOT_THEME_NEVRA.rpm"
  hash_input prairie_icon_theme_rpm_sha256 \
    "$repo_root/build/packages/prairie-icon-theme/RPMS/noarch/$PRAIRIE_ICON_THEME_NEVRA.rpm"
  hash_input luma_developer_platform_rpm_sha256 \
    "$repo_root/build/packages/luma-developer-platform/x86_64/RPMS/$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA.rpm"
  hash_input prairie_core_apps_rpm_sha256 \
    "$repo_root/build/packages/prairie-core-apps/RPMS/noarch/$PRAIRIE_CORE_APPS_NEVRA.rpm"
  hash_input shell_state_rpm_sha256 \
    "$repo_root/build/packages/luma-shell-state/RPMS/noarch/$LUMA_SHELL_STATE_NEVRA.rpm"
} >"$report"

printf 'Desktop build report inputs recorded: %s\n' "$report"
