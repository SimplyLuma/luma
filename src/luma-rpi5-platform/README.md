# luma-rpi5-platform

Raspberry Pi 5 platform support for the generic Luma ARM image. Every piece is
gated to Pi 5 hardware (device-tree compatible `raspberrypi,5-model-b` or
`raspberrypi,5-model-b-d0`), so the package is inert on other machines.

| File | What it does |
|---|---|
| `/usr/lib/firmware/brcm/brcmfmac43455-sdio.raspberrypi,5-model-b-d0.txt.xz` | Wi-Fi NVRAM alias. D0 boards ask for the `-d0` name, which linux-firmware lacks; it points at the Pi 5 NVRAM. Only requested on D0 boards. |
| `/usr/lib/udev/rules.d/70-luma-rpi5-v3d.rules` | V3D runtime-PM autosuspend 100 ms -> 2000 ms. Matches only `brcm,2712-v3d`. |
| `luma-rpi5-shell-defaults.service` + `62-luma-rpi5-schemas` | Shell default `org.project_luma.shell-state backdrop-blur=false`. A gschema override would apply on every machine, so the service (`ConditionFirmware=`) compiles the installed Shell state schema with `rpi5.gschema.override` into `/run/luma-rpi5/schemas`, and the user environment generator adds that directory to `GSETTINGS_SCHEMA_DIR`. Values a person sets still win. |
| `63-luma-rpi5-gsk-renderer` | User environment generator: `GSK_RENDERER=gl` for GTK apps on Pi 5 only (device-tree compatible). On V3D, GL draws a 4K frame in ~16 ms where the default Vulkan renderer takes ~30 ms. A `GSK_RENDERER` the person set wins. |
| `luma-rpi5-boot-next` | Selects the SD card menu entry for the next boot only (`installed`, `install`, `rescue`, `uboot`). GRUB has no USB keyboard under U-Boot. |
| `card/` | Sources of the SD card boot partition, used by `scripts/devices/build-rpi5-sd-image.sh`; not installed. `card/dtb/luma-rpi5-hdmi-audio.dtso` is the HDMI audio fragment merged into the card's device trees. |

## Hardware status (kernel 7.2.11, mainline device trees)

- Display (vc4 KMS, V3D GL/Vulkan), USB, Ethernet, NVMe on PCIe and Wi-Fi work.
- **HDMI audio** (both ports) comes from `luma-rpi5-kmod` plus the card's
  device trees with `luma-rpi5-hdmi-audio` merged in; see "HDMI audio" below.
- **4K at 60 Hz** needs an HDMI 2.0 TV input. Many TVs expose only 4K 24/25/30
  until the input's enhanced or UHD colour mode is switched on in the TV's own
  settings; also use a cable rated for 18 Gbit/s.
- **Fan**: the RP1 PWM fan driver is not upstream, so Linux cannot control
  the fan header (official case, Active Cooler). Cases with their own fan
  controller (Argon ONE V3, over I2C) are untested.
- **RTC**: the rtc-rpi driver is posted upstream but not merged; the clock
  comes from the network.
- **Bluetooth**: not working. It needs `brcm/BCM4345C0.hcd` and a kernel with
  `CONFIG_SERIAL_8250_BCM7271`.
- **Power button**: needs a gpio-keys node in the device tree; untested.
- CMA: the card's device trees reserve 256 MB below 1 GB (Fedora recommends
  `cma=256M@0M-1024M`); the kernel's 64 MB default runs out at 4K.

## HDMI audio

Mainline vc4 already registers an HDMI sound card per port (with jack
detection) when the HDMI node has a `dmas` property. On BCM2712 two things are
missing upstream: a driver for the 40-bit DMA engine, and vc4 handing that
engine a physical address. Both live in `luma-rpi5-kmod`
(`src/luma-rpi5-kmod`, patches in `patches/linux-rpi5`): `bcm2712-dma.ko`
is Raspberry Pi's `bcm2835-dma` (rpi-7.2.y) built for BCM2712 only, and
`vc4.ko` is the kernel's own vc4 with the BCM2712 address fix, installed to
`updates/`. The fragment `card/dtb/luma-rpi5-hdmi-audio.dtso` adds the DMA
controller (DMA6-DMA11, `brcm,bcm2712-dma`) and the HDMI0/HDMI1 DMA requests,
with the same cells as Raspberry Pi's device tree. The card build merges it
(`fdtoverlay`) into every BCM2712 tree the kernel ships under the names U-Boot
loads (`dtb/broadcom/bcm2712-rpi-5-b.dtb`, `bcm2712-d-rpi-5-b.dtb`, and
`bcm2712-rpi-500.dtb`, `bcm2712-rpi-cm5-cm5io.dtb`, `bcm2712-rpi-cm5l-cm5io.dtb`
once the kernel has them).

It is not a firmware `dtoverlay=`: Linux gets the tree U-Boot loads from the
card's `dtb/` directory (with U-Boot's board fixups such as the Ethernet MAC),
so firmware overlays never reach it. Removing that file makes U-Boot pass the
firmware's tree instead, which the installed system does not boot with.

Why a kmod per Fedora kernel and not a Luma Pi kernel:

- The Pi stays on the generic ARM image and Fedora's kernel, so kernel
  security updates arrive on Fedora's schedule; a rebuild is seconds, from
  that kernel's own SRPM, against its `kernel-devel`.
- Every Pi item still missing upstream is a module that can be built the same
  way (Bluetooth UART, RTC, RP1 fan PWM, HEVC decoder); only vc4 needs a
  replacement module, and its change is one function.
- `luma-rpi5-kmod` requires `kernel-uname-r` of exactly its kernel, so an
  image compose that bumps the kernel without rebuilding it fails instead of
  shipping a kernel without the DMA driver.
- A separate kernel package would mean a separate image ref, installer and
  full kernel builds for every Fedora update, for patches that are all
  module-sized.

Safety: the fragment is only merged when the card's OS payload carries `luma-rpi5-kmod` for its kernel, because stock vc4 waits
forever for a DMA controller it has no driver for (the display then stays on
the firmware framebuffer). If an initramfs ever pairs stock vc4 with
`bcm2712-dma`, the DMA driver refuses vc4's legacy bus address (it is RAM on
BCM2712) instead of writing to it. The in-tree `bcm2835-dma` is untouched,
so Pi 4 and older keep their mainline behaviour.

Drop the kmod pieces as they land upstream (status in
`patches/linux-rpi5/README.md`).
