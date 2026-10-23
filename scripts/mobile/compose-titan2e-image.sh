#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Compose the Unihertz Titan 2 Elite microSD image as an OSTree deployment.
#   compose-titan2e-image.sh RPM_DIR KERNEL_DIR OUTPUT_DIR
# RPM_DIR     Luma RPMs (greeter, phosh, platform, Mesa, kernel modules, ...);
#             Fedora 44 packages come from the builder's own repositories.
# KERNEL_DIR  build-titan2e-kernel.sh output: boot_a.img, Image.lz4,
#             kernel.release and the titan2e-connsys / titan2e-ccci module trees.
# OUTPUT_DIR  new directory: the OSTree repo, luma-titan2e.img (GPT, partition 1
#             ext4 with the OSTree sysroot), luma-titan2e-boot.img and manifests.
#   LUMA_TITAN2E_BUILD_ID      build id (YYYYMMDD.N, default today's .1)
#   LUMA_TITAN2E_KERNEL_SOURCE the kernel's titan2e-kernel-source.json (tool pins,
#                              boot partition size; default: this checkout's)
#   LUMA_TITAN2E_AVB_KEY_SHA1  public key sha1 boot_a must be signed with
#                              (default: the AOSP test key the kernel build uses)
# Runs as root in a Fedora 44 AArch64 userspace. The kernel's built-in
# initramfs picks the deployment from ostree's boot entries and runs its
# ostree-prepare-root.
set -euo pipefail
repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo_root/scripts/mobile/lib/handheld-rootfs.sh"
rpm_dir=$(realpath "${1:?RPM_DIR}")
kernel_dir=$(realpath "${2:?KERNEL_DIR}")
out=${3:?OUTPUT_DIR}
variant=release
ref=${LUMA_TITAN2E_REF:-luma/1/aarch64/unihertz-titan2e/nightly}
image_size=${LUMA_TITAN2E_IMAGE_SIZE:-8G}
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die 'run as root'
[ "$(uname -m)" = aarch64 ] || die 'compose in an AArch64 userspace'
[ ! -e "$out" ] || die "existing output must be retained: $out"
krel=$(cat "$kernel_dir/kernel.release")
for f in boot_a.img Image.lz4 titan2e-connsys titan2e-ccci; do [ -e "$kernel_dir/$f" ] || die "missing $kernel_dir/$f"; done
for tool in dnf5 createrepo_c rpm-ostree ostree mkfs.ext4 sfdisk lz4 dconf depmod; do
	command -v "$tool" >/dev/null || die "missing $tool"
done

mkdir -p "$out"
work=$(mktemp -d "$out/work.XXXXXX")
build_id=${LUMA_TITAN2E_BUILD_ID:-$(date -u +%Y%m%d).1}
[[ "$build_id" =~ ^[0-9]{8}\.[0-9]+$ ]] || die "invalid build id: $build_id"

# boot_a: the phone's vbmeta chains boot, so an image without the AVB hash
# footer, or signed with another key, bootloops it. Check before anything else.
kernel_source=${LUMA_TITAN2E_KERNEL_SOURCE:-$repo_root/config/mobile/titan2e-kernel-source.json}
[ -f "$kernel_source" ] || die "missing $kernel_source (set LUMA_TITAN2E_KERNEL_SOURCE)"
eval "$(python3 - "$kernel_source" <<'PY'
import json, shlex, sys
c = json.load(open(sys.argv[1]))
t = c['tools']['avbtool']
for k in ('url', 'commit', 'path', 'sha256'):
    print(f'avbtool_{k}={shlex.quote(t[k])}')
print(f"boot_partition_size={c['boot_partition_size']}")
PY
)"
curl -fsSL "$avbtool_url/+/$avbtool_commit/$avbtool_path?format=TEXT" | base64 -d >"$work/avbtool.py"
[ "$(sha256sum "$work/avbtool.py" | cut -d' ' -f1)" = "$avbtool_sha256" ] || die 'avbtool checksum differs'
avb_key_sha1=${LUMA_TITAN2E_AVB_KEY_SHA1:-cdbb77177f731920bbe0a0f94f84d9038ae0617d}
info=$(python3 "$work/avbtool.py" info_image --image "$kernel_dir/boot_a.img") || die 'boot_a.img has no AVB footer'
avb_field() { printf '%s\n' "$info" | sed -n "s/^ *$1: *//p" | head -n 1; }
[ "$(avb_field 'Image size')" = "$boot_partition_size bytes" ] || die 'boot_a.img is not the size of the boot partition'
[ "$(avb_field 'Algorithm')" = SHA256_RSA2048 ] || die 'boot_a.img is not signed SHA256_RSA2048'
[ "$(avb_field 'Public key (sha1)')" = "$avb_key_sha1" ] || die 'boot_a.img is signed with another key'
[ "$(avb_field 'Partition Name')" = boot ] || die 'boot_a.img hash descriptor is not for boot'
# verify_image looks up each hash descriptor's partition as <name>.img beside it.
mkdir -p "$work/avb" && cp "$kernel_dir/boot_a.img" "$work/avb/boot.img"
python3 "$work/avbtool.py" verify_image --image "$work/avb/boot.img" >/dev/null || die 'boot_a.img AVB signature does not verify'
rm -rf "$work/avb"
rootfs=$work/rootfs
# Payload modes must not leak from a package's build tree (setgid/0750 dirs).
"$repo_root/scripts/packages/check-rpm-modes.sh" "$rpm_dir"/*.rpm || die 'an RPM carries leaked file modes'
createrepo_c -q "$rpm_dir"

# Pinned Fedora builds (FP6 Alpha 1 baseline + the Titan's additions), checked
# against their sha256; LUMA_TITAN2E_BASELINE_CACHE may hold earlier downloads.
baseline=$work/baseline
mkdir -p "$baseline"
python3 - "$baseline" "${LUMA_TITAN2E_BASELINE_CACHE:-}" \
	"$repo_root/config/mobile/fedora-alpha1-baseline.json" \
	"$repo_root/config/mobile/titan2e/fedora-baseline.json" <<'PY'
import hashlib, json, os, shutil, sys, urllib.request
out, cache, *manifests = sys.argv[1:]
for manifest in manifests:
    for name, e in json.load(open(manifest))['runtime'].items():
        url = e.get('url') or e['official_signed_build_archive']
        dest = os.path.join(out, name)
        cached = os.path.join(cache, name) if cache else ''
        if cached and os.path.isfile(cached):
            shutil.copyfile(cached, dest)
        else:
            urllib.request.urlretrieve(url, dest)
        digest = hashlib.sha256(open(dest, 'rb').read()).hexdigest()
        if digest != e['sha256']:
            sys.exit(f'checksum differs: {name}')
PY
createrepo_c -q "$baseline"

# Packages: the Titan list, Luma RPMs preferred over Fedora's.
mapfile -t packages < <(grep -v '^#' "$repo_root/config/mobile/titan2e/packages.txt" | grep .)
dnf5 -y --installroot="$rootfs" --releasever=44 --use-host-config \
	--setopt=install_weak_deps=False \
	--repofrompath="luma-titan2e,file://$rpm_dir" \
	--setopt=luma-titan2e.gpgcheck=0 --setopt=luma-titan2e.priority=1 \
	--repofrompath="luma-baseline,file://$baseline" \
	--setopt=luma-baseline.gpgcheck=0 --setopt=luma-baseline.priority=1 \
	install "${packages[@]}"
rpm --root "$rootfs" -q luma-titan2e-platform luma-phosh luma-greeter ostree luma-update \
	titan2e-kernel-modules luma-keyring greenboot nss-altfiles e2fsprogs xdg-desktop-portal-gtk NetworkManager-wwan gtk4-layer-shell >/dev/null || die 'rootfs lacks the Titan core packages'
# Shared handheld pins (call audio) stay identical to the FP6 image.
. "$repo_root/config/mobile/alpha1-package-inputs.env"
rpm --root "$rootfs" -q "$LUMA_CALLAUDIOD_AARCH64_NEVRA" "$FP6_ALPHA_ESIM_BROKER_NEVRA" >/dev/null || die 'rootfs lacks the pinned shared call audio or eSIM broker'
# The handheld pins shared with the FP6 image: shell, browser, LumaUI, apps.
desktop_pins=$(. "$repo_root/config/desktop/inputs.env" && printf '%s %s %s %s %s' \
	"$LUMA_DEVELOPER_PLATFORM_AARCH64_NEVRA" "$LUMA_DEVELOPER_PLATFORM_SDK_NEVRA" "$PRAIRIE_CORE_APPS_NEVRA" \
	"gnome-control-center-50.4-${GNOME_CONTROL_CENTER_LUMA_RELEASE}.fc44.aarch64" \
	"gnome-control-center-filesystem-50.4-${GNOME_CONTROL_CENTER_LUMA_RELEASE}.fc44.noarch")
for nevra in "$LUMA_PHOSH_AARCH64_NEVRA" "$VIOLA_BROWSER_AARCH64_NEVRA" $desktop_pins; do
	rpm --root "$rootfs" -q "$nevra" >/dev/null || die "rootfs lacks the pinned $nevra"
done
for p in luma-fp6-platform qbootctl aboot-deploy aboot-update; do
	! rpm --root "$rootfs" -q "$p" >/dev/null 2>&1 || die "Qualcomm boot package in the image: $p"
done

# Kernel: the boot_a kernel image for OSTree's boot checksum, and the
# out-of-tree Wi-Fi/Bluetooth and modem modules.
mod=$rootfs/usr/lib/modules/$krel
[ -d "$mod" ] || die "titan2e-kernel-modules does not provide $krel"
lz4 -d -q "$kernel_dir/Image.lz4" "$mod/vmlinuz"
for set in titan2e-connsys titan2e-ccci; do
	cp -a "$kernel_dir/$set/usr/lib/modules/$krel/extra" "$mod/"
done
depmod -b "$rootfs" "$krel"

# The account and session the FP6 Alpha 1 image ships: luma (UID 1000) locked
# here, its credential set on the deployment below; greetd starts the phone
# session directly (no first-user wizard).
grep -q '^luma:' "$rootfs/etc/group" || groupadd --root "$rootfs" --gid 1000 luma
# video and render as well as the seat's uaccess: the phone session needs the
# DRM and backlight devices after the greeter hands over.
useradd --root "$rootfs" --uid 1000 --gid luma --groups wheel,luma-display,audio,video,render \
	--no-create-home --shell /bin/bash --comment 'Project Luma local owner' luma
install -d -m 0700 -o 1000 -g 1000 "$rootfs/home/luma"
usermod --root "$rootfs" --password '!' luma
normal_greetd=$repo_root/config/mobile/fp6-physical/overlay/etc/greetd/config.toml
install -D -m 0644 "$normal_greetd" "$rootfs/etc/greetd/config.toml"
# Accounts live in /usr/lib/passwd and /usr/lib/group after the OSTree
# postprocess, as on the FP6 image.
handheld_rootfs_altfiles "$rootfs" >/dev/null
grep -q '^passwd: .*altfiles' "$rootfs/etc/authselect/nsswitch.conf" || die 'nsswitch does not read altfiles'
# The OSTree boot directory is in the physical root: rpm-ostree needs it on /boot.
printf '%s\n' '/sysroot/boot /boot none bind 0 0' >"$rootfs/etc/fstab"
# The handheld steps shared with the FP6 image (scripts/mobile/lib).
handheld_rootfs_identity "$repo_root" "$rootfs"
printf 'luma\n' >"$rootfs/etc/hostname"
handheld_rootfs_dconf "$repo_root" "$rootfs"
handheld_rootfs_machine_id "$rootfs"
handheld_rootfs_databases "$rootfs"
handheld_rootfs_account_modes "$rootfs"
# Presets first: preset-all disables what no preset names (greetd included), so
# the image's own enables come after it.
systemctl --root="$rootfs" preset-all
for unit in NetworkManager.service bluetooth.service chronyd.service greetd.service ModemManager.service; do
	[ -e "$rootfs/usr/lib/systemd/system/$unit" ] && systemctl --root="$rootfs" enable "$unit"
done
systemctl --root="$rootfs" disable sshd.service 2>/dev/null || :
! systemctl --root="$rootfs" -q is-enabled serial-getty@ttyGS0.service 2>/dev/null ||
	die 'a release image has a USB console'
# No first-user wizard: its socket stays off, as on the FP6 Alpha 1 image.
systemctl --root="$rootfs" disable luma-first-user.socket 2>/dev/null || :
# The boot contract: greetd is the display manager, the health check is
# enabled, and the LK slot is marked only after the check.
[ "$(readlink "$rootfs/etc/systemd/system/display-manager.service")" = /usr/lib/systemd/system/greetd.service ] ||
	die 'display-manager.service is not greetd'
! systemctl --root="$rootfs" -q is-enabled luma-first-user.socket 2>/dev/null || die 'luma-first-user.socket is enabled'
systemctl --root="$rootfs" -q is-enabled greenboot-healthcheck.service || die 'greenboot-healthcheck.service is not enabled'
# greenboot-healthcheck's Also= must not bring back the grub rollback trigger.
systemctl --root="$rootfs" disable greenboot-set-rollback-trigger.service 2>/dev/null || :
! systemctl --root="$rootfs" -q is-enabled greenboot-set-rollback-trigger.service 2>/dev/null ||
	die 'greenboot-set-rollback-trigger.service is enabled'
[ -L "$rootfs/etc/systemd/system/greenboot-success.target.wants/luma-titan2e-boot-ok.service" ] &&
	[ ! -e "$rootfs/etc/systemd/system/multi-user.target.wants/luma-titan2e-boot-ok.service" ] ||
	die 'luma-titan2e-boot-ok is not ordered after the health check'
rpm --root "$rootfs" -qa --qf '%{NAME} %{EPOCHNUM}:%{VERSION}-%{RELEASE} %{ARCH}\n' | LC_ALL=C sort >"$out/rpm-manifest.txt"

# Release identity (os-release, luma-release, system-release), as the FP6
# finalizer stamps it through the shared release owner.
python3 "$repo_root/scripts/os/lib/os_release.py" --channel nightly --build-id "$build_id" \
	--nightly-date "${build_id%%.*}" --build-date "$(date -u +%F)" \
	--hardware-target unihertz,titan2e --architecture aarch64 --root "$rootfs"

# OSTree layout (as the FP6 finalizer does): the physical root mounts on
# /sysroot, /ostree points into it, and /home lives in /var.
mkdir -p "$rootfs/sysroot"
ln -sfn sysroot/ostree "$rootfs/ostree"
if [ -d "$rootfs/home" ] && [ ! -L "$rootfs/home" ]; then
	mv "$rootfs/home" "$rootfs/var/home"
	ln -s var/home "$rootfs/home"
fi

# OSTree commit.
printf '{"ref": "%s", "boot-location": "modules"}\n' "$ref" >"$work/treefile.json"
rpm-ostree compose postprocess "$rootfs" "$work/treefile.json"
# One RPM database: /usr/share/rpm, aliased from /usr/lib/sysimage/rpm (what
# export-ostree.sh admits).
python3 "$repo_root/scripts/os/lib/rpmdb_layout.py" --root "$rootfs"
ostree init --repo="$out/repo" --mode=archive
ostree commit --repo="$out/repo" --branch="$ref" --tree=dir="$rootfs" \
	--subject="Luma for the Unihertz Titan 2 Elite" \
	--add-metadata-string=version="$build_id" \
	--add-metadata-string=luma.hardware-target=unihertz,titan2e --consume
commit=$(ostree rev-parse --repo="$out/repo" "$ref")

# Sysroot with one deployment. The initramfs boots the newest entry in
# /boot/loader/entries (boot counting, fallback to the previous one).
sysroot=$work/sysroot
mkdir -p "$sysroot"
ostree admin init-fs --modern "$sysroot"
ostree admin os-init --sysroot="$sysroot" luma
ostree config --repo="$sysroot/ostree/repo" set sysroot.bootloader none
ostree pull-local --repo="$sysroot/ostree/repo" --remote=luma "$out/repo" "$ref"
ostree admin deploy --sysroot="$sysroot" --os=luma --karg-none "luma:$ref"
# The account on the deployment, by the shared FP6 Alpha 1 steps
# (seeded_account.py): luma and its groups into /etc, credential 1234.
deployment=$(find "$sysroot/ostree/deploy/luma/deploy" -mindepth 1 -maxdepth 1 -type d -name "$commit.*")
[ "$(printf '%s\n' "$deployment" | grep -c .)" = 1 ] || die 'expected one deployment'
install -d -m 0755 "$sysroot/ostree/deploy/luma/var/home"
[ -e "$sysroot/ostree/deploy/luma/var/home/luma" ] ||
	install -d -m 0700 -o 1000 -g 1000 "$sysroot/ostree/deploy/luma/var/home/luma"
python3 "$repo_root/scripts/mobile/provision-titan2e-account.py" "$deployment" "$normal_greetd" \
	>"$out/account.json" || die 'account provisioning failed'
# The accounts resolve on the deployment (its /etc, altfiles), as at boot.
for q in 'passwd luma' 'passwd greetd' 'group greetd' 'group luma-display' 'group video'; do
	chroot "$deployment" /usr/bin/getent $q >/dev/null || die "getent $q fails on the deployment"
done
chroot "$deployment" /usr/bin/getent group video | grep -q 'luma' || die 'luma is not in video'

# Disk image: GPT, partition 1 = ext4 "LumaRoot" from 1 MiB to the end.
img=$out/luma-titan2e.img
truncate -s "$image_size" "$img"
printf 'label: gpt\nstart=2048, name=LumaRoot, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4\n' | sfdisk -q "$img"
part_bytes=$(( $(sfdisk -d "$img" | sed -n 's/.*size= *\([0-9]*\),.*/\1/p') * 512 ))
truncate -s "$part_bytes" "$work/root.ext4"
mkfs.ext4 -q -L LumaRoot -E root_owner=0:0 -d "$sysroot" "$work/root.ext4"
dd if="$work/root.ext4" of="$img" bs=1M seek=1 conv=notrunc,sparse status=none
# The source revision: LUMA_TITAN2E_SOURCE_REVISION for an exported tree,
# otherwise the checkout's HEAD and whether it has local changes.
revision=${LUMA_TITAN2E_SOURCE_REVISION:-$(git -C "$repo_root" rev-parse HEAD 2>/dev/null || echo unknown)}
dirty=${LUMA_TITAN2E_SOURCE_DIRTY:-$( [ -z "$(git -C "$repo_root" status --porcelain 2>/dev/null)" ] && echo false || echo true)}
install -m 0644 "$kernel_dir/boot_a.img" "$out/luma-titan2e-boot.img"
printf '%s\n' "$info" >"$out/avb-info.txt"
printf 'build_id=%s\nref=%s\ncommit=%s\nkernel=%s\nsource_revision=%s\nsource_dirty=%s\nvariant=%s\n' \
	"$build_id" "$ref" "$commit" "$krel" "$revision" "$dirty" "$variant" >"$out/build.env"
# OSTree marks deployment roots immutable.
chattr -R -i "$sysroot/ostree/deploy" 2>/dev/null || :
rm -rf "$work"
printf 'composed %s (%s) in %s\n' "$ref" "$commit" "$out"
