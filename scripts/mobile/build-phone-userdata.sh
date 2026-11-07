#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# The generic phone userdata image: one per ARM build, the same file for every
# phone. It holds the generic ARM commit and nothing device-specific; a phone's
# device kit (scripts/devices/build-device-kit.py) adds its packages on the
# first boot (luma-device-layer.service, see devices/README.md).
#
# userdata holds a GPT with 4096-byte sectors (UFS; the initramfs cannot read a
# 512-byte table):
#   p1 LumaBoot  FAT32, 480 MiB from 1 MiB, volume ID 032D-57AD: empty here;
#                the device kit's image writes this range only
#   p2 LumaRoot  ext4, UUID 03797c05-cf87-4eda-adc4-6ce111baf2d3: /ostree, /boot
# Every phone boot image names both (pmos_root_uuid, pmos_boot_uuid) and boots
# ostree's default deployment (scripts/mobile/common/initramfs). The initramfs
# grows LumaRoot to fill userdata on the first boot.
#
# usage: build-phone-userdata.sh --repo REPO --ref REF --output FILE [--size SIZE]
#
# REF (luma/<major>/aarch64/<channel>) is deployed from the OSTree repository
# REPO with origin luma:REF; a commit carrying a hardware target is refused.
# /ostree/root.a and root.b point at the deployment (the aboot slot links).
# /etc/luma/device-kit.conf names LumaBoot as the device kit's filesystem.
# One user, luma, password 1234. /etc/systemd/system/boot.automount is masked
# (systemd-gpt-auto's /boot automount breaks shutdown on these phones).
#
# Writes FILE (Android sparse; zero blocks as FILL so a reflash overwrites the
# previous install, scripts/mobile/img2simg.py) and FILE.manifest. Runs as root
# on Linux; nothing runs inside the payload, so any host architecture works.
# Work files go to TMPDIR.
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
fail() { printf 'build-phone-userdata: %s\n' "$*" >&2; exit 1; }
if [ -z "${LUMA_PHONE_USERDATA_NS:-}" ]; then
  LUMA_PHONE_USERDATA_NS=1 exec unshare --mount --propagation private bash "$0" "$@"
fi
root_uuid=03797c05-cf87-4eda-adc4-6ce111baf2d3
boot_uuid=032D-57AD
repo="" ref="" output="" size=12G
while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo) repo=$(realpath -e "${2:?}"); shift 2 ;;
    --ref) ref=${2:?}; shift 2 ;;
    --output) output=${2:?}; shift 2 ;;
    --size) size=${2:?}; shift 2 ;;
    *) sed -n 's/^# usage: /usage: /p' "$0" >&2; exit 2 ;;
  esac
done
[ -n "$repo" ] && [ -n "$ref" ] && [ -n "$output" ] || fail "missing arguments"
[[ "$ref" =~ ^luma/[0-9]+/aarch64/[a-z0-9-]+$ ]] || fail "$ref is not a generic ARM channel ref"
[ "$(id -u)" = 0 ] || fail "run as root"

commit=$(ostree --repo="$repo" rev-parse "$ref")
target=$(ostree --repo="$repo" show --print-metadata-key=org.projectluma.hardware-target "$commit" 2>/dev/null | tr -d "'" || true)
[ -z "$target" ] || fail "$commit is built for hardware target $target; phones deploy the generic ARM commit"
version=$(ostree --repo="$repo" show --print-metadata-key=version "$commit" | tr -d "'")
echo "Payload: luma:$ref $commit ($version)"

work=$(mktemp -d "${TMPDIR:-/var/tmp}/phone-userdata.XXXXXX")
disk=$work/disk.raw mnt=$work/mnt loop=""
cleanup() {
  umount -R "$mnt" 2>/dev/null || true
  [ -z "$loop" ] || losetup -d "$loop" 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT INT TERM
truncate -s "$size" "$disk"
loop=$(losetup -b 4096 -fP --show "$disk")
sfdisk -q "$loop" <<EOF
label: gpt
start=256, size=480MiB, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name=LumaBoot
type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name=LumaRoot
EOF
partx -u "$loop" 2>/dev/null || true
mkfs.vfat -F 32 -S 4096 -n LUMABOOT -i "${boot_uuid/-/}" "${loop}p1" >/dev/null
mkfs.ext4 -q -L LumaRoot -U "$root_uuid" "${loop}p2"
mkdir -p "$mnt"
mount "${loop}p2" "$mnt"

ostree admin init-fs "$mnt" >/dev/null
ostree admin os-init --sysroot="$mnt" luma >/dev/null
ostree --repo="$mnt/ostree/repo" pull-local "$repo" "$commit" >/dev/null
ostree --repo="$mnt/ostree/repo" refs --create="luma:$ref" "$commit"
printf '[origin]\nrefspec=luma:%s\n' "$ref" >"$work/origin"
ostree admin deploy --sysroot="$mnt" --os=luma --origin-file="$work/origin" "$commit" >/dev/null
deploys=("$mnt/ostree/deploy/luma/deploy/"*.0)
[ "${#deploys[@]}" = 1 ] || fail "expected one deployment"
deploy=${deploys[0]}
[ -x "$deploy/usr/libexec/luma-device-layer" ] ||
  echo "warning: $commit has no luma-device-layer; device kits will not be applied" >&2

# The aboot slot links the initramfs boots; its OSTree boot resolver keeps them
# on the default deployment from now on.
rel=deploy/luma/deploy/$(basename "$deploy")
ln -sfn "$rel" "$mnt/ostree/root.a"
ln -sfn "$rel" "$mnt/ostree/root.b"

install -d -m 0755 "$deploy/etc/luma"
cat >"$deploy/etc/luma/device-kit.conf" <<EOF
# The device kit (devices/README.md) is on the phone userdata image's LumaBoot
# filesystem; luma-device-layer.service layers it on the first boot.
source=UUID=$boot_uuid
EOF
chmod 0644 "$deploy/etc/luma/device-kit.conf"

# The user. Appending keeps each file's SELinux label.
hash=$(openssl passwd -6 1234)
echo "luma:x:1000:1000:Luma:/home/luma:/bin/bash" >>"$deploy/etc/passwd"
echo "luma:$hash:20000:0:99999:7:::" >>"$deploy/etc/shadow"
echo "luma:x:1000:" >>"$deploy/etc/group"
echo "luma:!::" >>"$deploy/etc/gshadow"
for g in wheel audio luma-display; do
  grep -q "^$g:" "$deploy/etc/group" || continue
  sed -i "/^$g:/{s/:\$/:luma/;t;s/\$/,luma/}" "$deploy/etc/group"
done
var=$mnt/ostree/deploy/luma/var
mkdir -p "$var/home"
cp -a "$deploy/etc/skel" "$var/home/luma"
chown -R 1000:1000 "$var/home/luma"
chmod 0700 "$var/home/luma"
: >"$deploy/etc/systemd/system/boot.automount"
fc=$deploy/etc/selinux/targeted/contexts/files/file_contexts
setfiles -r "$deploy" "$fc" "$deploy/etc" 2>/dev/null || true
setfiles -r "$mnt/ostree/deploy/luma" "$fc" "$var/home" 2>/dev/null || true

# The home directory is the skeleton and nothing else: no device presets.
diff <(cd "$deploy/etc/skel" && find . | sort) <(cd "$var/home/luma" && find . | sort) >/dev/null ||
  fail "/var/home/luma differs from /etc/skel"

{
  echo "commit=$commit"
  echo "version=$version"
  echo "ref=luma:$ref"
  echo "deployment=$(basename "$deploy")"
  echo "root_uuid=$root_uuid"
  echo "boot_uuid=$boot_uuid"
  sed 's/^/origin: /' "$deploy.origin"
} >"$work/manifest"

sync
umount -R "$mnt"
for _ in $(seq 1 60); do
  grep -qs -- "${loop}p2 " /proc/[0-9]*/mountinfo || break
  sleep 1
done
rc=0
e2fsck -fy "${loop}p2" >"$work/fsck.log" 2>&1 || rc=$?
[ "$rc" -le 1 ] || { cat "$work/fsck.log" >&2; fail "e2fsck failed ($rc)"; }
losetup -d "$loop"; loop=""
mkdir -p "$(dirname "$output")"
python3 "$repo_root/scripts/mobile/img2simg.py" "$disk" "$output"
install -m 0644 "$work/manifest" "$output.manifest"
cat "$output.manifest"
