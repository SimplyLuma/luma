# luma-rpi5-kmod

Kernel modules for HDMI audio on BCM2712 boards (Raspberry Pi 5, Pi 500,
Compute Module 5) on Fedora's kernel, built for one kernel release at a time.

| Module | Source | Why |
|---|---|---|
| `bcm2712-dma.ko` | `drivers/dma/bcm2835-dma.c` with Raspberry Pi's BCM2712 support (rpi-7.2.y), built with `BCM2835_DMA_BCM2712_ONLY` | Mainline has no driver for the BCM2712 40-bit DMA engine. Built under its own name and matching only `brcm,bcm2712-dma`, so the in-tree `bcm2835-dma` keeps serving BCM2835/BCM2711. |
| `vc4.ko` (in `updates/`) | the kernel's own `drivers/gpu/drm/vc4` plus one patch | The 40-bit engine maps slave addresses with `dma_map_resource()`, so on BCM2712 vc4 must hand it the physical `HDMI_MAI_DATA` address. Older SoCs keep the bus address. |

Nothing changes until the device tree has the DMA controller and the HDMI
`dmas` properties: the SD card build merges `luma-rpi5-hdmi-audio`
(`src/luma-rpi5-platform/card/dtb/`) into the trees U-Boot loads.

Sources are not copied into Git. `scripts/devices/prepare-rpi5-kmod-source.sh`
unpacks the Fedora SRPM of the kernel pinned in `config/kernel/rpi5-kmod.env`,
applies Fedora's patch and `patches/linux-rpi5/<series>`, and copies the files
in `sources.list` next to the `Kbuild` files here. `SOURCES.manifest` in the
package records the SRPM and patch hashes.
`scripts/packages/build-luma-rpi5-kmod.sh` builds the RPM.
