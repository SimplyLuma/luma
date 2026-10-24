# Luma on the Unihertz Titan 2 Elite

Luma runs on the Titan 2 Elite from a microSD card. The phone keeps Android on
its internal storage and on boot slot b; Luma's kernel goes into boot slot a.
This guide is for people comfortable with a terminal.

This is the **Titan 2 Elite** with the MediaTek MT6878. Other Titan models use
different chips and will not start this kernel.

## What Luma never touches

Luma writes only `boot_a`, and the boot-slot record in `misc`. Never flash,
erase or format any other partition. In particular, these hold the phone's own
radio calibration and identity (its IMEI) and cannot be recreated:

`nvram`, `nvdata`, `nvcfg`, `protect1`, `protect2`, `proinfo`

The bootloader also checks `dtbo_a`, `vbmeta_a` and `vendor_boot_a` and stops
if they are not Unihertz's own, so they stay as shipped. Luma reads the
`vendor` and `nvdata` partitions (read-only) on first boot to copy the Wi-Fi,
Bluetooth and speaker firmware. For the modem it reads `nvdata`, `nvcfg`,
`protect1`, `protect2`, `nvram`, `mcf_ota` and `modem` read-only and works from
its own copy.

## What you need

- A Titan 2 Elite with stock Android, and a USB-C data cable
- A microSD card of 16 GB or more; everything on it is erased
- A computer with `adb`, `fastboot` and `zstd`
- From https://dl.simplyluma.com/mobile/titan2e/alpha1/: `luma-titan2e-alpha1.zst` (the Luma system for the card),
  `boot.bin` (the kernel for `boot_a`) and `SHA256SUMS`

## Step 1. Check the files

```
shasum -a 256 -c SHA256SUMS
```

Every file must report `OK`. Never flash a boot image that fails this check:
the phone's `vbmeta` checks `boot_a`, and a boot image without Luma's signed
hash footer leaves the phone unable to start, and can block fastboot.

## Step 2. Unlock the bootloader

Unlocking erases the phone. In Android, open Settings → About phone and tap
Build number seven times, then turn on Developer options → OEM unlocking and
USB debugging. Connect the phone and run:

```
adb reboot bootloader
fastboot flashing unlock
```

The phone asks for confirmation for only a few seconds: press **Volume Up**.
Then run `fastboot reboot` and let Android start.

## Step 3. Write the microSD card

Find the card's device with `lsblk` (Linux) or `diskutil list` (macOS) before
and after inserting it; here it is `/dev/sdX`. Write the system to it:

```
zstd -dc luma-titan2e-alpha1.zst | sudo dd of=/dev/sdX bs=4M conv=fsync status=progress
```

Put the card in the phone. On first start, Luma grows its partition to fill
the card.

## Step 4. Install the Luma kernel

With Android running and USB debugging on:

```
adb reboot bootloader
fastboot devices
fastboot getvar current-slot
```

The phone has no button combination for fastboot, so `adb reboot bootloader`
from Android is the way in. `current-slot` must say `b`: Android runs from slot
b and Luma goes into slot a. If it says `a`, stop here; installing on such a
phone would replace Android's own boot image. Then:

```
fastboot flash boot_a boot.bin
fastboot set_active a
fastboot reboot
```

## Step 5. First start

Luma starts from the card and shows the lock screen. Sign in as **luma** with
the PIN **1234**, then change it in Settings right away. Once the phone has
reached the lock screen, Luma marks slot a and its system as booted
successfully. On the first start, Luma copies the Wi-Fi, Bluetooth and speaker
firmware from the phone's own partitions. During the first start the phone
restarts once by itself to finish setting up the modem.

## Switching between Luma and Android

To start Android, run this in Luma's Terminal. It tells the bootloader to use
slot b next and restarts the phone:

```
sudo luma-titan2e-boot-android
```

To come back to Luma, start Android with USB debugging on and run:

```
adb reboot bootloader
fastboot set_active a
fastboot reboot
```

Check that both directions work on your phone before you rely on them.

## If something goes wrong

- Until Luma has started successfully once, a failed start counts against
  slot a, and the bootloader starts Android on slot b after a few attempts.
- If a Luma update fails to start twice, the previous system starts instead.
- Without the microSD card, the Luma kernel restarts the phone after 30
  seconds and tries again. Put the card back to start Luma, then switch to
  Android as above.
- Never flash or erase any partition other than `boot_a`, never relock the
  bootloader, and never change `vbmeta`, `dtbo` or `vendor_boot`.
