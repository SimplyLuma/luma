# Pixel 9 Pro Fold (comet)

Boot image and test-image tooling for the Pixel 9 Pro Fold. The device
manifests are in [devices/google/comet](../../../devices/google/comet); the
install guide is [INSTALL.md](../../../devices/google/comet/INSTALL.md).

| Path | What |
|---|---|
| `patches/linux-zumapro/comet/` | Kernel series on linux-zumapro `c03f06b6` (displays, touch, GPU, Wi-Fi, Bluetooth, audio, call audio, sensors, charging, modem link, GNSS) |
| `config/kernel/pixel9fold-source.json` | Source lock: kernel archive, patches and built-in GPU firmware, with SHA-256 pins |
| `config/kernel/pixel9fold.fragment` | Options on top of `zumapro_defconfig` (boot, SELinux, F2FS, binder, netfilter) |
| `config/kernel/pixel9fold.config` | The resolved `.config`; the build fails if its own differs |
| `config/kernel/pixel9fold.modules` | Modules packed into the initramfs (`/luma-fold/modules`, loaded by hook 83) |
| `config/mobile/comet-initramfs/` | Initramfs files Luma owns: init functions, boot hooks, Fold services and scripts |
| `config/mobile/comet-initramfs.manifest` | Every initramfs entry with its SHA-256 and origin |
| `assemble-initramfs.py` | Builds the initramfs tree from the manifest |
| `build-boot-image.sh` | Builds and signs `boot.img` |
| `mesa/`, `shims/` | Mesa for the Mali-G715 and the phoc/phosh preload shims (initramfs payloads) |
| `compose-on-phone.sh`, `compose-test-commit.sh` | Layer replacement RPMs onto a mobile OSTree commit for testing |

## Building the boot image

Run as root on an aarch64 or x86_64 Linux host with clang/lld, lz4, dtc,
kmod, git, python3 3.11+, simg2img, lpunpack and erofs-utils.

1. **Inputs.**
   - FP6 Alpha 1 boot image (the initramfs base):
     `https://dl.simplyluma.com/mobile/fp6/alpha1/luma-mobile-alpha1-fp6-boot.bin`,
     SHA-256 `6b93fa901d85152efb29e9a58874d040e86fcad597c4dae0c9b11c897cf1051b`.
   - Google's factory image for the phone's firmware, `comet-cp3a.261005.005-factory-b6a1c90d.zip`
     from https://developers.google.com/android/images#comet,
     SHA-256 `b6a1c90da0a7714f3ab1e4d3bb7d8c0a4a5e1ee8d138003c79bdde94ffdc3967`.
     Its firmware is not redistributable and never enters git.
   - `arm/mali/arch11.8/mali_csffw.bin` from linux-firmware 20260309
     (SHA-256 pinned in the source lock).
   - AOSP `mkbootimg.py`, `avbtool.py` and `testkey_rsa4096.pem`.

2. **Vendor partition.**
   ```sh
   unzip comet-cp3a.261005.005-factory-b6a1c90d.zip
   unzip comet-cp3a.261005.005/image-comet-cp3a.261005.005.zip super.img
   simg2img super.img super.raw
   lpunpack -p vendor_a super.raw .
   fsck.erofs --extract=vendor vendor_a.img
   ```
   `firmware.manifest` lists the 18 files used and their SHA-256;
   `config/mobile/comet-initramfs/luma-fold/firmware/LUMA-FOLD-FIRMWARE` maps
   them to their vendor paths.

3. **Payloads.** Built files the initramfs carries, at the paths the manifest
   lists, under one directory (`payload/luma-fold/...`):

   | Origin | Files | Build |
   |---|---|---|
   | `payload:mesa` | `luma-fold/mesa/lib64/*` | `R=<work dir> mesa/build-mesa.sh` (Mesa `e282ea8c6def`, staging/26.3) |
   | `payload:shims` | `luma-fold/luma-fold-*.so` | the `clang` line in each `shims/*.c` |
   | `payload:luma-phosh` | `luma-fold/phosh-shell/phosh` | `/usr/bin/phosh` of luma-phosh `1.luma.90~fold20261029.4` |
   | `payload:modem-stack` | `luma-fold/modem-stack/usr/*` | the RPMs in `luma-fold/modem-stack/PACKAGES`, extracted |
   | `payload:kepler-gnssd` | `luma-fold/gnss/kepler-gnssd`, `caimito-prologue.bin` | `kepler-gnssd` from exynos-modem `eba1e23a` |
   | `payload:tone` | `luma-fold/audio/tone.wav` | any 48 kHz mono S16 WAV (the audio check's test tone) |

   The build warns when a payload's SHA-256 differs from the manifest
   (`--strict` makes it an error).

   **Payloads without source in this branch yet** (tracked; each needs its
   source or packaging added here):

   | Payload | Missing |
   |---|---|
   | `payload:modem-stack` | Sources and specs for ModemManager `1.25.95-0.7.sit26` (samsung-sit plugin), exynos-modem `0.1.0-0.11.20260909giteba1e23aea1b` with its Fold patches (`exynos-modem`, `sit-proxy`, `sit-lpa`, `carrier-settings-tool`), and luma-lpac-sit `2.3.0-1.luma1` |
   | `payload:luma-phosh` | luma-phosh `1.luma.90~fold20261029.4` patches 0065-0067 |
   | `payload:kepler-gnssd` | `kepler-gnssd` build steps and the source of the `caimito-prologue.bin` board profile |
   | `payload:tone` | A script that generates `tone.wav` |
   | `compose-test-commit.sh` | `scripts/mobile/build-fp6-alpha1-fresh-image.py`, the image builder it calls |

4. **Initramfs.**
   ```sh
   scripts/mobile/pixel9-fold/assemble-initramfs.py \
     --fp6-boot luma-mobile-alpha1-fp6-boot.bin --vendor vendor --payload payload \
     [--authorized-keys ~/.ssh/id_ed25519.pub] initramfs
   ```
   `--authorized-keys` lets you SSH in as root over USB networking
   (hook 79); without it the phone has no SSH key.

5. **Boot image.** The command line names the Luma image's root and boot
   filesystem UUIDs and its OSTree deployment. Read them from the image you
   flash: `blkid` on its partitions, and the `ostree=` option in the boot
   partition's `loader/entries/*.conf`.
   ```sh
   ROOT_UUID=... BOOT_UUID=... OSTREE_PATH=/ostree/boot.1/luma/<checksum>/0 \
   MALI_FIRMWARE_DIR=/path/to/linux-firmware \
   MKBOOTIMG=mkbootimg.py AVBTOOL=avbtool.py AVB_KEY=testkey_rsa4096.pem \
   scripts/mobile/pixel9-fold/build-boot-image.sh initramfs out
   ```
   `out/` gets `boot.img`, the `.config`, the embedded DTB and `manifest.env`.

The kernel tree is the pinned archive plus the series applied with `git am`;
its tree matches the `luma/comet-daily8-try` branch the phone's kernel was
built from. Built with the `luma-daily-v8-mobile5` inputs, this produces the
same `.config` apart from `CONFIG_INITRAMFS_SOURCE`, kernel release
`7.3.0-rc5-zumapro+`, a 103950848-byte Image and the same device tree.

### RAM clobber zone

Before the kernel unpacks its initramfs, something in the boot chain writes a
2560-byte table at physical `0x81800000`, which is Image offset `0x1800000`.
The build puts a 2 MiB patterned file first in the initramfs to absorb it and
fails if that file does not cover `0x17f0000`-`0x1840000`. With
`CONFIG_DYNAMIC_DEBUG` off the margin is about 332 KB; a kernel that grows
past it needs a different fix (reserving the range in the linker script).

## Test images

`compose-on-phone.sh` runs on the phone as root: it layers replacement RPMs
onto a commit already in `/ostree/repo` (for example an ARM nightly) and
commits the overlay locally, labelling only the layered files. Deploy the
result with `ostree admin deploy <commit>`.

`compose-test-commit.sh` does the same on a build host, signs the commit with
a local test-only key and builds a fresh-install `userdata` image with the FP6
fresh-image builder (`scripts/mobile/build-fp6-alpha1-fresh-image.py`).
Neither carries rpm-created `passwd`/`group` files over the base: accounts
come from the packages' `sysusers.d` files.
