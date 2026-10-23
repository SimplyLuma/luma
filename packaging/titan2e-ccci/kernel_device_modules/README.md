# MediaTek CCCI driver source (vendored)

Unmodified files from the MediaTek MT6878 GPL release:

- Repository: https://github.com/motorola-mt6878/kernel_device_modules
- Commit: `6586afaa0622648a6e740fb4a83f4b8057346bc2`

| Path | Use |
| --- | --- |
| `drivers/misc/mediatek/ccci_util/` | `ccci_util_lib.ko` |
| `drivers/misc/mediatek/eccci/` | `ccci_md_all.ko`, `ccci_auxadc.ko`, `ccci_ccif.ko`, `ccci_dpmaif.ko` |
| `drivers/misc/mediatek/ccmni/` | `ccmni.ko` |
| `drivers/misc/mediatek/include/` | shared MediaTek headers |
| `drivers/misc/mediatek/rps/rps_perf.h`, `drivers/misc/mediatek/slbc/slbc_ops.h` | headers the drivers include |
| `include/soc/mediatek/emi.h` | the only header of the release's `include/` tree the build uses |

The files keep their upstream copyright notices and license, GPL-2.0
(`LICENSES/GPL-2.0.txt`). `../kernel_device_modules.sha256` lists the SHA-256
of every file; `scripts/packages/build-titan2e-ccci-modules.sh` checks the tree
against it, then applies `../titan2e-ccci.patch` and the numbered patches to a
copy. Changes to these files belong in those patches, never here.
