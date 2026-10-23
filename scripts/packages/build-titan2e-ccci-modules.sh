#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build the Titan 2 Elite modem kernel modules (MediaTek CCCI: ccci_util, eccci,
# CCIF/DPMAIF transports, ccmni) out of tree against the Titan kernel.
#   build-titan2e-ccci-modules.sh [kernel [output dir]]
# kernel is the build-titan2e-kernel.sh output (default) or a built kernel tree.
# Sources: packaging/titan2e-ccci/kernel_device_modules (MediaTek's MT6878 GPL
# release, vendored unmodified) plus packaging/titan2e-ccci/*.patch.
set -euo pipefail
repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
kdir=$(realpath "${1:-$repo_root/build/packages/titan2e-kernel}")
[ ! -d "$kdir/kbuild/obj" ] || kdir=$kdir/kbuild/obj
out=${2:-$repo_root/build/titan2e-ccci}
[ -e "$kdir/Module.symvers" ] || { echo "$kdir: not a built kernel tree" >&2; exit 1; }
[ ! -e "$out" ] || { echo "Existing output must be retained: $out" >&2; exit 1; }
command -v dtc >/dev/null || { echo "dtc is required" >&2; exit 1; }

# packaging/titan2e-ccci/kernel_device_modules: unmodified files from
# https://github.com/motorola-mt6878/kernel_device_modules at
# 6586afaa0622648a6e740fb4a83f4b8057346bc2, checked against their SHA-256.
kdm=$repo_root/packaging/titan2e-ccci/kernel_device_modules
(cd "$kdm" && sha256sum --quiet -c "$repo_root/packaging/titan2e-ccci/kernel_device_modules.sha256") ||
	{ echo "$kdm differs from kernel_device_modules.sha256" >&2; exit 1; }

mkdir -p "$(dirname "$out")"
work=$(mktemp -d "${out}.work.XXXXXX")
src=$work/src
mkdir -p "$src/drivers/misc/mediatek" "$src/include-kdm/soc/mediatek"
cp -R "$kdm/drivers/misc/mediatek/"{ccci_util,eccci,ccmni,include} "$src/drivers/misc/mediatek/"
cp "$kdm/include/soc/mediatek/emi.h" "$src/include-kdm/soc/mediatek/"
patch -d "$src" -p1 -s <"$repo_root/packaging/titan2e-ccci/titan2e-ccci.patch"
cp "$kdm/drivers/misc/mediatek/rps/rps_perf.h" "$kdm/drivers/misc/mediatek/slbc/slbc_ops.h" "$src/compat/"
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
