#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

build_root=${LUMA_BUILD_ROOT:-/srv/luma-build}
source_root="$build_root/src/ProjectLuma"
current="$build_root/artifacts/current"
staging_repo="$build_root/updates/staging/repo"
webroot="$build_root/updates/webroot"
signing_home="$build_root/signing/recent-development-gnupg"
fingerprint_file="$build_root/config/recent-signing-fingerprint"
tls_ca="$build_root/secrets/recent-tls/ca.crt"

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || fail 'Recent publication must run as root'
[ -L "$current" ] || fail 'accepted current artifact pointer is missing'
for path in \
  "$current/luma-desktop.qcow2" \
  "$current/luma-desktop.qcow2.deployment.json" \
  "$current/build-report.txt" \
  "$fingerprint_file" "$tls_ca"; do
  [ -s "$path" ] || fail "required publication input is missing: $path"
done
fingerprint=$(<"$fingerprint_file")
[[ "$fingerprint" =~ ^[0-9A-F]{40}$ ]] || fail 'invalid Recent signing fingerprint'

for tool in blkid flock lsblk modprobe mount mountpoint ostree qemu-nbd umount; do
  command -v "$tool" >/dev/null 2>&1 || fail "required image-mount tool is missing: $tool"
done

# A bare OSTree repository's file objects include ownership and xattrs in
# their checksums. FUSE/libguestfs presentation can change that metadata and
# produce false corruption. Attach the immutable QCOW2 read-only and use the
# kernel filesystem implementation so the exact repository metadata is seen.
exec 8>/run/lock/luma-recent-nbd.lock
flock -n 8 || fail 'another Recent image publication is active'
modprobe nbd max_part=16
nbd=
for candidate in /sys/block/nbd*; do
  [ -d "$candidate" ] || continue
  [ ! -e "$candidate/pid" ] || continue
  nbd="/dev/${candidate##*/}"
  break
done
[ -n "$nbd" ] || fail 'no unused NBD device is available'

mount_root=$(mktemp -d "$build_root/tmp/recent-source.XXXXXX")
chmod 0755 "$mount_root"
source_archive=$(mktemp -d "$build_root/tmp/recent-source-archive.XXXXXX")
nbd_attached=0
mounted=0
cleanup() {
  if [ "$mounted" -eq 1 ]; then umount "$mount_root" >/dev/null 2>&1 || true; fi
  if [ "$nbd_attached" -eq 1 ]; then qemu-nbd --disconnect "$nbd" >/dev/null 2>&1 || true; fi
  rmdir "$mount_root" >/dev/null 2>&1 || true
  case "$source_archive" in
    "$build_root"/tmp/recent-source-archive.*)
      rm -rf -- "$source_archive"
      ;;
  esac
}
trap cleanup EXIT INT TERM

qemu-nbd --read-only --connect="$nbd" "$current/luma-desktop.qcow2"
nbd_attached=1
for _ in $(seq 1 20); do
  mapfile -t partitions < <(lsblk -lnpo NAME,TYPE "$nbd" | awk '$2 == "part" { print $1 }')
  [ "${#partitions[@]}" -gt 0 ] && break
  sleep 0.25
done
[ "${#partitions[@]}" -gt 0 ] || fail 'accepted image exposes no partitions'

source_repo=
for partition in "${partitions[@]}"; do
  filesystem=$(blkid -s TYPE -o value "$partition" 2>/dev/null || true)
  case "$filesystem" in
    ext2|ext3|ext4) mount_options=ro,noload ;;
    xfs) mount_options=ro,norecovery ;;
    btrfs|vfat) mount_options=ro ;;
    *) continue ;;
  esac
  if ! mount -o "$mount_options" "$partition" "$mount_root"; then
    continue
  fi
  mounted=1
  for candidate in "$mount_root/ostree/repo" "$mount_root/sysroot/ostree/repo"; do
    if [ -f "$candidate/config" ]; then
      [ -z "$source_repo" ] || fail 'accepted filesystem exposes multiple OSTree repositories'
      source_repo=$candidate
    fi
  done
  if [ -n "$source_repo" ]; then
    break
  fi
  umount "$mount_root"
  mounted=0
done
[ -n "$source_repo" ] || fail 'accepted image does not expose its OSTree repository'

accepted_commit=$(
  "$source_root/scripts/update/read-booted-deployment-checksum.py" \
    "$current/luma-desktop.qcow2.deployment.json"
)
ostree init --repo="$source_archive" --mode=archive
LUMA_BUILD_USER=root "$build_root/bin/luma-build-run" \
  ostree pull-local --repo="$source_archive" --untrusted \
    --disable-verify-bindings "$source_repo" "$accepted_commit"
LUMA_BUILD_USER=root "$build_root/bin/luma-build-run" \
  ostree fsck --repo="$source_archive" --quiet
chown -R luma-build:luma-build "$source_archive"

"$build_root/bin/luma-build-run" env LUMA_STAGE_REMOTE=build-origin \
  "$source_root/scripts/update/publish-recent.sh" \
    --image "$current/luma-desktop.qcow2" \
    --source-repo "$source_archive" \
    --deployment-json "$current/luma-desktop.qcow2.deployment.json" \
    --build-report "$current/build-report.txt" \
    --repo "$staging_repo" \
    --gpg-key "$fingerprint" \
    --gpg-homedir "$signing_home"

"$build_root/bin/luma-build-run" \
  "$source_root/scripts/update/activate-recent-webroot.sh" \
    --repo "$staging_repo" --webroot "$webroot"

systemctl enable --now luma-recent-http.service
systemctl reload-or-restart luma-recent-http.service
recent_ready=0
for _ in $(seq 1 20); do
  if curl --fail --silent --cacert "$tls_ca" \
      https://127.0.0.1:8443/health >/dev/null 2>&1; then
    recent_ready=1
    break
  fi
  sleep 0.25
done
[ "$recent_ready" -eq 1 ] || fail 'Recent HTTPS service did not become ready'
curl --fail --silent --show-error --cacert "$tls_ca" \
  https://127.0.0.1:8443/luma/repo/summary >/dev/null
commit=$(ostree rev-parse --repo="$webroot/current" luma/44/x86_64/recent)
printf 'Recent publication active: %s\n' "$commit"
printf 'Transport endpoint: https://127.0.0.1:8443/luma/repo\n'
