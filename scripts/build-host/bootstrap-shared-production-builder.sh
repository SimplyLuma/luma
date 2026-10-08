#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
build_root=${LUMA_BUILD_ROOT:-/srv/luma-build}
build_user=${LUMA_BUILD_USER:-luma-build}
protected_vm=${LUMA_PROTECTED_VM:-viola-windows-builder}
package_manifest=${LUMA_BUILD_PACKAGE_MANIFEST:-$repo_root/config/build-host/fedora44-packages.txt}
runner_source=${LUMA_BUILD_RUNNER_SOURCE:-$script_dir/luma-build-run}
preview_source=${LUMA_PREVIEW_PUBLISHER_SOURCE:-$script_dir/publish-preview-vm.sh}
storage_source=${LUMA_CONTAINER_STORAGE_SOURCE:-$repo_root/config/build-host/containers-storage.conf}
containers_source=${LUMA_CONTAINERS_SOURCE:-$repo_root/config/build-host/containers.conf}
change_log="$build_root/bootstrap/system-changes.log"

if [ "$(id -u)" -ne 0 ]; then
  printf 'error: bootstrap must run as root\n' >&2
  exit 1
fi
command -v virsh >/dev/null 2>&1 || {
  printf 'error: required build-host bootstrap tool is missing: virsh\n' >&2
  exit 1
}
[ -r "$package_manifest" ] || {
  printf 'error: package manifest is unavailable: %s\n' "$package_manifest" >&2
  exit 1
}
[ -r "$runner_source" ] || {
  printf 'error: guarded build runner is unavailable: %s\n' "$runner_source" >&2
  exit 1
}
[ -r "$preview_source" ] || {
  printf 'error: preview publisher is unavailable: %s\n' "$preview_source" >&2
  exit 1
}
[ -r "$storage_source" ] && [ -r "$containers_source" ] || {
  printf 'error: reviewed container configuration is unavailable\n' >&2
  exit 1
}
[ "$build_root" = /srv/luma-build ] || {
  printf 'error: reviewed container configuration is bound to /srv/luma-build\n' >&2
  exit 1
}

vm_state=$(virsh domstate "$protected_vm" 2>/dev/null | tr -d '\r' || true)
case "$vm_state" in
  running|shut\ off) ;;
  *)
  printf 'error: protected VM %s is %s; stop and investigate without modifying it\n' \
    "$protected_vm" "${vm_state:-unavailable}" >&2
  exit 75
  ;;
esac

install -d -o root -g root -m 0755 "$build_root" "$build_root/bootstrap"
touch "$change_log"
chmod 0644 "$change_log"
log_change() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$change_log"
}
install_root_copy() {
  local source_path=$1
  local target_path=$2
  local target_mode=$3
  if [ "$(readlink -f "$source_path")" != "$(readlink -m "$target_path")" ]; then
    install -o root -g root -m "$target_mode" "$source_path" "$target_path"
  else
    chown root:root "$target_path"
    chmod "$target_mode" "$target_path"
  fi
}

if ! getent group "$build_user" >/dev/null; then
  groupadd "$build_user"
  log_change "created group $build_user"
fi
if ! getent passwd "$build_user" >/dev/null; then
  useradd \
    --gid "$build_user" \
    --home-dir "$build_root/home" \
    --no-create-home \
    --shell /bin/bash \
    "$build_user"
  passwd --lock "$build_user" >/dev/null
  log_change "created locked user $build_user with home $build_root/home"
fi

if [ "$(getent passwd "$build_user" | cut -d: -f6)" != "$build_root/home" ]; then
  printf 'error: existing %s account has an unexpected home directory\n' \
    "$build_user" >&2
  exit 1
fi
if id -nG "$build_user" | tr ' ' '\n' | grep -Eq '^(libvirt|qemu)$'; then
  printf 'error: %s must not have libvirt or qemu group authority\n' "$build_user" >&2
  exit 1
fi

mapfile -t requested_packages < <(
  sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$package_manifest"
)
missing_packages=()
for package in "${requested_packages[@]}"; do
  if ! rpm -q "$package" >/dev/null 2>&1; then
    missing_packages+=("$package")
  fi
done
if [ "${#missing_packages[@]}" -gt 0 ]; then
  dnf5 install -y --setopt=install_weak_deps=False "${missing_packages[@]}"
  log_change "installed packages: ${missing_packages[*]}"
else
  log_change "package set already present; no packages installed"
fi
for tool in restorecon semanage; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required build-host bootstrap tool is missing after package installation: %s\n' \
      "$tool" >&2
    exit 1
  }
done

user_uid=$(id -u "$build_user")
user_gid=$(id -g "$build_user")
install -d -o "$user_uid" -g "$user_gid" -m 0700 \
  "$build_root/home" \
  "$build_root/cache" \
  "$build_root/config" \
  "$build_root/data" \
  "$build_root/run" \
  "$build_root/tmp" \
  "$build_root/containers" \
  "$build_root/containers/runroot" \
  "$build_root/containers/storage"
install -d -o "$user_uid" -g "$user_gid" -m 0750 \
  "$build_root/src" \
  "$build_root/src/ProjectLuma" \
  "$build_root/packages" \
  "$build_root/artifacts" \
  "$build_root/imports" \
  "$build_root/logs"
install -d -o "$user_uid" -g "$user_gid" -m 0700 \
  "$build_root/config/containers"
install -d -o root -g root -m 0755 "$build_root/bin"
install -d -o root -g root -m 0755 "$build_root/vm"

vm_fcontext="$build_root/vm(/.*)?"
if ! semanage fcontext -l |
  awk -v pattern="$vm_fcontext" \
    '$1 == pattern && $NF ~ /:virt_image_t:/ { found=1 } END { exit !found }'; then
  semanage fcontext -a -t virt_image_t "$vm_fcontext"
  log_change "declared SELinux virt_image_t context for $vm_fcontext"
fi

install -o "$user_uid" -g "$user_gid" -m 0600 "$storage_source" \
  "$build_root/config/containers/storage.conf"
install -o "$user_uid" -g "$user_gid" -m 0600 "$containers_source" \
  "$build_root/config/containers/containers.conf"

install_root_copy "$runner_source" "$build_root/bin/luma-build-run" 0755
install_root_copy "$preview_source" \
  "$build_root/bin/publish-preview-vm.sh" 0755
install_root_copy "$package_manifest" \
  "$build_root/bootstrap/fedora44-packages.txt" 0644
install_root_copy "$storage_source" \
  "$build_root/bootstrap/containers-storage.conf" 0644
install_root_copy "$containers_source" \
  "$build_root/bootstrap/containers.conf" 0644
install_root_copy "$0" \
  "$build_root/bootstrap/bootstrap-shared-production-builder.sh" 0755

{
  printf 'LUMA_BUILD_ROOT=%q\n' "$build_root"
  printf 'LUMA_BUILD_USER=%q\n' "$build_user"
  printf 'LUMA_PROTECTED_VM=%q\n' "$protected_vm"
  printf 'LUMA_ACTIVE_CPU_QUOTA=%q\n' '800%'
  printf 'LUMA_IDLE_CPU_QUOTA=%q\n' '1400%'
  printf 'LUMA_MEMORY_HIGH=%q\n' '16G'
  printf 'LUMA_MEMORY_MAX=%q\n' '20G'
} >"$build_root/bootstrap/build-host.env"
chmod 0644 "$build_root/bootstrap/build-host.env"

rpm -q "${requested_packages[@]}" | sort \
  >"$build_root/bootstrap/installed-packages.txt"
sha256sum \
  "$build_root/bootstrap/bootstrap-shared-production-builder.sh" \
  "$build_root/bootstrap/fedora44-packages.txt" \
  "$build_root/bootstrap/containers-storage.conf" \
  "$build_root/bootstrap/containers.conf" \
  "$build_root/bin/luma-build-run" \
  "$build_root/bin/publish-preview-vm.sh" \
  >"$build_root/bootstrap/SHA256SUMS"

restorecon -RF "$build_root" >/dev/null
chown -R "$user_uid:$user_gid" \
  "$build_root/home" "$build_root/cache" "$build_root/config" \
  "$build_root/data" "$build_root/run" "$build_root/tmp" \
  "$build_root/containers" "$build_root/src" "$build_root/packages" \
  "$build_root/artifacts" "$build_root/imports" "$build_root/logs"

subuid=$(awk -F: -v user="$build_user" '$1 == user { print $0 }' /etc/subuid)
subgid=$(awk -F: -v user="$build_user" '$1 == user { print $0 }' /etc/subgid)
[ -n "$subuid" ] && [ -n "$subgid" ] || {
  printf 'error: subordinate IDs were not allocated for rootless containers\n' >&2
  exit 1
}

runuser -u "$build_user" -- env -u DBUS_SESSION_BUS_ADDRESS \
  HOME="$build_root/home" \
  XDG_CACHE_HOME="$build_root/cache" \
  XDG_CONFIG_HOME="$build_root/config" \
  XDG_DATA_HOME="$build_root/data" \
  XDG_RUNTIME_DIR="$build_root/run" \
  TMPDIR="$build_root/tmp" \
  CONTAINERS_STORAGE_CONF="$build_root/config/containers/storage.conf" \
  CONTAINERS_CONF="$build_root/config/containers/containers.conf" \
  podman info --format '{{.Store.GraphRoot}}' | grep -Fx "$build_root/containers/storage"

log_change "verified rootless Podman graphroot and protected VM state=$vm_state"
printf 'Luma build root ready: %s\n' "$build_root"
printf 'Guarded runner: %s/bin/luma-build-run\n' "$build_root"
