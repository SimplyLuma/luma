# Luma on the Google Pixel 5a (barbet)

**Experimental.** This is a bring-up install for people helping with the port.
It erases Android and all of `userdata`, including any Luma data already on the
phone. Keep the exact Google factory image for the phone; it is the recovery
path. Do not relock the bootloader.

## What you need

- a Pixel 5a (`barbet`) with an unlocked bootloader;
- a computer with `fastboot` and a USB-C data cable;
- a full test set from `scripts/mobile/build-barbet-install.sh`: `boot.img`,
  `userdata.img`, `SHA256SUMS` and `INSTALL.txt`;
- Google's factory image for the phone, to restore Android and to extract the
  firmware Luma needs.

Check the set before flashing:

```sh
sha256sum -c SHA256SUMS
```

## Install

The bootloader rejects `fastboot boot` of a custom image on this phone, so the
install is a flash. The commands below are the only partition writes:

```sh
fastboot devices
fastboot getvar product          # must print barbet
fastboot flash userdata userdata.img
fastboot flash boot_b boot.img
fastboot --set-active=b
fastboot reboot
```

`fastboot flash userdata` erases Android and all existing Luma data. The
install never writes `boot_a` (Android), `modemst1`, `modemst2`, `fsg`, `fsc`, `persist`, `misc`,
`dtbo_a`, `dtbo_b`, `vbmeta_a`, `vbmeta_b`, `bootloader_a`, `bootloader_b`,
`super` or `frp`; do not add commands that do.

## Firmware

The first boot needs the operator-extracted firmware in the shared store
`/var/lib/luma/device-firmware`. The manifest lists the files and hashes;
extract them from the matching factory image, never from another build:

- `qcom/a650_sqe.fw`, `qcom/a650_gmu.bin`, `qcom/a620_zap.*` — GPU;
- `qcom/sm7250/google/barbet/adsp.*` — ADSP, in
  `/var/lib/luma/device-firmware-barbet` on this install;
- `rt5514p_dsp_fw1.bin` … `rt5514p_dsp_fw4.bin` — RT5514P microphone DSP,
  accepted from either store.

The WCN3990 WLAN firmware and `board-2.bin` are listed for the follow-up
Wi-Fi work; they do not make Wi-Fi work until the modem/WLFW stack is enabled.

After the first boot, copy the tree to the phone (USB-NCM/SSH or the installer
path you normally use) and restart the audio service. The current set does not
include the WCN3990 Wi-Fi firmware or `tqftpserv`; see `SUPPORT.md`, Wi-Fi is
not a working function yet.

## After the first boot

Sign in as `luma` with the password `1234` and change it in Settings. Run:

```sh
systemctl status luma-barbet-audio.service
cat /proc/asound/cards
wpctl status
```

The audio service starts the ADSP, sets the mixer routes and exposes the
built-in speaker and microphone. If the card does not appear, check the
firmware tree and `journalctl -u luma-barbet-audio.service`.

## Recovery

To return to Android, flash the matching Google factory image with its own
`flash-all` script. Never repartition, relock the bootloader, or restore a
different factory build. The exact binaries and hashes used for this port are
listed in `SUPPORT.md`; raw captures and private firmware are not in the
repository.
