# Devices

Luma Mobile runs on many phones from one source tree. Each device is a small,
strict manifest; everything a device shares with others lives in its SoC
family; kernels live in their own repository. This page explains the layout,
the support tiers and how to add a device.

## Layout

```
devices/
  device.schema.json          schema for every device.toml
  boot.schema.json            schema for every boot.toml
  <vendor>/<codename>/
    device.toml               the manifest: identity, SoC, tier, kernel tag, releases
    boot.toml                 kernel command line, AVB footer, dtbo policy
    firmware.manifest         firmware file names and SHA-256, no firmware
    INSTALL.md                the install guide, as published
    tests/test_<vendor>_<codename>*.py
socs/
  soc.schema.json             schema for every soc.toml
  <vendor>/<family>/soc.toml  shared by every device on that chip family
```

A device directory holds exactly those five entries. Anything else fails CI.

**Kernels** are in [SimplyLuma/linux](https://github.com/SimplyLuma/linux), one
branch per SoC family (`milos` for SM7635). Each kernel Luma ships has a tag,
`luma-<codename>-<release>-<kernel release>`, whose message names the
published boot image and the SHA-256 of its kernel, DTB, `.config` and modules.
The device's exact `.config`, device-tree overlays and build steps are in
`luma/<codename>/<release>/` on that branch. `device.toml` points at the tag.

**SoC config** (`socs/<vendor>/<family>/soc.toml`) holds the kernel branch,
boot image format, page size, load offsets and firmware directory. A second
phone on the same chip inherits all of it, so its `device.toml` and
`boot.toml` only state what is different about that phone.

**Firmware** never enters git. `firmware.manifest` is the same file the OS
keeps next to the firmware on the phone (`/var/lib/luma/device-firmware`): a
header with the file count, then one tab-separated row per directory (`D`),
file (`F`, with SHA-256) or relative alias (`L`). The OS checks the store
against it before boot, and `device.toml` records the manifest's own SHA-256.

## Support tiers

| Tier | What it means | What it needs |
|---|---|---|
| `supported` | Luma publishes install images and OS updates for it, tests every release on the real device, and treats a regression as a release blocker. | A maintainer on the Luma team, at least one entry in `releases`, an update ref, and device tests. |
| `community` | Builds from this tree and is looked after by a named outside maintainer. Luma reviews changes but does not test releases on it or block on it. | A maintainer who answers issues, device tests. |
| `experimental` | Bring-up. It may not boot, may lose features between releases, and may be removed if nobody maintains it. Never offered to people who did not go looking for it. | A maintainer. May run SELinux permissive; every other tier must boot with `enforcing=1`. |

A device moves up a tier by pull request, with the evidence (which releases
were tested and how) in the description. A device whose maintainer leaves
drops to `experimental` until someone takes it over.

## Adding a device

1. Check whether its SoC family exists under `socs/`. If it does not, add
   `socs/<vendor>/<family>/soc.toml` and create the family's branch in
   SimplyLuma/linux from a public upstream tree. Name families after the
   upstream kernel's name for the chip, lower case.
2. Get the kernel building on that branch. Put the device's `.config`, DT
   overlays and build steps under `luma/<codename>/<release>/` and tag the
   commit you ship.
3. Create `devices/<vendor>/<codename>/` with the five entries. The codename is
   the one the vendor and upstream kernel use, lower case. Copy the Fairphone
   6 directory as a starting point and remove what does not apply.
4. List the firmware the device needs in `firmware.manifest`. Record names and
   hashes only; say in `INSTALL.md` where the person gets the firmware from
   (usually the vendor's own factory package).
5. Add the directory to `.github/CODEOWNERS` with its maintainers.
6. Write device tests for anything the shared checks cannot see: install
   guide commands that must match the manifests, partitions that must never
   be written, and so on.
7. To list the device in Luma Imager, add an `[imager]` table to
   `device.toml` ([imager-catalog.md](imager-catalog.md)).
8. Run the checks, then open a pull request with tier `experimental` or
   `community`:

```sh
python3 -m venv /tmp/devices && /tmp/devices/bin/pip install jsonschema==4.23.0
/tmp/devices/bin/python -B tools/check-devices.py
/tmp/devices/bin/python -B -m unittest tests.unit.test_device_tree
```

## What the checks enforce

`tools/check-devices.py` runs in CI on every pull request. It fails when:

- a manifest has an unknown or missing key, or a value outside its schema;
- a directory's path does not match the vendor, codename or family inside it;
- a device names a SoC family that does not exist;
- a file under `devices/` or `socs/` is binary, an archive, a key, over
  512 KiB, or of a firmware file type;
- any file contains a number shaped like an IMEI or ICCID;
- `firmware.manifest` does not match its recorded hash, count or row format,
  or an alias points outside the firmware store;
- the kernel command line drops `enforcing=1` (outside `experimental`) or
  contains a forbidden argument such as `selinux=0`;
- the install guide flashes or erases a partition the device must preserve;
- the `[imager]` table promises an install it cannot do: `available` without
  a release, guide or stated file sizes, or `fastboot_bundle` without
  `fastboot_product`; or its guide writes a preserved partition;
- a device or SoC directory has no owner in `.github/CODEOWNERS`.

It then runs every device's own tests.

## Never in this tree

- Firmware, bootloaders, or any other binary from a vendor.
- Signing keys, tokens or other secrets.
- Anything identifying a particular phone or person: IMEI, ICCID, serial
  numbers, Wi-Fi names, backups.
- Code that writes an IMEI or other radio identity. Luma only ever preserves
  the identity the phone already has.

## Fairphone 6 corresponding source

The Fairphone 6 boot image (`luma-mobile-alpha1-fp6-boot.bin`, SHA-256
`6b93fa90…`) is built from:

- Kernel, DTB and modules: [SimplyLuma/linux `luma-fp6-alpha1-7.1.2-luma-fp-ims1.gmu2.selinux1`](https://github.com/SimplyLuma/linux/tree/luma-fp6-alpha1-7.1.2-luma-fp-ims1.gmu2.selinux1)
  (GPL-2.0-only), based on milos-mainline/linux `dfe0125e`.
- Initramfs: three concatenated cpio archives. The kernel unpacks them in
  order, and a later file replaces an earlier one.
  1. and 2. postmarketOS edge, unmodified:
     [`postmarketos-initramfs` 3.12.0-r0](https://gitlab.postmarketos.org/postmarketOS/pmaports/-/tree/41732e213ba0/main/postmarketos-initramfs)
     (GPL-2.0-or-later), modules from
     [`linux-postmarketos-qcom-milos` 7.1.2](https://gitlab.postmarketos.org/postmarketOS/pmaports/-/tree/5689eb3c5174/device/testing/linux-postmarketos-qcom-milos)
     (GPL-2.0-only), and userspace from the matching Alpine and postmarketOS
     edge packages.
  3. The Luma overlay:
     - `/init_functions.sh` is
       [`scripts/mobile/fp6-native-init-functions.sh`](../scripts/mobile/fp6-native-init-functions.sh),
       byte for byte. It is postmarketOS's 3.12.0 `init_functions.sh` plus 119
       added lines (the firmware store check and the OSTree root setup), and
       stays GPL-2.0-or-later.
     - `/usr/lib/ostree/ostree-prepare-root` is from `ostree-2026.5-1.luma.2.fc44`,
       built by [`scripts/packages/build-ostree-fp6.sh`](../scripts/packages/build-ostree-fp6.sh)
       from the upstream revisions pinned in
       [`config/mobile/fp6-ostree-source.json`](../config/mobile/fp6-ostree-source.json)
       plus [`patches/ostree/`](../patches/ostree)
       (LGPL-2.0-or-later), with
       [`packaging/rpm/ostree-fp6.spec`](../packaging/rpm/ostree-fp6.spec).
     - The libraries it needs are unmodified Fedora 44 packages: glibc 2.43-9,
       gcc 16.2.1-2 (`libgcc_s`), glib2 2.88.4-1, util-linux 2.41.5-1,
       composefs 1.0.8-5, openssl 3.5.9-1, libffi 3.5.2-2, pcre2 10.47-1.fc44.1,
       libselinux 3.11-2, systemd 259.9-1 and zlib-ng 2.3.3-3. Their sources are
       the matching packages at [src.fedoraproject.org](https://src.fedoraproject.org/).
     - Kernel modules for `7.1.2-luma-fp-ims1.gmu2.selinux1` are from the
       kernel tag above. The boot theme (`luma-loading-handheld`, Figtree) is
       Luma's own artwork.
