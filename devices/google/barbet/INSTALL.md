# Luma on the Google Pixel 5a (barbet)

**Experimental.** This is a bring-up install for people helping with the port.
It erases all of `userdata`, including Android user data and any Luma data
already on the phone. Android's `boot_a` and system partitions are preserved,
but Android user data is not. Keep the exact Google factory image; it is the
recovery path. Do not relock the bootloader.

**Not release-ready:** kernel integration, a published Luma kernel tag, the
standalone postmarketOS ramdisk and a fresh full-install test are still pending.
The development phone's boot test is not qualification of an install set.

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

`fastboot flash userdata` erases Android user data and all existing Luma data. The
install never writes `boot_a` (Android), `modemst1`, `modemst2`, `fsg`, `fsc`, `persist`, `misc`,
`dtbo_a`, `dtbo_b`, `vbmeta_a`, `vbmeta_b`, `bootloader_a`, `bootloader_b`,
`super` or `frp`; do not add commands that do.

## Firmware

The first boot needs the operator-extracted firmware in the shared store
`/var/lib/luma/device-firmware`. The manifest lists the files and hashes;
extract them from the matching factory image, never from another build:

- `qcom/a650_sqe.fw`, `qcom/a650_gmu.bin`, `qcom/a620_zap.*` — GPU;
- `qcom/sm7250/google/barbet/modem.*` — modem, which also boots the WCN3990;
- `wlanmdsp.mbn`, `bdwlan.bin`,
  `ath10k/WCN3990/hw1.0/{board-2.bin,firmware-5.bin}` — WLAN;
- `qcom/sm7250/google/barbet/adsp.*` — ADSP, in
  `/var/lib/luma/device-firmware-barbet` on this install;
- `rt5514p_dsp_fw1.bin` … `rt5514p_dsp_fw4.bin` — RT5514P microphone DSP,
  accepted from either store.

Wi-Fi needs the modem and WLAN files in the shared store because the barbet
service starts the modem before the audio service stages the barbet store.

After the first boot, copy the tree to the phone (USB-NCM/SSH or the installer
path you normally use) and restart the audio service.

Wi-Fi uses the WCN3990 firmware above and the base image's `tqftpserv`.
`luma-barbet-wifi.service` serves the firmware over QRTR, starts the modem and
waits for the WLFW QRTR service before the audio service starts the ADSP.
The barbet `rmtfs.service` drop-in explicitly runs `/usr/bin/rmtfs -r -P -s`
and Wi-Fi requires that service before starting the modem. This selects the
read-only partition backend without an FP6 file-store wrapper, so rmtfs does
not write `modemst1`, `modemst2` or `fsg`. The development phone runs this
command; Wi-Fi association on a fresh install is still untested.

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
different factory build. Firmware names and hashes are recorded in
`firmware.manifest`; raw captures and private firmware are not in the repository.

## Verified development behavior and remaining limits

The development kernel is based on Luma's 7.2.15 SM7250 branch. Display and
touch work; Adreno 620 rendering and DRV2624 vibration have been confirmed
on the phone. The source-DTS image boots on the development phone and plays
speaker audio through the desktop sink.

Wi-Fi scanning works after WLFW registers; fresh-install association is not
qualified. Speaker playback works. The 32 kHz capture front end retains the
48 kHz primary TDM bus; microphone pitch needs revalidation on the 7.2 port.
The battery percentage is a linear voltage estimate, not coulomb-counted.
Earlier suspend tests exercised s2idle and USB recovery; drain and the final
kernel's suspend behavior remain unqualified.

SELinux enforcing, a clean full install and the stock `vbmeta_b` install
path remain untested. Calls, mobile data, cameras, Bluetooth, NFC and
fingerprint are not qualified. The device stays `experimental`.
