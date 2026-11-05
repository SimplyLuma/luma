#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build the Unihertz Titan 2 Elite microSD card image
# (luma-titan2e-<channel>-<build>-aarch64.img.zst) from the generic Luma ARM
# commit plus the Titan device repository. The phone's boot_a holds the Titan
# kernel (built-in device tree and initramfs); the initramfs mounts partition 1
# of the card and boots ostree's default deployment from /boot/loader/entries,
# with greenboot's boot counting (the shared phone resolver,
# scripts/mobile/common/initramfs/luma-ostree-boot.sh).
#
#   GPT, 512-byte sectors
#   p1  ext4 "LumaRoot" from 1 MiB to the end: the OSTree physical root with
#       /boot as a directory; grows to the card on the first boot
#
# The deployment follows the generic ARM ref (luma/<major>/aarch64/<channel>:
# one OS commit for every ARM device). The Titan's kernel modules and platform
# services are a client-side rpm-ostree layer from the device repository
# (rpm-md, packages and repomd.xml signed with the Luma OS release key), made
# now by the payload's own rpm-ostree (scripts/devices/rpm-ostree-offline.sh)
# so the first boot needs no network. The card keeps that repository
# (/etc/yum.repos.d/luma-unihertz-titan2e.repo at --device-repo-url, only the
# device packages, excluding every Luma-owned name) and the armored release
# key, so every rpm-ostree update reapplies the layer on the new commit.
#
# usage: build-titan2e-sd-image.sh --payload REPO REF --key FILE --device-repo DIR --output DIR [--device-repo-url URL] [--device-package NAME]... [--size GIB] [--no-compress] [--release]
#
# --payload is the signed OSTree repository (archive, local path or http(s)
# URL) carrying REF, the generic ARM channel ref; the commit must be aarch64,
# on REF's channel and name no hardware target. --key is the Luma OS release
# key, armored: it verifies the commit and the device repository and is the
# key the card keeps. --device-package adds packages to the layer besides
# titan2e-kernel-modules and luma-titan2e-platform.
#
# First boot: no account exists and root is locked; the builder marks the
# deployment for the image's first-boot setup (/etc/luma/first-boot/pending)
# and sets the handheld device class (/etc/luma-device-class).
#
# --release refuses a card that gives root access (root password, root SSH
# keys) or follows a hardware-targeted stream.
#
# Runs as root on Linux (loop devices). On a host that is not aarch64 the
# layer's rpm-ostree runs under qemu-user (see rpm-ostree-offline.sh); build on
# an aarch64 host where possible. The Titan kernel has no SELinux, so no labels
# are applied beyond what ostree writes.
set -euo pipefail
export TZ=UTC LC_ALL=C

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
usage() { sed -n 's/^# usage: /usage: /p' "$0" >&2; exit 2; }

payload="" ref="" key="" device_repo="" output="" size_gib=12 compress=1 release=0
device_repo_url=https://dl.simplyluma.com/os/devices/aarch64/unihertz-titan2e/
device_packages=(titan2e-kernel-modules luma-titan2e-platform)
while [ "$#" -gt 0 ]; do
  case "$1" in
    --payload) payload=${2:?}; ref=${3:?}; shift 3 ;;
    --key) key=$(realpath -e "${2:?}"); shift 2 ;;
    --device-repo) device_repo=$(realpath -e "${2:?}"); shift 2 ;;
    --device-repo-url) device_repo_url=${2:?}; shift 2 ;;
    --device-package) device_packages+=("${2:?}"); shift 2 ;;
    --output) output=${2:?}; shift 2 ;;
    --size) size_gib=${2:?}; shift 2 ;;
    --no-compress) compress=0; shift ;;
    --release) release=1; shift ;;
    *) usage ;;
  esac
done
[ -n "$payload" ] && [ -n "$key" ] && [ -n "$device_repo" ] && [ -n "$output" ] || usage
[[ "$ref" =~ ^luma/[0-9]+/aarch64/(stable|beta|nightly)$ ]] || fail "--payload ref must be a generic ARM channel ref, not $ref"
channel=${ref##*/}
grep -q 'BEGIN PGP PUBLIC KEY BLOCK' "$key" || fail "--key is not an armored public key"
[ -f "$device_repo/repodata/repomd.xml" ] && [ -f "$device_repo/repodata/repomd.xml.asc" ] ||
  fail "--device-repo is not a signed rpm-md repository (repodata/repomd.xml and repomd.xml.asc)"
[[ "$device_repo_url" =~ ^https://[^[:space:]]+/$ ]] || fail "--device-repo-url must be an https:// URL ending in /"
[[ "$size_gib" =~ ^[0-9]+$ ]] && [ "$size_gib" -ge 10 ] || fail "--size must be at least 10 (GiB)"
[ "$(uname -s)" = Linux ] || fail "build the card image on Linux"
[ "$(id -u)" = 0 ] || fail "run as root: the image is assembled on a loop device"
for tool in blkid e2fsck gpg losetup mkfs.ext4 mount ostree python3 sfdisk sha256sum systemctl; do
  command -v "$tool" >/dev/null 2>&1 || fail "required tool is missing: $tool"
done
[ "$compress" = 0 ] || command -v zstd >/dev/null 2>&1 || fail "required tool is missing: zstd"
case "$payload" in http://*|https://*) payload_url=$payload ;; *) payload_url=file://$(realpath -e "$payload") ;; esac

mkdir -p "$output"
output=$(realpath -e "$output")
work=$(mktemp -d "$output/.titan2e-card.XXXXXX")
loop=""
cleanup() {
  set +e
  mountpoint -q "$work/sysroot" 2>/dev/null && umount "$work/sysroot"
  [ -n "$loop" ] && losetup -d "$loop"
  rm -rf "$work"
}
trap cleanup EXIT INT TERM

# The release key as ostree and gpg read it.
gpg_home=$(mktemp -d "$work/gpg.XXXXXX")
gpg --homedir "$gpg_home" --batch --quiet --import "$key" 2>/dev/null
fpr=$(gpg --homedir "$gpg_home" --batch --with-colons --list-keys | awk -F: '$1 == "fpr" {print $10; exit}')
[ -n "$fpr" ] || fail "--key holds no key"
gpg --homedir "$gpg_home" --batch --quiet --verify "$device_repo/repodata/repomd.xml.asc" \
  "$device_repo/repodata/repomd.xml" 2>/dev/null || fail "the device repository's repomd.xml.asc does not verify with --key"
gpg --homedir "$gpg_home" --batch --export "$fpr" >"$work/release.gpg"
gpgconf --homedir "$gpg_home" --kill all 2>/dev/null || true

# The disk.
img_partial="$work/card.img"
truncate -s "${size_gib}G" "$img_partial"
sfdisk --quiet "$img_partial" <<EOF
label: gpt
unit: sectors
start=2048, name=LumaRoot, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4
EOF
loop=$(losetup --find --show --partscan "$img_partial")
udevadm settle 2>/dev/null || true
[ -b "${loop}p1" ] || fail "${loop}p1 did not appear"

sysroot="$work/sysroot"
mkdir "$sysroot"
# The payload first, into a scratch repository, to name the image and derive
# the filesystem identity from the commit.
ostree init --repo="$work/meta" --mode=bare-user-only
ostree remote add --repo="$work/meta" --set=gpg-verify=true --set=gpg-verify-summary=false \
  --set=gpgkeypath="$work/release.gpg" luma "$payload_url"
ostree pull --repo="$work/meta" --commit-metadata-only luma "$ref" >/dev/null
commit=$(ostree --repo="$work/meta" rev-parse "luma:$ref")
meta() { ostree --repo="$work/meta" show --print-metadata-key="$1" "$commit" 2>/dev/null | tr -d "'" || true; }
[ "$(meta org.projectluma.architecture)" = aarch64 ] || fail "payload commit is not aarch64"
[ "$(meta org.projectluma.channel)" = "$channel" ] || fail "payload commit is not on the $channel channel"
[ -z "$(meta org.projectluma.hardware-target)" ] || fail "payload commit is built for hardware target $(meta org.projectluma.hardware-target)"
build_id=$(meta org.projectluma.build-id)
version=$(meta version)
[[ "$build_id" =~ ^[0-9]{8}\.[0-9]+$ ]] || fail "payload build id is not a nightly id: $build_id"
image_name="luma-titan2e-$channel-$build_id-aarch64"
echo "Payload: luma:$ref $commit ($version)"
root_uuid=$(python3 -c 'import sys, uuid; print(uuid.uuid5(uuid.uuid5(uuid.NAMESPACE_URL, "luma-titan2e:" + sys.argv[1]), "root"))' "$commit")
mkfs.ext4 -q -L LumaRoot -U "$root_uuid" -E "hash_seed=$root_uuid" "${loop}p1"
mount "${loop}p1" "$sysroot"

# The OSTree deployment, as the installer makes it: the commit pulled with its
# signature checked, one deployment with origin luma:REF. The remote is then
# defined only by the image's own /etc/ostree/remotes.d.
ostree admin init-fs --sysroot="$sysroot" "$sysroot" >/dev/null
repo="$sysroot/ostree/repo"
ostree config --repo="$repo" set sysroot.bootloader none
ostree config --repo="$repo" set sysroot.readonly true
ostree admin os-init --sysroot="$sysroot" luma >/dev/null
ostree remote add --repo="$repo" --set=gpg-verify=true --set=gpg-verify-summary=false \
  --set=gpgkeypath="$work/release.gpg" luma "$payload_url"
ostree pull --repo="$repo" luma "$ref" >/dev/null
[ "$(ostree --repo="$repo" rev-parse "luma:$ref")" = "$commit" ] || fail "pulled commit differs from $commit"
# Kernel arguments: the release's (/usr/lib/bootc/kargs.d), for the record. The
# Titan kernel takes its command line from its built-in device tree; the
# initramfs adds only ostree=.
kargs=(rw)
for karg in $(ostree --repo="$repo" cat "$commit" /usr/lib/bootc/kargs.d/10-luma-graphical-boot.toml 2>/dev/null |
              sed -n 's/^kargs *= *\[\(.*\)\]/\1/p' | tr -d '",'); do
  kargs+=("$karg")
done
ostree admin deploy --sysroot="$sysroot" --os=luma --no-merge "${kargs[@]/#/--karg=}" "luma:$ref" >/dev/null
ostree remote delete --repo="$repo" luma
deploys=("$sysroot/ostree/deploy/luma/deploy/"*.0)
[ "${#deploys[@]}" = 1 ] && [ -d "${deploys[0]}" ] || fail "expected one deployment"
generic=${deploys[0]}
grep -Fxq "refspec=luma:$ref" "$generic.origin" || fail "the deployment's origin is not luma:$ref"

# The Titan layer (see the header). rpm-ostree cannot check repomd.xml.asc on
# an unbooted sysroot, so the build checked it above and the build-time
# repository file sets repo_gpgcheck=0; package signatures are checked.
key_path=/etc/pki/rpm-gpg/RPM-GPG-KEY-luma-os-release
repo_file=/etc/yum.repos.d/luma-unihertz-titan2e.repo
write_device_repo() {  # DEPLOYMENT BASEURL REPO_GPGCHECK
  local excludes=""
  [ ! -s "$1/usr/share/luma/luma-owned-packages.txt" ] || excludes=$(paste -sd, "$1/usr/share/luma/luma-owned-packages.txt")
  {
    echo "# Unihertz Titan 2 Elite device packages: the kernel modules and platform"
    echo "# services this phone layers on the Luma OS (rpm-ostree status shows them)."
    echo "[luma-unihertz-titan2e]"
    echo "name=Luma Unihertz Titan 2 Elite"
    echo "baseurl=$2"
    echo "enabled=1"
    echo "gpgcheck=1"
    echo "repo_gpgcheck=$3"
    echo "gpgkey=file://$key_path"
    echo "includepkgs=$(IFS=,; echo "${device_packages[*]},lpac-titan2e,luma-esim-broker")"
    [ -z "$excludes" ] || echo "# Luma: packages the Luma image owns never come from this repository"
    [ -z "$excludes" ] || echo "excludepkgs=$excludes"
  } >"$1$repo_file"
  chmod 0644 "$1$repo_file"
}
mkdir "$work/repos-aside"
find "$generic/etc/yum.repos.d" -maxdepth 1 -name '*.repo' -exec mv -t "$work/repos-aside" {} +
install -D -m 0644 "$key" "$generic$key_path"
write_device_repo "$generic" file:///run/luma-device-repo 0
echo "Layer: install ${device_packages[*]}"
TMPDIR="$work" bash "$repo_root/scripts/devices/rpm-ostree-offline.sh" --sysroot "$sysroot" --stateroot luma \
  --deployment "$generic" --bind "$device_repo:/run/luma-device-repo" -- install "${device_packages[@]}"
deploys=("$sysroot/ostree/deploy/luma/deploy/"*.0)
[ "${#deploys[@]}" = 2 ] || fail "rpm-ostree did not write the layered deployment"
ostree admin undeploy --sysroot="$sysroot" 1 >/dev/null
deploys=("$sysroot/ostree/deploy/luma/deploy/"*.0)
[ "${#deploys[@]}" = 1 ] && [ "${deploys[0]}" != "$generic" ] || fail "expected only the layered deployment"
deploy=${deploys[0]}
rm -f "$deploy$repo_file"
cp -a "$work/repos-aside/." "$deploy/etc/yum.repos.d/"
write_device_repo "$deploy" "$device_repo_url" 1
origin=$deploy.origin
grep -Fxq "baserefspec=luma:$ref" "$origin" || fail "the layer's origin is not based on luma:$ref"
layered=$(sed -n 's/^requested=//p' "$origin")
for name in "${device_packages[@]}"; do
  [[ ";$layered" == *";$name;"* ]] || fail "the layer does not request $name"
done
krel=$(find "$deploy/usr/lib/modules" -mindepth 1 -maxdepth 1 -name '*-luma-titan2e-*' -printf '%f\n')
[ -n "$krel" ] && [ "$(printf '%s\n' "$krel" | wc -l)" = 1 ] || fail "the layer has no single Titan module tree: $krel"
[ -s "$deploy/usr/lib/modules/$krel/modules.dep" ] || fail "the Titan module tree has no modules.dep"
echo "Titan modules: $krel ($(find "$deploy/usr/lib/modules/$krel" -name '*.ko*' | wc -l))"

# Platform units by their preset (a layer's scriptlets may not have applied it),
# the health check the boot marks wait for, and greenboot's rollback trigger,
# which arms the boot counter the initramfs follows.
systemctl --root="$deploy" preset $(cd "$deploy/usr/lib/systemd/system" && ls luma-titan2e-*.service) \
  greenboot-healthcheck.service greenboot-set-rollback-trigger.service >/dev/null 2>&1 || true
for unit in "$deploy"/usr/lib/systemd/system/luma-titan2e-*.service; do
  grep -Eq '^WantedBy=(multi-user|greenboot-success)\.target' "$unit" || continue
  systemctl --root="$deploy" -q is-enabled "${unit##*/}" || fail "${unit##*/} is not enabled"
done
for unit in greenboot-healthcheck.service greenboot-set-rollback-trigger.service; do
  systemctl --root="$deploy" -q is-enabled "$unit" || fail "$unit is not enabled"
done

# The Titan follows the generic ARM stream: no hardware-targeted update.conf.
if [ -e "$deploy/etc/luma/update.conf" ] &&
   grep -Eq '^\s*(hardware_target\s*=\s*\S|ref_template\s*=.*titan2e)' "$deploy/etc/luma/update.conf"; then
  fail "the deployment's /etc/luma/update.conf names a hardware target (luma-titan2e-platform before luma.58?)"
fi
grep -Eq "^(base)?refspec=luma:$ref\$" "$origin" || fail "the deployment does not follow luma:$ref"

# The update path: the image's own remote definition and mirror list; the
# release key where the remote reads it; the channel for luma-update.
remote_conf="$deploy/etc/ostree/remotes.d/luma.conf"
[ -f "$remote_conf" ] || fail "the image does not define the luma remote"
grep -Exq 'gpg-verify=(true|1|yes)' "$remote_conf" || fail "$remote_conf does not turn gpg-verify on"
gpgkeypath=$(sed -n 's/^gpgkeypath=//p' "$remote_conf")
[ -n "$gpgkeypath" ] || fail "$remote_conf names no gpgkeypath"
[ -f "$deploy$gpgkeypath" ] || install -D -m 0644 "$work/release.gpg" "$deploy$gpgkeypath"
install -d -m 0755 "$deploy/etc/luma"
printf 'channel=%s\n' "$channel" >"$deploy/etc/luma/update-channel.conf"

# /boot is a directory in the physical root; rpm-ostree needs it at /boot.
printf '%s\n' '/sysroot/boot /boot none bind 0 0' >"$deploy/etc/fstab"
var="$sysroot/ostree/deploy/luma/var"
install -d -m 0700 "$var/roothome"
cp -a "$deploy"/etc/skel/.bash* "$var/roothome/" 2>/dev/null || true
# A phone: the shell's handheld policy, and the image's first-boot setup.
printf 'handheld\n' >"$deploy/etc/luma-device-class"
install -d -m 0755 "$deploy/etc/luma/first-boot"
epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo_root" log -1 --format=%ct HEAD 2>/dev/null || date +%s)}
printf 'origin=card\ndevice=unihertz,titan2e\ncreated=%s\n' "$epoch" >"$deploy/etc/luma/first-boot/pending"
if [ -e "$deploy/usr/libexec/luma-setup" ]; then first_boot=luma-setup
else first_boot="none (no first-boot setup in the payload)"; echo "warning: $first_boot" >&2; fi

if [ "$release" = 1 ]; then
  [ ! -e "$var/roothome/.ssh" ] || fail "release card has /root/.ssh"
  root_hash=$(awk -F: '$1 == "root" {print $2}' "$deploy/etc/shadow" 2>/dev/null || true)
  case "$root_hash" in '!'*|'*'*|'') ;; *) fail "release card has a root password" ;; esac
fi
used_mib=$(df -BM --output=used "$sysroot" | tail -n 1 | tr -dc 0-9)
packages=$(rpm --dbpath "$deploy/usr/share/rpm" -q "${device_packages[@]}" | tr '\n' ' ')
sync
umount "$sysroot"
e2fsck -fn "${loop}p1" >/dev/null || fail "the root filesystem failed e2fsck"
losetup -d "$loop"
loop=""

mv "$img_partial" "$output/$image_name.img"
image_bytes=$(stat -c %s "$output/$image_name.img")
image_sha256=$(sha256sum "$output/$image_name.img" | cut -d' ' -f1)
artifact="$image_name.img"
if [ "$compress" = 1 ]; then
  zstd -q -T0 -19 --rm -f "$output/$image_name.img" -o "$output/$image_name.img.zst"
  artifact="$image_name.img.zst"
fi
artifact_sha256=$(sha256sum "$output/$artifact" | cut -d' ' -f1)
printf '%s  %s\n' "$artifact_sha256" "$artifact" >"$output/$artifact.sha256"
{
  echo "image $image_name.img"
  echo "image_bytes $image_bytes"
  echo "image_sha256 $image_sha256"
  echo "artifact $artifact $(stat -c %s "$output/$artifact") $artifact_sha256"
  echo "layout gpt p1=2048+rest(ext4,LumaRoot,$root_uuid)"
  echo "root_used_mib $used_mib"
  echo "origin luma:$ref"
  echo "commit $commit"
  echo "version $version"
  echo "channel $channel"
  echo "titan_kernel $krel"
  echo "layered $layered"
  echo "packages $packages"
  echo "device_repo $device_repo_url ($device_repo, key $fpr)"
  echo "kargs ${kargs[*]}"
  echo "first_boot $first_boot"
  [ "$release" = 0 ] || echo "release yes (no root password, no root SSH)"
} >"$output/$image_name.manifest.txt"
echo "Card image: $output/$artifact"
echo "  raw $image_bytes bytes, sha256 $image_sha256; root uses $used_mib MiB"
