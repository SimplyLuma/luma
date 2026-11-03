# luma-rpi5-platform

Raspberry Pi 5 platform support for the Luma Pi image, which runs the Luma
Raspberry Pi kernel (`kernel-luma-rpi`). Every piece is
gated to Pi 5 hardware (device-tree compatible `raspberrypi,5-model-b` or
`raspberrypi,5-model-b-d0`), so the package is inert on other machines.

| File | What it does |
|---|---|
| `/usr/lib/firmware/brcm/brcmfmac43455-sdio.raspberrypi,5-model-b-d0.txt.xz` | Wi-Fi NVRAM alias. D0 boards ask for the `-d0` name, which linux-firmware lacks; it points at the Pi 5 NVRAM. Only requested on D0 boards. |
| `/usr/lib/udev/rules.d/70-luma-rpi5-v3d.rules` | V3D runtime-PM autosuspend 100 ms -> 2000 ms. Matches only `brcm,2712-v3d`. |
| `63-luma-rpi5-gsk-renderer` | User environment generator: `GSK_RENDERER=gl` for GTK apps on Pi 5 only (device-tree compatible). On V3D, GL draws a 4K frame in ~16 ms where the default Vulkan renderer takes ~30 ms. A `GSK_RENDERER` the person set wins. |
| `luma-rpi5-boot-next` | Selects the SD card menu entry for the next boot only (`installed`, `install`, `rescue`, `uboot`). GRUB has no USB keyboard under U-Boot. |
| `luma-rpi5-firmware-sync.service` + `/usr/libexec/luma-rpi5-firmware-sync` | After a good boot (`After=boot-complete.target`), copies the running Raspberry Pi kernel's `dtb/broadcom/bcm27*.dtb` and `dtb/overlays/` to `/boot/efi` (the card's FAT partition), where the board firmware builds Linux's device tree from them. Only when the kernel ships overlays (`kernel-luma-rpi`), `config.txt` boots U-Boot and there is no `dtb/` directory; never touches `config.txt`. |
| `/etc/luma/update.conf` | The Pi image's update stream: `raspberrypi,rpi5`, ref `luma/1/aarch64/raspberrypi-rpi5/<channel>`, its own graph. The Pi image runs `kernel-luma-rpi`; the generic ARM stream would bring back Fedora's kernel, which does not boot on the firmware tree. |
| `card/` | Sources of the card's boot partition, used by `scripts/devices/build-rpi5-disk-image.sh` (preinstalled card: `config.txt`, firmware layout) and `build-rpi5-sd-image.sh` (installer card: `config-uboot.txt`, U-Boot layout); not installed. |

## Device trees

The board firmware builds Linux's tree from the `.dtb` files and `overlays/`
on the card plus `config.txt` (`dtoverlay=vc4-kms-v3d-pi5,cma-256`,
`dtparam=pciex1`, and any lines the owner adds for HATs, I2C, SPI and so on),
filling in the MAC, Bluetooth address, serial and memory. U-Boot uses that
tree itself and hands it to GRUB and Linux unchanged because the card has no
`dtb/` directory. This is how Raspberry Pi OS, Ubuntu, Manjaro and LibreELEC
boot, and it is why `kernel-luma-rpi` exists: HDMI audio (BCM2712 DMA),
Bluetooth (BCM7271 UART), HEVC decode, fan, RTC and power button all come
from Raspberry Pi's tree and its drivers.

Fedora's kernel hangs on the firmware's tree. Cards that boot Fedora's kernel
(the installer card, or a Fedora-kernel deployment) use the U-Boot layout:
upstream trees in `dtb/broadcom` with 256 MiB CMA, no overlays
(`scripts/devices/rpi5-board-dtbs.sh --layout uboot`).

## Hardware status

See `devices/raspberrypi/rpi5/SUPPORT.md`. 4K at 60 Hz needs an HDMI 2.0 TV
input: many TVs expose only 4K 24/25/30 until the input's enhanced or UHD
colour mode is switched on in the TV's own settings; also use a cable rated
for 18 Gbit/s.
