# Devices

One directory per supported device, `devices/<vendor>/<codename>/`, each a
strict manifest checked by `tools/check-devices.py`. Shared chip-family
configuration is in `../socs/`. See [docs/devices.md](../docs/devices.md) for
the layout, support tiers and how to add a device.

| Device | Directory | SoC | Tier |
|---|---|---|---|
| Fairphone 6 | [fairphone/fp6](fairphone/fp6) | Qualcomm SM7635 (milos) | supported |
| OnePlus 6T | [oneplus/fajita](oneplus/fajita) | Qualcomm SDM845 | experimental |
| Raspberry Pi 5 | [raspberrypi/rpi5](raspberrypi/rpi5) | Broadcom BCM2712 | experimental |

## Phones: one image plus a device kit

Every phone installs the same two-part download:

1. The generic phone userdata image of an ARM build
   (`scripts/mobile/build-phone-userdata.sh`): the one ARM OS commit
   (`luma/1/aarch64/<channel>`), nothing device-specific. A GPT with 4096-byte
   sectors: `LumaBoot` (FAT32, 480 MiB at 1 MiB, volume ID `032D-57AD`, empty)
   and `LumaRoot` (ext4, UUID `03797c05-cf87-4eda-adc4-6ce111baf2d3`).
2. The device kit (`scripts/devices/build-device-kit.py`, from `device.toml`):
   - the boot image: kernel, device tree and an initramfs with the shared
     OSTree resolver (`scripts/mobile/common/initramfs`), naming both
     filesystems (`pmos_root_uuid`, `pmos_boot_uuid`);
   - when the device has a `[device_repo]`, `…-kit.sparse`: a `LumaBoot`
     filesystem holding `luma-device-kit/` (`kit.json`, its signature
     `kit.json.asc`, and `repo/`, the signed rpm-md device repository) as an
     Android sparse image that writes only `LumaBoot`'s range of userdata;
   - `FLASH.txt`: `[kit] flash`, the fastboot commands in order.

On the first boot `luma-device-layer.service` (luma-update; the image names the
kit's filesystem in `/etc/luma/device-kit.conf`):

1. checks `kit.json` and `repomd.xml` against the Luma OS Release key
   (`/etc/pki/ostree/luma-release.gpg`) and `[kit] compatible` against the
   device tree's `compatible`;
2. copies the repository to `/var/lib/luma-device-layer/repo` and, with only
   that repository configured (no network needed, firmware included), runs
   one transaction: `rpm-ostree override replace --from repo=<id>` for
   `[device_repo] replace` with `--install` for `[device_repo] install`;
3. writes `/etc/yum.repos.d/<id>.repo` at `[device_repo] url`
   (`repo_gpgcheck=1`, `gpgcheck=1`) and restarts once. From then on
   `rpm-ostree upgrade` keeps the layer and follows the online repository,
   as on the Raspberry Pi.

A boot whose deployment carries the layer writes
`/var/lib/luma-device-layer/layered` and the unit no longer starts. A failure
never restarts the phone; after two staged attempts that did not boot (greenboot
rollback, power lost before finalisation) it stops and logs why
(`journalctl -u luma-device-layer`, `luma-device-layer status`).
`luma-device-layer apply --retry` starts over.

| Device | `[device_repo]` | Kit |
|---|---|---|
| Fairphone 6 | `luma-fairphone-fp6`: modules, firmware, platform, IMS, eSIM, modem stack | boot image + `…-kit.sparse` |
| Pixel 9 (`google,tokay`) | none yet: modules and firmware are in the boot image | boot image only |
