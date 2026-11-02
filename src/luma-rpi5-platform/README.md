# luma-rpi5-platform

Raspberry Pi 5 platform support for the generic Luma ARM image. Every piece is
gated to Pi 5 hardware (device-tree compatible `raspberrypi,5-model-b` or
`raspberrypi,5-model-b-d0`), so the package is inert on other machines.

| File | What it does |
|---|---|
| `/usr/lib/firmware/brcm/brcmfmac43455-sdio.raspberrypi,5-model-b-d0.txt.xz` | Wi-Fi NVRAM alias. D0 boards ask for the `-d0` name, which linux-firmware lacks; it points at the Pi 5 NVRAM. Only requested on D0 boards. |
| `/usr/lib/udev/rules.d/70-luma-rpi5-v3d.rules` | V3D runtime-PM autosuspend 100 ms -> 2000 ms. Matches only `brcm,2712-v3d`. |
| `luma-rpi5-shell-defaults.service` + `62-luma-rpi5-schemas` | Shell default `org.project_luma.shell-state backdrop-blur=false`. A gschema override would apply on every machine, so the service (`ConditionFirmware=`) compiles the installed Shell state schema with `rpi5.gschema.override` into `/run/luma-rpi5/schemas`, and the user environment generator adds that directory to `GSETTINGS_SCHEMA_DIR`. Values a person sets still win. |
| `luma-rpi5-boot-next` | Selects the SD card menu entry for the next boot only (`installed`, `install`, `rescue`, `uboot`). GRUB has no USB keyboard under U-Boot. |
| `card/` | Sources of the SD card boot partition, used by `scripts/devices/build-rpi5-sd-image.sh`; not installed. |

## Hardware status (kernel 7.2.11, mainline device trees)

- Display (vc4 KMS, V3D GL/Vulkan), USB, Ethernet, NVMe on PCIe and Wi-Fi work.
- **HDMI audio** does not work on mainline yet: it needs the BCM2712 DMA-Lite
  driver, which is still downstream. Use USB audio or an I2S DAC on the header.
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
