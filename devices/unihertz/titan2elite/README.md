# Unihertz Titan 2 Elite (titan2elite)

Luma on the Unihertz Titan 2 Elite, the MediaTek MT6878 keyboard phone with a
1080 × 1200 AMOLED panel. It runs the same Luma system as the Fairphone 6 from
a microSD card, on a mainline 7.2 kernel. The phone's own Android stays on slot
b and on its internal storage, untouched.

## Alpha

Build 20261020.16. Luma on the Titan 2 Elite is an alpha: daily use works, some hardware does not yet.

Works: SIM and eSIM, VoLTE calls (earpiece and speaker), SMS, mobile data (IPv6 with 464XLAT for IPv4), Wi-Fi, Bluetooth, main/tele/front cameras, charging, vibration, keyboard backlight.

Not yet: proximity, rotation, light and compass sensors (the screen stays on at your ear during calls; press the power key to turn it off); GPS (location is Wi-Fi based); call audio over Bluetooth or USB-C headsets; OTA updates (the Titan update channel is not published yet).

Install: download from https://dl.simplyluma.com/mobile/titan2e/alpha1/ and follow [INSTALL.md](INSTALL.md). Android stays on slot b; Luma boots from slot a and the microSD card. First boot opens the lock screen for the pre-made account; the PIN is 1234.

Built on the generic Luma image; this branch adds the Titan pieces.

## Hardware

| Part | In Luma |
| --- | --- |
| Display | The bootloader's DSI command-mode path, refreshed in step with the panel's tearing signal (60 Hz), with page flips paced to the refresh, brightness, panel sleep, and a panel reset when it does not come back after wake. Phosh at scale 2.5. |
| GPU | Mali-G615 with panthor and Mesa 26.1.6 (Mali-G615 backport). The compositor and apps render on the GPU, and fall back to the CPU when the GPU is not available. |
| Touch | FocalTech FT3383 over SPI, including the edge gestures. |
| Keyboard | AW9523B matrix with the Alt and Fn layers printed on the keys; double tap Shift for Caps Lock. Keyboard backlight (AW36404). Power and volume keys. |
| Wi-Fi | MediaTek CONNSYS (MT6637 radio) with NetworkManager, on 2.4 and 5 GHz. |
| Bluetooth | Same chip, through BlueZ: pairing, A2DP audio, network tethering (BNEP) and LE devices. |
| Audio | Speaker (FourSemi FS19xx amplifier), earpiece and main microphone, through PipeWire; the speaker and earpiece are ports of one output. Loaded at boot. |
| Cameras | Main (Sony IMX766), telephoto (OmniVision OV50D40) and front (OmniVision OV32B40) through MT6878 seninf and CAMSV raw capture, with libcamera's software ISP; the Camera app uses all three. |
| Battery and charging | Charge level from the fuel gauge; MT6375 charger with USB charger detection (BC1.2). |
| Vibration | Vibration motor on the MT6369 VIBR supply. |
| USB | Peripheral mode. |
| Cellular | MediaTek modem over CCCI, run by `luma-modemd` with ModemManager: SIM and eSIM, registration, SMS, mobile data (with 464XLAT on IPv6-only networks) and VoLTE calls on the earpiece and speaker. |
| Storage | Luma runs from a microSD card, grown to the whole card on first start; internal storage keeps Android. |
| Updates | OSTree deployments with boot counting: an update that fails to start twice falls back to the previous one. |

Not supported yet: the proximity, rotation, light and compass sensors, GPS
(location is Wi-Fi based), and call audio over Bluetooth or USB-C headsets. The boot
image is signed with the AOSP test key, which the unlocked bootloader accepts.

## Sources

| What | Where |
| --- | --- |
| Kernel | [MT6878-mainline/linux](https://github.com/MT6878-mainline/linux) `12f2e9f0` plus [`patches/linux-mt6878`](../../../patches/linux-mt6878), with [`config/mobile/titan2e-kernel.config`](../../../config/mobile/titan2e-kernel.config). Release `7.2.0-luma-titan2e-m2-g7083190aafdc`. |
| Initramfs | [`config/mobile/titan2e-initramfs/init`](../../../config/mobile/titan2e-initramfs/init) and the static BusyBox from Fedora `busybox-1.37.0-7.fc44`, built into the kernel. It boots the newest OSTree deployment on the card and counts its boots. |
| Boot image | [`scripts/packages/build-titan2e-kernel.sh`](../../../scripts/packages/build-titan2e-kernel.sh): Android v4 boot image with an AVB hash footer, and the `titan2e-kernel-modules` RPM. |
| Wi-Fi and Bluetooth drivers | MediaTek's MT6878 GPL drivers with [`packaging/titan2e-connsys`](../../../packaging/titan2e-connsys), built by [`scripts/packages/build-titan2e-connsys-modules.sh`](../../../scripts/packages/build-titan2e-connsys-modules.sh). |
| Platform package | [`src/luma-titan2e-platform`](../../../src/luma-titan2e-platform) and [`packaging/rpm/luma-titan2e-platform.spec`](../../../packaging/rpm/luma-titan2e-platform.spec): boot-slot confirmation after the health check, deployment blessing, first-boot root grow, switching to Android, watchdog, GPU, Wi-Fi/Bluetooth and audio services, UCM profile, WirePlumber policy, camera configuration and output scale. |
| Camera | [`patches/libcamera`](../../../patches/libcamera) 0032-0034 (request lifetime, 8-bit Bayer scaling, in-flight requests on stop) and the Camera app in [`src/prairie-core`](../../../src/prairie-core). |
| Modem | [`packaging/titan2e-ccci`](../../../packaging/titan2e-ccci) (MediaTek's GPL CCCI drivers ported to 7.2, built by [`scripts/packages/build-titan2e-ccci-modules.sh`](../../../scripts/packages/build-titan2e-ccci-modules.sh)), `luma-modemd`, `luma-titan2e-speech` and the UCM Voice Call verb in the platform package, and [`patches/modemmanager-fp6`](../../../patches/modemmanager-fp6) 0004-0011. Where the modem protocols come from: [`PROVENANCE.md`](../../../src/luma-titan2e-platform/PROVENANCE.md). |
| eSIM | [`src/luma-esim-broker`](../../../src/luma-esim-broker) and `lpac-titan2e`: [lpac](https://github.com/estkme-group/lpac) `3ff35594` with [`patches/lpac`](../../../patches/lpac) 0001-0005. |
| Mesa | [`packaging/rpm/mesa.spec`](../../../packaging/rpm/mesa.spec) with [`patches/mesa`](../../../patches/mesa) and the inputs pinned in [`config/mobile/mesa-source.env`](../../../config/mobile/mesa-source.env). |

[`source-pins.json`](source-pins.json) lists every upstream input with its
revision, and [`public-file-manifest.json`](public-file-manifest.json) the
SHA-256 of every file this device adds.

## Building from this branch

Everything below builds from this branch and public upstreams at pinned
revisions, in the Fedora 44 builder (`scripts/packages/run-in-rpm-builder.sh`):

| Step | Script | Output |
| --- | --- | --- |
| Kernel | `scripts/packages/build-titan2e-kernel.sh` | `boot_a.img`, `titan2e-kernel-modules`, `kbuild/` |
| Modem drivers | `scripts/packages/build-titan2e-ccci-modules.sh` | CCCI modules, from the vendored `packaging/titan2e-ccci/kernel_device_modules` |
| Wi-Fi/Bluetooth drivers | `scripts/packages/build-titan2e-connsys-modules.sh` | CONNSYS modules, from the vendored `packaging/titan2e-connsys` trees |
| Platform | `scripts/packages/build-luma-titan2e-platform.sh` | `luma-titan2e-platform` |
| GPU | `scripts/packages/build-mesa.sh` | Mesa with Mali-G615 support |
| Modem stack | `scripts/mobile/build-fp6-gnss-rpm.sh` | `ModemManager` and `libqmi` with `patches/modemmanager-fp6` and `patches/ModemManager/owned-fd` |
| Call audio | `scripts/packages/build-callaudiod.sh` | `callaudiod` |
| eSIM | `scripts/packages/build-luma-esim-broker.sh`, `scripts/packages/build-lpac-titan2e.sh` | `luma-esim-broker`, `lpac-titan2e` |
| Camera | `scripts/mobile/build-fp6-libcamera.sh` | libcamera with `patches/libcamera/upstream-fp6` and `patches/libcamera` |

The rest of the system is Luma's generic handheld package set; the Titan
image is that set plus the packages above, with the Qualcomm boot packages
left out.

## The image

`scripts/mobile/compose-titan2e-image.sh RPM_DIR KERNEL_DIR OUTPUT_DIR` composes
the microSD image (`luma-titan2e.img`, an OSTree deployment on GPT partition
`LumaRoot`) and `luma-titan2e-boot.img` (published compressed as
`luma-titan2e-alpha1.zst` and as `boot.bin`) from the generic Luma handheld package
set plus the Titan packages built above. Its Titan inputs are in this branch:
`config/mobile/titan2e/packages.txt`, `config/mobile/titan2e/fedora-baseline.json`,
`scripts/mobile/provision-titan2e-account.py` (the pre-made `luma` account with
PIN 1234) and `scripts/mobile/tests/smoke-titan2e-initramfs.sh` (boots the
initramfs against a composed image).

### Generic dependencies

These come from the generic Luma image build, not from this branch:

- the generic Luma handheld RPMs in `RPM_DIR`, at the versions in
  `config/mobile/alpha1-package-inputs.env` and `config/desktop/inputs.env`
  (luma-phosh, phoc, luma-greeter, luma-greetd-session, luma-update,
  luma-keyring, prairie-core-apps, luma-developer-platform, Viola and others);
- `config/mobile/alpha1-package-inputs.env` and
  `config/mobile/fedora-alpha1-baseline.json` (the pinned handheld and Fedora
  package sets);
- `scripts/mobile/lib/handheld-rootfs.sh` (handheld identity, dconf, machine id,
  account file modes);
- `scripts/mobile/seeded_account.py` and its `recipient_state.py` (the default
  account, used by `provision-titan2e-account.py`);
- `scripts/os/lib/os_release.py` with hardware targets, and
  `scripts/os/lib/rpmdb_layout.py`;
- the handheld greetd configuration,
  `config/mobile/fp6-physical/overlay/etc/greetd/config.toml`.

## Keys

Nothing in this branch needs a private key, and none is included. Every build
works with the defaults below; bring your own keys to replace them.

| What | Default | Your own |
| --- | --- | --- |
| `boot_a` (AVB hash footer) | the AOSP test key `testkey_rsa2048.pem`, pinned in `config/mobile/titan2e-kernel-source.json`. The unlocked bootloader accepts any key; `vbmeta` stays Unihertz's. | `TITAN2E_AVB_KEY=/path/to/rsa2048.pem scripts/packages/build-titan2e-kernel.sh` (`openssl genrsa -out rsa2048.pem 2048`). |
| Kernel modules | signed with a key each kernel build generates (`certs/signing_key.pem` in its build output; `CONFIG_MODULE_SIG`, not enforced). | Nothing to do; or set `CONFIG_MODULE_SIG_KEY` to your key. |
| OS updates | `/etc/luma/update.conf` (from `luma-titan2e-platform`) follows Luma's `unihertz-titan2e` channel, which is not published yet: update checks find nothing until it is. Set `automatic_download = false` to stop them. | Serve your own OSTree repository and update graph: sign commits with your GPG key (`gpg-verify=true` in `/etc/ostree/remotes.d/`), sign the graph with your minisign key and put its public key in `/etc/luma/update-graph-keys.d/`, then point `graph_url`, `stable_repo_url` and `ref_template` in `/etc/luma/update.conf` at your server. `scripts/os/generate-release-keys.sh` creates both key pairs. |

## Firmware

No firmware is in this repository, and none is needed from outside the phone.
The bootloader loads the Wi-Fi, Bluetooth and GNSS processor code from the
phone's own `connsys_*` partitions and the modem image from `modem_a`. On first
boot, `luma-titan2e-connsys-fw` copies the rest from the phone's vendor
partition, read-only, to `/var/lib/titan2e-connsys/firmware`: the ten files in
[`firmware.manifest`](firmware.manifest), plus the phone's own Wi-Fi
calibration and Bluetooth address from `nvdata`. `luma-titan2e-modem-fs` copies
the modem's NVRAM file store once from `nvdata`, `nvcfg`, `protect1`,
`protect2` and `mcf_ota` (read-only) into `/var/lib/luma-modem`; the modem
works from that copy. The Mali CSF firmware comes from Fedora's
`linux-firmware`. The cameras need no firmware.
