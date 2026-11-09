# OnePlus 6T (fajita)

Experimental. Luma does not publish images for this phone. You build them
yourself. You need an unlocked bootloader and a phone you can erase.

## What works

Tested on Luma Nightly 20261108.5 (`luma/1/aarch64/nightly`), kernel
7.1.0-rc1-sdm845-luma:

- Display, touch, GPU (Adreno 630: GNOME Shell, GTK4, and Android with the
  hardware graphics path).
- Wi-Fi (WCN3990), Bluetooth, battery and charging.
- Speaker, headphones and microphones are listed; speaker playback works.
- USB networking (ECM, phone at 172.16.42.1).
- OTA updates use the shared ARM ref `luma/1/aarch64/nightly`.

Not working or not tested yet: calls and SMS, mobile data, cameras,
fingerprint, the earpiece output outside calls (switching to it leaves
PipeWire stuck), SELinux enforcing.

## Never write these

The install writes only `boot_a`, `boot_b` and `userdata`. Never write
`modemst1`, `modemst2`, `fsg`, `fsc`, `persist`, `vbmeta_a`, `vbmeta_b`,
`modem_a`, `modem_b` or `dtbo_a`/`dtbo_b`. `modemst1` and `modemst2` hold
the phone's own modem settings, including its IMEI. Back up all of them
before you start (read-only `dd` from a running Linux system).

## Firmware

The firmware is not in this tree. Use the OnePlus 6/6T files from
[sdm845-mainline/firmware-oneplus-sdm845](https://gitlab.com/sdm845-mainline/firmware-oneplus-sdm845)
(commit `3e31a0c3e5a061645c09f805387b49fa9d35acbf`) and linux-firmware, as
postmarketOS packages them (`firmware-oneplus-sdm845`,
`linux-firmware-ath10k`, `linux-firmware-qcom`). The image keeps them in
`/var/lib/luma/device-firmware`. They must match `firmware.manifest`.

## Build

1. Kernel: prepare the source from
   `config/kernel/oneplus-fajita-7.1-rc1.json` with
   `scripts/kernel/prepare-source.py`. Merge
   `config/kernel/oneplus-fajita.config` into the base config named in the
   lock (`scripts/kconfig/merge_config.sh -m`), run `make LLVM=1 olddefconfig`,
   then build `Image.gz dtbs modules`.
2. Initramfs: start from `postmarketos-initramfs` 3.11.0-r1. Replace its
   modules with this kernel's. Add `ostree-prepare-root` and its Fedora 44
   libraries from the deployment. Run the OSTree root setup before the
   `/sysroot/etc/os-release` check, and do not mount the pmOS boot partition
   over `/boot`. Before that, point `/ostree/root.a` and `/ostree/root.b` at
   the deployment of the highest-version entry in `/boot/loader/entries`, so
   one boot image keeps working after updates and `rpm-ostree install`. Kernel modules are overlaid from
   `/var/lib/luma/device-modules`, and the firmware loader path points at
   `/var/lib/luma/device-firmware`.
3. Boot image: header v0, page size 4096, `Image.gz` with
   `qcom/sdm845-oneplus-fajita.dtb` appended, offsets from
   `socs/qcom/sdm845/soc.toml`, command line from `boot.toml` plus the
   generated `pmos_root_uuid` and `pmos_boot_uuid` arguments.
4. Userdata image: a GPT with 4096-byte sectors inside `userdata`
   (`LumaBoot` FAT and `LumaRoot` ext4), the verified Luma commit deployed with
   `ostree admin deploy`, and the device files on `/var`.

The device layer also needs, until Luma ships them for this SoC:

- `rmtfs` enabled, and `tqftpserv` (Fedora 44 package) for the Wi-Fi firmware.
- `qbootctl` (Fedora 44 package) run after `boot-complete.target`. Without
  it the bootloader counts every boot as a failed try, marks the slot
  unbootable after 7 boots and switches slots.
- A clock floor: the PM8998 RTC starts at 1970, and GPG then rejects the
  2026 Luma keys (Flatpak, GDM and updates fail).
- A Bluetooth public address set from the SoC serial when `hci0` appears.
- The OnePlus 6T ALSA UCM from
  [sdm845-mainline/alsa-ucm-conf](https://gitlab.com/sdm845-mainline/alsa-ucm-conf)
  (commit `1b8d290e`), and an `/etc/modprobe.d/dist-alsa.conf` that drops
  Fedora's `snd-seq` install rule.
- `/var/lib/waydroid/waydroid.cfg` with `gbm`, `mesa` and `freedreno`
  (SimplyLuma/luma#286).

## Flash

Enter fastboot: hold Volume Up and Power. Then:

```sh
fastboot flash userdata luma-fajita-userdata.sparse
fastboot flash boot_a luma-fajita-boot.img
fastboot flash boot_b luma-fajita-boot.img
fastboot set_active a
fastboot reboot
```

Test a new boot image with `fastboot boot luma-fajita-boot.img` before you
flash it. The USB rescue network is at 172.16.42.1.
