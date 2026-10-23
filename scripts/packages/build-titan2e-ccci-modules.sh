#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build the Titan 2 Elite modem kernel modules (MediaTek CCCI: ccci_util, eccci,
# CCIF/DPMAIF transports, ccmni) out of tree against the Titan kernel.
#   build-titan2e-ccci-modules.sh [kernel [output dir]]
# kernel is the build-titan2e-kernel.sh output (default) or a built kernel tree.
# Sources: the MT6878 GPL vendor tree at a pinned commit plus
# packaging/titan2e-ccci/titan2e-ccci.patch. Two of its headers
# (rps/rps_perf.h, slbc/slbc_ops.h) are used unchanged from the same tree.
set -euo pipefail
repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
kdir=$(realpath "${1:-$repo_root/build/packages/titan2e-kernel}")
[ ! -d "$kdir/kbuild/obj" ] || kdir=$kdir/kbuild/obj
out=${2:-$repo_root/build/titan2e-ccci}
[ -e "$kdir/Module.symvers" ] || { echo "$kdir: not a built kernel tree" >&2; exit 1; }
[ ! -e "$out" ] || { echo "Existing output must be retained: $out" >&2; exit 1; }
command -v dtc >/dev/null || { echo "dtc is required" >&2; exit 1; }

KDM_URL=https://github.com/motorola-mt6878/kernel_device_modules.git
KDM_REV=6586afaa0622648a6e740fb4a83f4b8057346bc2
KDM_HEADERS="drivers/misc/mediatek/rps/rps_perf.h b38bca244cb2b772656214c4675ada2095d560c0fdf6241b3b62e86845dd045f
drivers/misc/mediatek/slbc/slbc_ops.h 064fc397745f8c71da6b15f7b2a6df23db273de45b6227c603423d3be440f9cc"

mkdir -p "$(dirname "$out")"
work=$(mktemp -d "${out}.work.XXXXXX")
git init -q "$work/kdm"
git -C "$work/kdm" remote add origin "$KDM_URL"
git -C "$work/kdm" sparse-checkout set --no-cone /drivers/misc/mediatek/ccci_util/ \
	/drivers/misc/mediatek/eccci/ /drivers/misc/mediatek/ccmni/ /drivers/misc/mediatek/include/ /include/ \
	/drivers/misc/mediatek/rps/rps_perf.h /drivers/misc/mediatek/slbc/slbc_ops.h
git -C "$work/kdm" fetch -q --depth 1 --filter=blob:none origin "$KDM_REV"
git -C "$work/kdm" checkout -q FETCH_HEAD

src=$work/src
mkdir -p "$src"
# Export tracked source and executable bits, not inherited checkout ACLs.
# Shared build directories may carry ACL identities absent from the job sandbox.
git -C "$work/kdm" archive HEAD drivers/misc/mediatek/ccci_util \
	drivers/misc/mediatek/eccci drivers/misc/mediatek/ccmni \
	drivers/misc/mediatek/include include | tar -x -C "$src"
mv "$src/include" "$src/include-kdm"
patch -d "$src" -p1 -s <"$repo_root/packaging/titan2e-ccci/titan2e-ccci.patch"
while read -r path sha; do
	dest=$src/compat/$(basename "$path")
	git -C "$work/kdm" show "HEAD:$path" >"$dest"
	[ "$(sha256sum "$dest" | cut -d' ' -f1)" = "$sha" ] || { echo "checksum differs: $path" >&2; exit 1; }
done <<<"$KDM_HEADERS"
patch -d "$src" -p1 -s <"$repo_root/packaging/titan2e-ccci/0002-remove-sysctl-sentinels.patch"
patch -d "$src" -p1 -s <"$repo_root/packaging/titan2e-ccci/0003-parent-ccmni-to-modem.patch"

make -C "$kdir" M="$src" ARCH=arm64 LLVM=1 -j"${JOBS:-8}" CONFIG_DEBUG_INFO_BTF_MODULES= \
	CONFIG_MTK_ECCCI_DRIVER=m CONFIG_MTK_NET_CCMNI=m modules

krel=$(cat "$kdir/include/config/kernel.release")
dest=$out/usr/lib/modules/$krel/extra/titan2e-ccci
mkdir -p "$dest"
find "$src" -name '*.ko' ! -name ccci_cldma.ko ! -name ccci_fsm_scp.ko -exec cp {} "$dest/" \;
for m in "$dest"/*.ko; do "${STRIP:-llvm-strip}" --strip-debug "$m"; done
rm -rf "$work"
ls "$dest"
