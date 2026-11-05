#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Phone userdata image of the generic ARM commit, optionally with a device's
# repository layered on: the disk the phone's boot image starts. userdata holds
# a GPT with 4096-byte sectors (UFS; the initramfs cannot read a 512-byte
# table): LumaBoot (FAT, empty) and LumaRoot (ext4) holding /ostree and /boot,
# with the UUIDs from the device's device.toml [image]. The initramfs grows
# LumaRoot to fill userdata on the first boot.
#
# usage: build-phone-userdata.sh --repo REPO --ref REF --device VENDOR/CODENAME --output FILE [--device-repo DIR --device-key FILE] [--size SIZE]
#
# REF (luma/1/aarch64/<channel>) is deployed from the signed OSTree repository
# REPO with origin luma:REF; a commit carrying a hardware target is refused.
# With --device-repo, the device's repository (device.toml [device_repo]: DIR, the
# signed rpm-md tree, checked against --device-key) is applied by the
# payload's own rpm-ostree (scripts/devices/rpm-ostree-offline.sh) as two
# client-side transactions: override replace --from repo of the [device_repo]
# replace list, then install of the install list. The generic deployment is
# removed, /ostree/root.a and root.b point at the layered one, and the image
# keeps /etc/yum.repos.d/<id>.repo at the repository's https URL with
# repo_gpgcheck=1, so rpm-ostree upgrades keep the layer.
#
# One user, luma, password 1234. /etc/systemd/system/boot.automount is masked
# (systemd-gpt-auto's /boot automount breaks shutdown on these phones).
# Writes FILE (Android sparse, zero blocks as FILL so a reflash overwrites
# everything; scripts/mobile/img2simg.py) and FILE.manifest (origin, commit, packages).
# Runs as root on Linux; on a host that is not aarch64, rpm-ostree runs under
# qemu-user binfmt (see rpm-ostree-offline.sh). Work files go to TMPDIR.
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
fail() { printf 'build-phone-userdata: %s\n' "$*" >&2; exit 1; }
repo="" ref="" output="" device="" device_repo="" device_key="" size=10G
while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo) repo=$(realpath -e "${2:?}"); shift 2 ;;
    --ref) ref=${2:?}; shift 2 ;;
    --output) output=${2:?}; shift 2 ;;
    --device) device=${2:?}; shift 2 ;;
    --device-repo) device_repo=$(realpath -e "${2:?}"); shift 2 ;;
    --device-key) device_key=$(realpath -e "${2:?}"); shift 2 ;;
    --size) size=${2:?}; shift 2 ;;
    *) sed -n 's/^# usage: /usage: /p' "$0" >&2; exit 2 ;;
  esac
done
[ -n "$repo" ] && [ -n "$ref" ] && [ -n "$output" ] || fail "missing arguments"
[[ "$ref" =~ ^luma/[0-9]+/aarch64/[a-z0-9-]+$ ]] || fail "$ref is not a generic ARM channel ref"
[ "$(id -u)" = 0 ] || fail "run as root"
[ -n "$device" ] || fail "--device is required (device.toml [image] names the filesystems)"
toml="$repo_root/devices/$device/device.toml"
[ -f "$toml" ] || fail "no $toml"
eval "$(python3 - "$toml" <<'PY'
import shlex, sys, tomllib
d = tomllib.load(open(sys.argv[1], "rb"))
r = d.get("device_repo", {})
print("root_uuid=" + shlex.quote(d["image"]["root_uuid"]))
print("boot_uuid=" + shlex.quote(d["image"]["boot_uuid"]))
print("repo_id=" + shlex.quote(r.get("id", "")))
print("repo_url=" + shlex.quote(r.get("url", "")))
print("replace=(" + " ".join(shlex.quote(n) for n in r.get("replace", [])) + ")")
print("install=(" + " ".join(shlex.quote(n) for n in r.get("install", [])) + ")")
PY
)"
if [ -n "$device_repo" ]; then
  [ -n "$repo_id" ] && [ -n "$device_key" ] || fail "--device-repo needs device.toml [device_repo] and --device-key"
  [ -f "$device_repo/repodata/repomd.xml" ] && [ -f "$device_repo/repodata/repomd.xml.asc" ] ||
    fail "$device_repo is not a signed rpm-md repository"
fi

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

if [ -n "$device_repo" ]; then
  key_path=/etc/pki/rpm-gpg/RPM-GPG-KEY-luma-os-release
  repo_file=/etc/yum.repos.d/$repo_id.repo
  names=$(find "$device_repo/Packages" -name '*.rpm' -exec rpm -qp --qf '%{NAME}\n' {} + 2>/dev/null | sort -u | paste -sd,)
  write_repo() {  # DEPLOYMENT BASEURL REPO_GPGCHECK
    {
      echo "# $device device packages, layered on the Luma OS with rpm-ostree"
      echo "# (rpm-ostree status lists them). Upgrades keep the layer."
      echo "[$repo_id]"
      echo "name=Luma $device"
      echo "baseurl=$2"
      echo "enabled=1"
      echo "gpgcheck=1"
      echo "repo_gpgcheck=$3"
      echo "gpgkey=file://$key_path"
      echo "includepkgs=$names"
    } >"$1$repo_file"
    chmod 0644 "$1$repo_file"
  }
  # rpm-ostree cannot check repomd.xml.asc on an unbooted sysroot, so the
  # host's gpg checks it here and the build-time file sets repo_gpgcheck=0;
  # package signatures are checked by rpm-ostree. The image keeps
  # repo_gpgcheck=1. Offline: only the device repository is configured.
  gpg_home=$(mktemp -d "$work/gpg.XXXXXX")
  gpg --homedir "$gpg_home" --batch --quiet --import "$device_key" 2>/dev/null
  gpg --homedir "$gpg_home" --batch --quiet --verify "$device_repo/repodata/repomd.xml.asc" \
    "$device_repo/repodata/repomd.xml" 2>/dev/null || fail "repomd.xml.asc does not verify with --device-key"
  gpgconf --homedir "$gpg_home" --kill all 2>/dev/null || true
  mkdir "$work/repos-aside"
  find "$deploy/etc/yum.repos.d" -maxdepth 1 -name '*.repo' -exec mv -t "$work/repos-aside" {} +
  install -D -m 0644 "$device_key" "$deploy$key_path"
  write_repo "$deploy" file:///run/luma-device-repo 0
  run() {  # rpm-ostree on the current deployment; leaves only the new one
    local before=$deploy
    TMPDIR="$work" bash "$repo_root/scripts/devices/rpm-ostree-offline.sh" --sysroot "$mnt" --stateroot luma \
      --deployment "$deploy" --bind "$device_repo:/run/luma-device-repo" -- "$@"
    deploys=("$mnt/ostree/deploy/luma/deploy/"*.0)
    [ "${#deploys[@]}" = 2 ] || fail "rpm-ostree did not write a deployment for: $*"
    ostree admin undeploy --sysroot="$mnt" 1 >/dev/null
    deploys=("$mnt/ostree/deploy/luma/deploy/"*.0)
    [ "${#deploys[@]}" = 1 ] && [ "${deploys[0]}" != "$before" ] || fail "expected only the new deployment"
    deploy=${deploys[0]}
  }
  [ "${#replace[@]}" = 0 ] || run override replace --experimental --from "repo=$repo_id" "${replace[@]}"
  [ "${#install[@]}" = 0 ] || run install "${install[@]}"
  rm -f "$deploy$repo_file"
  cp -a "$work/repos-aside/." "$deploy/etc/yum.repos.d/"
  write_repo "$deploy" "$repo_url" 1
  origin=$deploy.origin
  grep -Fxq "baserefspec=luma:$ref" "$origin" || fail "the layered origin is not based on luma:$ref"
  for name in "${install[@]}"; do
    grep -Eq "^requested=(.*;)?$name;" "$origin" || fail "the origin does not request $name"
  done
  for name in "${replace[@]}"; do
    grep -q "$name" "$origin" || fail "the origin does not replace $name from $repo_id"
  done
  for name in "${replace[@]}" "${install[@]}"; do
    rpm --dbpath "$deploy/usr/share/rpm" -q --qf '%{NAME}-%{VERSION}-%{RELEASE}\n' "$name" >/dev/null ||
      fail "$name is not in the layered deployment"
  done
fi
grep -Eq "^(base)?refspec=luma:$ref\$" "$deploy.origin" || fail "the deployment does not follow luma:$ref"

# The aboot slot links the initramfs boots; its OSTree boot resolver keeps them
# on the default deployment from now on.
rel=deploy/luma/deploy/$(basename "$deploy")
ln -sfn "$rel" "$mnt/ostree/root.a"
ln -sfn "$rel" "$mnt/ostree/root.b"

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

{
  echo "commit=$commit"
  echo "version=$version"
  echo "ref=luma:$ref"
  echo "deployment=$(basename "$deploy")"
  echo "root_uuid=$root_uuid"
  echo "boot_uuid=$boot_uuid"
  sed 's/^/origin: /' "$deploy.origin"
  if [ -n "$device_repo" ]; then
    rpm --dbpath "$deploy/usr/share/rpm" -q --qf '%{NAME}-%{EPOCH}:%{VERSION}-%{RELEASE}.%{ARCH}\n' \
      "${replace[@]}" "${install[@]}" | sed 's/(none)/0/; s/^/device: /'
  fi
} >"$work/manifest"

sync
umount -R "$mnt"
e2fsck -fy "${loop}p2" >/dev/null || [ $? -le 1 ] || fail "e2fsck failed"
losetup -d "$loop"; loop=""
mkdir -p "$(dirname "$output")"
python3 "$repo_root/scripts/mobile/img2simg.py" "$disk" "$output"
install -m 0644 "$work/manifest" "$output.manifest"
cat "$output.manifest"
