#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build the Unihertz Titan 2 Elite kernel from config/mobile/titan2e-kernel-source.json:
# boot_a.img (Image.lz4 with the built-in DTB and initramfs, in an Android v4
# boot image with an AVB hash footer for boot), the titan2e-kernel-modules RPM,
# and kbuild/ for out-of-tree modules (build-titan2e-connsys-modules.sh).
#   TITAN2E_LINUX_GIT  fetch the kernel commit from this URL or local repository
#   TITAN2E_AVB_KEY    AVB signing key (default: the pinned AOSP test key, which
#                      an unlocked bootloader accepts)
#   JOBS               make jobs (default 8)
set -euo pipefail
repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo_root/config/desktop/inputs.env"
output="$repo_root/build/packages/titan2e-kernel"
[ ! -e "$output" ] || { echo 'Existing output must be retained' >&2; exit 1; }
mkdir -p "$(dirname "$output")"
work=$(mktemp -d "$output.work.XXXXXX")
trap 'printf "Kernel workspace retained: %s\n" "$work" >&2' ERR

eval "$(python3 - "$repo_root/config/mobile/titan2e-kernel-source.json" <<'PY'
import json, shlex, sys
c = json.load(open(sys.argv[1]))
v = {k: c[k] for k in ('repository', 'commit', 'patches', 'config', 'dtb', 'boot_partition_size')}
# Bumped when only the config changes: the patch series sets the release date.
v['config_release'] = c.get('config_release', 1)
bb = c['initramfs']['busybox']
v.update(initramfs_init=c['initramfs']['init'], busybox_url=bb['url'],
         busybox_sha256=bb['sha256'], busybox_path=bb['path'])
for name, t in c['tools'].items():
    for k in ('url', 'commit', 'path', 'sha256'):
        v[f'{name}_{k}'] = t[k]
for k, x in v.items():
    print(f'{k}={shlex.quote(str(x))}')
PY
)"

tools=$work/tools
mkdir -p "$tools/gki"
fetch_tool() { # fetch_tool <name> <destination>
	local url commit path sha
	eval "url=\$${1}_url commit=\$${1}_commit path=\$${1}_path sha=\$${1}_sha256"
	curl -fsSL "$url/+/$commit/$path?format=TEXT" | base64 -d >"$2"
	[ "$(sha256sum "$2" | awk '{print $1}')" = "$sha" ] || { echo "checksum differs: $path" >&2; exit 1; }
}
fetch_tool mkbootimg "$tools/mkbootimg.py"
fetch_tool gki_certificate "$tools/gki/generate_gki_certificate.py"
: >"$tools/gki/__init__.py"
fetch_tool avbtool "$tools/avbtool.py"
key=${TITAN2E_AVB_KEY:-}
[ -n "$key" ] || { key=$tools/testkey_rsa2048.pem; fetch_tool avb_testkey "$key"; }

# The initramfs: config/mobile/titan2e-initramfs/init and the static BusyBox
# from the pinned Fedora package.
rd=$work/initramfs
mkdir -p "$rd"
curl -fsSL "$busybox_url" -o "$rd/busybox.rpm"
[ "$(sha256sum "$rd/busybox.rpm" | awk '{print $1}')" = "$busybox_sha256" ] || { echo "checksum differs: $busybox_url" >&2; exit 1; }
(cd "$rd" && rpm2cpio busybox.rpm | cpio -id --quiet "./$busybox_path")
cat >"$rd/initramfs.list" <<EOF
dir /dev 0755 0 0
nod /dev/console 0600 0 0 c 5 1
nod /dev/null 0666 0 0 c 1 3
dir /bin 0755 0 0
file /bin/busybox $rd/$busybox_path 0755 0 0
file /init $repo_root/$initramfs_init 0755 0 0
dir /proc 0755 0 0
dir /sys 0755 0 0
dir /run 0755 0 0
dir /tmp 1777 0 0
dir /sysroot 0755 0 0
EOF

src=$work/linux obj=$work/obj
git init -q "$src"
git -C "$src" fetch -q --depth 1 "${TITAN2E_LINUX_GIT:-$repository}" "$commit"
git -C "$src" checkout -q --detach FETCH_HEAD
# Commit as the patch author so the series, and the kernel release derived
# from it, has the same commit IDs wherever it is built.
GIT_COMMITTER_NAME="Project Luma" GIT_COMMITTER_EMAIL=builds@project-luma.local \
	git -C "$src" am -q --committer-date-is-author-date "$repo_root/$patches"/*.patch
ts=$(git -C "$src" log -1 --format=%ct)

mkdir -p "$obj"
cp "$repo_root/$config" "$obj/.config"
"$src/scripts/config" --file "$obj/.config" --set-str TITAN2E_BUILTIN_DTB_PATH "$obj/titan-builtin.dtb" \
	--set-str INITRAMFS_SOURCE "$rd/initramfs.list"
export KBUILD_BUILD_TIMESTAMP="@$ts" KBUILD_BUILD_USER=luma KBUILD_BUILD_HOST=project-luma
kmake() { make -C "$src" O="$obj" ARCH=arm64 LLVM=1 -j"${JOBS:-8}" "$@"; }
kmake olddefconfig
diff <(grep -Ev '^CONFIG_(TITAN2E_BUILTIN_DTB_PATH|INITRAMFS_SOURCE)=' "$repo_root/$config") \
	<(grep -Ev '^CONFIG_(TITAN2E_BUILTIN_DTB_PATH|INITRAMFS_SOURCE)=' "$obj/.config") >&2 ||
	{ echo "$config is not olddefconfig-clean" >&2; exit 1; }
# The built-in tree is linked into Image by .incbin, so build it first.
kmake "$dtb"
cp "$obj/arch/arm64/boot/dts/$dtb" "$obj/titan-builtin.dtb"
kmake Image modules
krel=$(cat "$obj/include/config/kernel.release")

out=$work/output
mkdir -p "$out" "$work/rpmbuild"/{BUILD,BUILDROOT,RPMS,SRPMS,SOURCES,SPECS}
kmake INSTALL_MOD_PATH="$work/modroot" INSTALL_MOD_STRIP=1 modules_install
rm -f "$work/modroot/lib/modules/$krel"/{build,source}
tar --sort=name --owner=0 --group=0 --numeric-owner --mtime="@$ts" \
	-C "$work/modroot/lib/modules" -cf "$work/rpmbuild/SOURCES/modules-$krel.tar" "$krel"

lz4 -l -12 -f -q "$obj/arch/arm64/boot/Image" "$out/Image.lz4"
: >"$work/empty.rd"
python3 "$tools/mkbootimg.py" --header_version 4 --kernel "$out/Image.lz4" \
	--ramdisk "$work/empty.rd" --cmdline "" -o "$out/boot_a.img"
python3 "$tools/avbtool.py" add_hash_footer --image "$out/boot_a.img" \
	--partition_size "$boot_partition_size" --partition_name boot \
	--key "$key" --algorithm SHA256_RSA2048 --salt "$(sha256sum "$out/Image.lz4" | cut -d' ' -f1)"
cp "$obj/titan-builtin.dtb" "$obj/.config" "$obj/System.map" "$out/"
printf '%s\n' "$krel" >"$out/kernel.release"
[ "$krel" = "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["release"])' "$repo_root/config/mobile/titan2e-kernel-source.json")" ] ||
	echo "warning: kernel release $krel differs from the pinned release" >&2

install -m 0644 "$repo_root/packaging/rpm/titan2e-kernel-modules.spec" "$work/rpmbuild/SPECS/package.spec"
"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$work/rpmbuild" "$FEDORA_RPM_BUILD_CONTAINER" "
set -euo pipefail
dnf5 -y install rpm-build
rpmbuild -bb --target aarch64 --define \"_topdir \$PWD\" --define 'krel $krel' \
	--define 'kver ${krel%%-*}' --define 'krelease 1.luma.$(date -u -d "@$ts" +%Y%m%d%H%M).$config_release' SPECS/package.spec
"
cp -a "$work/rpmbuild/RPMS" "$out/"
(cd "$out"; find . -type f ! -name SHA256SUMS -printf '%P\n' | sort | xargs sha256sum >SHA256SUMS)

mv "$out" "$output"
mkdir "$output/kbuild"
mv "$src" "$output/kbuild/linux"
mv "$obj" "$output/kbuild/obj"
rm -rf "$work"
# Keep only what M= module builds need; clean also drops Module.symvers.
cp "$output/kbuild/obj/Module.symvers" "$output/kbuild/Module.symvers"
make -C "$output/kbuild/linux" O="$output/kbuild/obj" ARCH=arm64 LLVM=1 -s clean
make -C "$output/kbuild/linux" O="$output/kbuild/obj" ARCH=arm64 LLVM=1 -s modules_prepare
mv "$output/kbuild/Module.symvers" "$output/kbuild/obj/Module.symvers"
printf 'built %s for %s\n' "$output" "$krel"
