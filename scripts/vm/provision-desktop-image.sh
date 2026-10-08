#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

inputs=/tmp/inputs.env
if [ ! -f "$inputs" ]; then
  printf 'error: desktop composition inputs are missing: %s\n' "$inputs" >&2
  exit 1
fi
# shellcheck disable=SC1090
. "$inputs"

systemctl enable sshd.service

# Luma's own packages are installed from a real rpm-md repository, not from
# local file paths. rpm-ostree records the repositories a base commit was
# composed from and needs them to resolve the *base* side of a replacement; a
# package installed from a file has no origin once the provisioned tree is
# exported, so every later `rpm-ostree override replace` of it is recorded and
# silently dropped. See docs/design/luma-package-repository.md.
#
# The repository ships in the image at this same path, and the .repo file
# installed below is the same file the compose used, so the identifier stays
# resolvable for the life of the image.
luma_repo_dir=/var/lib/luma/package-repository
luma_repo_archive=/var/tmp/luma-package-repository.tar
if [ ! -f "$luma_repo_archive" ]; then
  printf 'error: Luma package repository archive is missing: %s\n' \
    "$luma_repo_archive" >&2
  exit 1
fi
rm -rf -- "$luma_repo_dir"
install -d -m 0755 /var/lib/luma "$luma_repo_dir"
tar -xf "$luma_repo_archive" -C "$luma_repo_dir"
rm -f -- "$luma_repo_archive"
install -D -m 0644 /tmp/luma-desktop.repo /etc/yum.repos.d/luma-desktop.repo
# rpm-ostreed is confined. Anything that has travelled through a podman `:z`
# mount on the build host carries container_file_t, which rpm-ostreed cannot
# read; the resulting failure names the package, not the repository. Relabel
# unconditionally so the repository is readable however it arrived here. -F is
# required: container_file_t is a customizable type, and a plain restorecon
# leaves customizable types exactly as it found them.
restorecon -RF /etc/yum.repos.d/luma-desktop.repo "$luma_repo_dir" \
  >/dev/null 2>&1 || :
[ -f "$luma_repo_dir/repodata/repomd.xml" ] || {
  printf 'error: Luma package repository has no metadata: %s\n' \
    "$luma_repo_dir" >&2
  exit 1
}

# A recovered or previously accepted base can already request older revisions
# of Luma's layered RPMs. rpm-ostree deliberately carries those requests
# forward, so installing a newer package with the same name produces a
# two-version depsolve conflict. Remove only matching requested layers first;
# base-package overrides are replaced separately below. The package names are
# read from the repository's own copies of the pinned RPMs, so this list means
# exactly what it did when it read them from /tmp.
local_layer_rpms=(
  "$luma_repo_dir/Packages/$LUMA_BOOT_THEME_NEVRA.rpm"
  "$luma_repo_dir/Packages/$PLYMOUTH_PLUGIN_SCRIPT_NEVRA.rpm"
  "$luma_repo_dir/Packages/$LUMA_CALCULATOR_NEVRA.rpm"
  "$luma_repo_dir/Packages/$LUMA_TERMINAL_NEVRA.rpm"
  "$luma_repo_dir/Packages/$LUMA_DISKS_NEVRA.rpm"
  "$luma_repo_dir/Packages/$TILING_SHELL_NEVRA.rpm"
  "$luma_repo_dir/Packages/$LUMA_TILING_TOGGLE_NEVRA.rpm"
  "$luma_repo_dir/Packages/$LUMA_BACKGROUNDS_NEVRA.rpm"
  "$luma_repo_dir/Packages/$PRAIRIE_ICON_THEME_NEVRA.rpm"
  "$luma_repo_dir/Packages/$FIGTREE_NEVRA.rpm"
  "$luma_repo_dir/Packages/$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA.rpm"
  "$luma_repo_dir/Packages/$PRAIRIE_CORE_APPS_NEVRA.rpm"
  "$luma_repo_dir/Packages/$LUMA_MONITOR_NEVRA.rpm"
  "$luma_repo_dir/Packages/$LUMA_SHELL_STATE_NEVRA.rpm"
  "$luma_repo_dir/Packages/$LUMA_MODS_NEVRA.rpm"
  "$luma_repo_dir/Packages/$LUMA_UPDATE_NEVRA.rpm"
  "$luma_repo_dir/Packages/$LUMA_SEARCH_NEVRA.rpm"
  "$luma_repo_dir/Packages/$LUMA_REEL_NEVRA.rpm"
  "$luma_repo_dir/Packages/$LUMA_DARKROOM_NEVRA.rpm"
  "$luma_repo_dir/Packages/$LUMA_ANDROID_RUNTIME_NEVRA.rpm"
  "$luma_repo_dir/Packages/$WINE_RELAY_EXPLORER_NEVRA.rpm"
  "$luma_repo_dir/Packages/$LUMA_RELAY_NEVRA.rpm"
  "$luma_repo_dir/Packages/$LUMA_APPLICATION_INSTALLER_NEVRA.rpm"
)
mapfile -t local_layer_names < <(
  rpm -qp --queryformat '%{NAME}\n' "${local_layer_rpms[@]}" | sort -u
)
# The native Shelf supersedes the historical desktop extension. Include its
# package name in stale-layer removal even though no replacement RPM is copied
# into the desktop composition.
# The canonical removal contract also governs the legacy composition lane.
mapfile -t removed_application_packages < <(
  sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' /tmp/removed-packages.txt |
    grep -E '^(gnome-disk-utility|gnome-calculator|ptyxis|gnome-terminal|gnome-console|yelp|firefox|gnome-extensions-app|openemu-linux|luma-imager)$'
)
local_layer_names+=(gnome-shell-extension-dash-to-dock foot gnome-calendar gnome-text-editor gnome-system-monitor "${removed_application_packages[@]}")
status_json=$(mktemp)
rpm-ostree status --json >"$status_json"
mapfile -t stale_layer_requests < <(
  python3 - "$status_json" "${local_layer_names[@]}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    deployments = json.load(stream).get("deployments", [])
if not deployments:
    raise SystemExit("rpm-ostree status reported no deployments")
target = deployments[0]
requests = []
for key in (
    "requested-packages",
    "requested-local-packages",
    "requested-local-fileoverride-packages",
):
    requests.extend(target.get(key, []))
seen = set()
for value in requests:
    if value in seen:
        continue
    if any(value == name or value.startswith(f"{name}-") for name in sys.argv[2:]):
        print(value)
        seen.add(value)
PY
)
rm -f -- "$status_json"
if [ "${#stale_layer_requests[@]}" -gt 0 ]; then
  rpm-ostree uninstall "${stale_layer_requests[@]}"
fi

# Fedora's Tour is a first-login application, not part of Luma's desktop
# contract. Remove it from the immutable base deployment so a fresh account
# reaches the Prairie desktop directly and no Fedora/Silverblue branding can
# reappear through per-user state.
rpm-ostree override remove gnome-tour

# Remove bundled assets and chooser metadata at package ownership, rather than
# hiding them per user. The same policy is consumed by handheld composition.
background_packages=()
while IFS= read -r package; do
  case "$package" in ''|'#'*) continue ;; esac
  if rpm -q "$package" >/dev/null 2>&1; then
    background_packages+=("$package")
  fi
done </tmp/excluded-background-packages.txt
if [ "${#background_packages[@]}" -gt 0 ]; then
  rpm-ostree override remove "${background_packages[@]}"
fi

# Base-package replacements below stay file-based. These are Fedora packages
# whose base version already has a resolvable rpm-md origin, which is the half
# that the layered Luma packages were missing; nothing here needs the Luma
# repository, and changing them would change which transaction installs what.
#
# Plymouth subpackages require an exact release match. Replace the complete
# installed family in one dependency-safe transaction.
rpm-ostree override replace \
  "/tmp/$PLYMOUTH_NEVRA.rpm" \
  "/tmp/$PLYMOUTH_CORE_LIBS_NEVRA.rpm" \
  "/tmp/$PLYMOUTH_GRAPHICS_LIBS_NEVRA.rpm" \
  "/tmp/$PLYMOUTH_SCRIPTS_NEVRA.rpm" \
  "/tmp/$PLYMOUTH_PLUGIN_LABEL_NEVRA.rpm" \
  "/tmp/$PLYMOUTH_PLUGIN_TWO_STEP_NEVRA.rpm" \
  "/tmp/$PLYMOUTH_SYSTEM_THEME_NEVRA.rpm" \
  "/tmp/$PLYMOUTH_THEME_SPINNER_NEVRA.rpm"
# Every layered Luma package below is requested as the exact NEVRA pinned in
# config/desktop/inputs.env. The repository decides nothing about which version
# is installed: it carries exactly one build of each pinned package, and an
# unresolvable pin fails the transaction rather than selecting a near miss.
rpm-ostree install \
  "$LUMA_BOOT_THEME_NEVRA" \
  "$PLYMOUTH_PLUGIN_SCRIPT_NEVRA"

rpm-ostree override replace \
  "/tmp/$GNOME_SHELL_NEVRA.rpm" \
  "/tmp/$GNOME_SHELL_COMMON_NEVRA.rpm" \
  "/tmp/gnome-control-center-50.4-${GNOME_CONTROL_CENTER_LUMA_RELEASE}.fc44.x86_64.rpm" \
  "/tmp/gnome-control-center-filesystem-50.4-${GNOME_CONTROL_CENTER_LUMA_RELEASE}.fc44.noarch.rpm" \
  "/tmp/$GTK4_NEVRA.rpm" \
  "/tmp/$GTK3_NEVRA.rpm" \
  "/tmp/$LIBHANDY_NEVRA.rpm" \
  "/tmp/$LIBADWAITA_NEVRA.rpm" \
  "/tmp/$NAUTILUS_NEVRA.rpm" \
  "/tmp/$NAUTILUS_EXTENSIONS_NEVRA.rpm"
rpm-ostree install \
  "$LUMA_CALCULATOR_NEVRA" \
  "$LUMA_TERMINAL_NEVRA" \
  "$LUMA_DISKS_NEVRA" \
  "$TILING_SHELL_NEVRA" \
  "$LUMA_TILING_TOGGLE_NEVRA" \
  "$LUMA_BACKGROUNDS_NEVRA" \
  "$PRAIRIE_ICON_THEME_NEVRA" \
  "$FIGTREE_NEVRA" \
  "$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA" \
  "$PRAIRIE_CORE_APPS_NEVRA" \
  "$LUMA_MONITOR_NEVRA" \
  "$LUMA_SHELL_STATE_NEVRA" \
  "$LUMA_MODS_NEVRA" \
  "$LUMA_UPDATE_NEVRA" \
  "$LUMA_SEARCH_NEVRA" \
  "$LUMA_REEL_NEVRA" \
  "$LUMA_DARKROOM_NEVRA"
# The Luma Monitor RPM was admitted in the transaction above. Remove Fedora's
# base launcher only after that replacement succeeds; never hide an unowned
# application by name or remove the user's independently installed packages.
if rpm -q gnome-system-monitor >/dev/null 2>&1; then
  rpm-ostree override remove gnome-system-monitor
fi
rpm-ostree install \
  waydroid \
  "$LUMA_ANDROID_RUNTIME_NEVRA"
rpm-ostree install \
  wine ntsync-autoload wine-dxvk winetricks \
  "$WINE_RELAY_EXPLORER_NEVRA" \
  "$LUMA_RELAY_NEVRA"
rpm-ostree install \
  "$LUMA_APPLICATION_INSTALLER_NEVRA"

# The shared application role list is also the desktop composition contract.
# Local/downstream packages above keep their exact pinned RPMs; only roles not
# already present in the base deployment and not supplied by a local Luma RPM
# are resolved from Fedora. This currently moves Text Editor onto the host's
# patched GTK/libadwaita stack instead of inheriting an unrelated Flatpak
# runtime, while preserving Flatpak as Luma's application substrate.
mapfile -t shared_application_roles < <(
  sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' \
    /tmp/application-packages.txt
)
# Desktop-only roles (GNOME Shell utilities) join the same resolution; the
# handheld composition never reads this list.
if [ -f /tmp/desktop-application-packages.txt ]; then
  mapfile -t -O "${#shared_application_roles[@]}" shared_application_roles < <(
    sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' \
      /tmp/desktop-application-packages.txt
  )
fi
# Remove excluded base applications without weakening dependency checks.
installed_application_removals=()
for package in "${removed_application_packages[@]}"; do
  rpm -q "$package" >/dev/null 2>&1 && installed_application_removals+=("$package")
done
if [ "${#installed_application_removals[@]}" -gt 0 ]; then
  rpm-ostree override remove "${installed_application_removals[@]}"
fi
native_role_requests=()
for package in "${shared_application_roles[@]}"; do
  if printf '%s\n' "${removed_application_packages[@]}" | grep -Fxq "$package"; then
    continue
  fi
  supplied_locally=0
  for local_name in "${local_layer_names[@]}" \
    gnome-shell gnome-shell-common gnome-control-center \
    gnome-control-center-filesystem gtk4 libadwaita nautilus \
    nautilus-extensions; do
    if [ "$package" = "$local_name" ]; then
      supplied_locally=1
      break
    fi
  done
  if [ "$supplied_locally" -eq 0 ] && ! rpm -q "$package" >/dev/null 2>&1; then
    native_role_requests+=("$package")
  fi
done
if [ "${#native_role_requests[@]}" -gt 0 ]; then
  rpm-ostree install "${native_role_requests[@]}"
fi

# Remove only an explicitly mapped Fedora system Flatpak whose declared native
# provider is installed or requested in this deployment. Never touch
# user-installed Flatpaks or use a global GTK_THEME override inside sandboxes.
while read -r app_id provider remainder; do
  case "$app_id" in
    ''|'#'*) continue ;;
  esac
  if [ -z "${provider:-}" ] || [ -n "${remainder:-}" ]; then
    printf 'error: malformed system Flatpak replacement entry: %s %s %s\n' \
      "$app_id" "${provider:-}" "${remainder:-}" >&2
    exit 1
  fi
  if rpm -q "$provider" >/dev/null 2>&1 || \
     printf '%s\n' "${native_role_requests[@]}" | grep -Fxq "$provider"; then
    flatpak uninstall --system --noninteractive "$app_id" >/dev/null 2>&1 || :
  else
    printf 'warning: keeping system Flatpak %s; native provider %s is not requested\n' \
      "$app_id" "$provider" >&2
  fi
done </tmp/system-flatpak-replacements.txt

install -D -m 0644 /tmp/user /etc/dconf/profile/user
install -D -m 0644 /tmp/00-luma-desktop /etc/dconf/db/luma.d/00-luma-desktop
install -D -m 0644 /tmp/00-prairie-login /etc/dconf/db/gdm.d/00-prairie-login
install -D -m 0644 /tmp/useradd /etc/default/useradd
install -D -m 0644 /tmp/plymouthd.conf /etc/plymouth/plymouthd.conf
install -D -m 0644 /tmp/fprintd-no-idle-exit.conf \
  /etc/systemd/system/fprintd.service.d/10-luma-no-idle-exit.conf
install -D -m 0644 /tmp/waydroid-container-ordering.conf \
  /etc/systemd/system/waydroid-container.service.d/10-luma-ordering.conf
systemctl preset waydroid-container.service
if systemctl is-enabled --quiet waydroid-container.service; then
  printf 'error: unused Android support would start at boot\n' >&2
  exit 1
fi
# ADR-021: a Luma desktop is not a Bluetooth hands-free unit for every bonded phone;
# Luma Connect enables it per user for the phone chosen for calls.
install -D -m 0644 /tmp/60-luma-bluetooth-roles.conf \
  /etc/wireplumber/wireplumber.conf.d/60-luma-bluetooth-roles.conf
# No error-bell sound: GTK never asks for a beep on Backspace in an empty
# field (or any other refused action), so nothing downstream reaches the
# sound theme. See config/desktop/gtk-3.0/settings.ini and gtk-4.0/settings.ini.
install -D -m 0644 /tmp/gtk-3.0-settings.ini /etc/gtk-3.0/settings.ini
install -D -m 0644 /tmp/gtk-4.0-settings.ini /etc/gtk-4.0/settings.ini
dconf update
rpm-ostree initramfs --enable
# efi_pstore keeps a kernel panic's last messages for systemd-pstore; Fedora
# builds it disabled (luma-vitals-crash-evidence ships the same argument in
# /usr/lib/bootc/kargs.d for image-based installs).
rpm-ostree kargs --append-if-missing=rhgb --append-if-missing=quiet \
  --append-if-missing=efi_pstore.pstore_disable=0
