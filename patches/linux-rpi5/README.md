# Raspberry Pi 5 kernel patches (luma-rpi5-kmod)

Applied, in order and without fuzz, to the Fedora kernel source named in
`config/kernel/rpi5-kmod.env` (upstream tarball plus Fedora's patch from that
kernel's SRPM). Only the files in `src/luma-rpi5-kmod/sources.list` are built.
The changes keep the licences of the files they modify (GPL-2.0-or-later for
`bcm2835-dma.c` and `dma-bcm2708.h`, GPL-2.0-only for vc4). Keep every author
header.

## 7.2

| Patch | Origin |
|---|---|
| 0001 | raspberrypi/linux `1565ec90fcc1` "dmaengine: Add support for BCM2708", `include/linux/platform_data/dma-bcm2708.h` hunk only |
| 0002 | raspberrypi/linux `e60679722107` "dmaengine: bcm2835: Load driver early and support legacy API" |
| 0003 | raspberrypi/linux `21dc57f93385` "dmaengine: bcm2835: Add BCM2712 support" |
| 0004 | raspberrypi/linux `03230798c633` "dmaengine: bcm2835: HACK: Support DMA-Lite channels" |
| 0005 | raspberrypi/linux `a8a77b3cddfb` "dmaengine: bcm2835: Fix a build warning" |
| 0006 | Luma: refuse system RAM as a slave address |
| 0007 | Luma: `BCM2835_DMA_BCM2712_ONLY` build (driver name `bcm2712-dma`, BCM2712 match only) |
| 0008 | Luma: vc4 HDMI physical MAI address on BCM2712; Phil Elwell's raspberrypi/linux `2193a0f6678f` limited to BCM2712 |

0002-0005 are the complete Raspberry Pi delta to `drivers/dma/bcm2835-dma.c`
on branch `rpi-7.2.y` at `a7e66d0e19130ac0325cd9a59da6834b35aab85c`: after
0001-0005 the file is identical to that revision. The Fedora 7.2.11 patch
does not touch any of these files.

Upstream status (2026-11-02): the BCM2712 DMA series ("Add support for
BCM2712 DMA engine", Andrea della Porta, Feb 2024; Dave Stevenson's rework)
is not in mainline or linux-next. Drop 0001-0007 and the kmod's DMA module
once the kernel ships a `brcm,bcm2712-dma` driver, and 0008 once vc4 passes
physical addresses upstream; then the overlay's binding must match upstream's.
