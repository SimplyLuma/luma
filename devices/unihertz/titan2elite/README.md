# Unihertz Titan 2 Elite (titan2elite)

Luma on the Unihertz Titan 2 Elite, the MediaTek MT6878 keyboard phone with a
1080 × 1200 AMOLED panel. It runs the same Luma system as the Fairphone 6 from
a microSD card, on a mainline 7.2 kernel. The phone's own Android stays on slot
b and on its internal storage, untouched.

## Hardware

| Part | In Luma |
| --- | --- |
| Display | The bootloader's DSI command-mode path, refreshed in step with the panel's tearing signal (60 Hz), with page flips paced to the refresh, brightness and panel sleep. Phosh at scale 2.5. |
| GPU | Mali-G615 with panthor and Mesa 26.1.6 (Mali-G615 backport). The compositor and apps render on the GPU, and fall back to the CPU when the GPU is not available. |
| Touch | FocalTech FT3383 over SPI, including the edge gestures. |
| Keyboard | AW9523B matrix with the Alt and Fn layers printed on the keys; double tap Shift for Caps Lock. Power and volume keys. |
| Wi-Fi | MediaTek CONNSYS (MT6637 radio) with NetworkManager, on 2.4 and 5 GHz. |
| Bluetooth | Same chip, through BlueZ: pairing, A2DP audio, network tethering (BNEP) and LE devices. |
| Audio | Speaker (FourSemi FS19xx amplifier), earpiece and main microphone, through PipeWire; the speaker and earpiece are ports of one output. Loaded at boot. |
| Cameras | Main (Sony IMX766), telephoto (OmniVision OV50D40) and front (OmniVision OV32B40) through MT6878 seninf and CAMSV raw capture, with libcamera's software ISP; the Camera app uses all three. |
| Battery | Charge level from the fuel gauge. |
| USB | Peripheral mode. |
| Storage | Luma runs from a microSD card, grown to the whole card on first start; internal storage keeps Android. |
| Updates | OSTree deployments with boot counting: an update that fails to start twice falls back to the previous one. |

Not in this branch yet: the cellular modem's userspace (calls, SMS, mobile
data), USB-C analog headsets and Bluetooth call audio. The boot image is signed
with the AOSP test key, which the unlocked bootloader accepts.

## Sources

| What | Where |
| --- | --- |
| Kernel | [MT6878-mainline/linux](https://github.com/MT6878-mainline/linux) `12f2e9f0` plus [`patches/linux-mt6878`](../../../patches/linux-mt6878), with [`config/mobile/titan2e-kernel.config`](../../../config/mobile/titan2e-kernel.config). Release `7.2.0-luma-titan2e-m2-g7035a815908b`. |
| Initramfs | [`config/mobile/titan2e-initramfs/init`](../../../config/mobile/titan2e-initramfs/init) and the static BusyBox from Fedora `busybox-1.37.0-7.fc44`, built into the kernel. It boots the newest OSTree deployment on the card and counts its boots. |
| Boot image | [`scripts/packages/build-titan2e-kernel.sh`](../../../scripts/packages/build-titan2e-kernel.sh): Android v4 boot image with an AVB hash footer, and the `titan2e-kernel-modules` RPM. |
| Wi-Fi and Bluetooth drivers | MediaTek's MT6878 GPL drivers with [`packaging/titan2e-connsys`](../../../packaging/titan2e-connsys), built by [`scripts/packages/build-titan2e-connsys-modules.sh`](../../../scripts/packages/build-titan2e-connsys-modules.sh). |
| Platform package | [`src/luma-titan2e-platform`](../../../src/luma-titan2e-platform) and [`packaging/rpm/luma-titan2e-platform.spec`](../../../packaging/rpm/luma-titan2e-platform.spec): boot-slot confirmation after the health check, deployment blessing, first-boot root grow, switching to Android, watchdog, GPU, Wi-Fi/Bluetooth and audio services, UCM profile, WirePlumber policy, camera configuration and output scale. |
| Camera | [`patches/libcamera`](../../../patches/libcamera) 0032-0034 (request lifetime, 8-bit Bayer scaling, in-flight requests on stop) and the Camera app in [`src/prairie-core`](../../../src/prairie-core). |
| Mesa | [`packaging/rpm/mesa.spec`](../../../packaging/rpm/mesa.spec) with [`patches/mesa`](../../../patches/mesa) and the inputs pinned in [`config/mobile/mesa-source.env`](../../../config/mobile/mesa-source.env). |

[`source-pins.json`](source-pins.json) lists every upstream input with its
revision, and [`public-file-manifest.json`](public-file-manifest.json) the
SHA-256 of every file this device adds.

## Firmware

No firmware is in this repository. The bootloader loads the Wi-Fi, Bluetooth
and GNSS processor code from the phone's own `connsys_*` partitions. On first
boot, `luma-titan2e-connsys-fw` copies the rest from the phone's vendor
partition, read-only, to `/var/lib/titan2e-connsys/firmware`: the ten files in
[`firmware.manifest`](firmware.manifest), plus the phone's own Wi-Fi
calibration and Bluetooth address from `nvdata`. The Mali CSF firmware comes
from Fedora's `linux-firmware`.
