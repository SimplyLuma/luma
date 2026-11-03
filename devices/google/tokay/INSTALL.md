# Luma Mobile Alpha 1 for Pixel 9

Luma Mobile is the same Luma operating system as the desktop, running on the Google Pixel 9. Alpha 1 is an early release for people comfortable using a terminal. Installing it **erases everything on the phone**.

## What you download

| | File | Size | What it is |
|---|---|---|---|
| 1 | [luma-pixel9-alpha1-userdata.sparse.zst](https://simplyluma.com/get/mobile/pixel9/alpha1/system) | 3.4 GB | **The complete Luma system**: the OS, apps, Android app support and settings, compressed. |
| 2 | [luma-pixel9-alpha1-boot.bin](https://simplyluma.com/get/mobile/pixel9/alpha1/boot) | 64 MB | The Luma boot file (kernel and startup). |

Also download [SHA256SUMS](https://simplyluma.com/get/mobile/pixel9/alpha1/sums) to check that the files arrived intact.

## Before you start

- A Google Pixel 9 (`tokay`, not the Pro, Pro XL or Pro Fold) that can be unlocked. Carrier-locked phones usually can't.
- A USB-C **data** cable and a computer with `fastboot` (Android platform tools, version 35 or newer) and `zstd`:
  - macOS: `brew install android-platform-tools zstd`
  - Fedora or Luma: `sudo dnf install android-tools zstd`
  - Windows: Android platform tools from Google, and `zstd` from its GitHub releases
- About 15 GB of free space on the computer
- A backup of everything on the phone

Alpha 1 was tested on a Pixel 9 with bootloader `ripcurrentpro-17.0-15739203` and radio `g5400c-260728-260806-B-16025591`.

## Install

### Step 1. Download and check the files

Download the system file (1), the boot file (2) and `SHA256SUMS` into one folder. Open a terminal there and run:

```
shasum -a 256 -c SHA256SUMS
zstd -d luma-pixel9-alpha1-userdata.sparse.zst -o luma-pixel9-alpha1-userdata.sparse
```

Both files must report `OK`.

### Step 2. Unlock the bootloader

In Android, turn on Developer options (tap Build number seven times in Settings, About phone), then turn on **OEM unlocking**. Turn the phone off, hold **Volume Down** and press **Power** until the Fastboot screen appears, connect it to the computer and run:

```
fastboot flashing unlock
```

Confirm on the phone with the volume and power buttons. Unlocking erases the phone.

### Step 3. Install Luma

On the Fastboot screen, check that it's the right phone:

```
fastboot devices
fastboot getvar product
```

`product` must say `tokay`. Then install Luma. The first command takes several minutes; let each one finish:

```
fastboot flash userdata luma-pixel9-alpha1-userdata.sparse
fastboot flash boot_a luma-pixel9-alpha1-boot.bin
fastboot --set-active=a
fastboot reboot
```

Luma writes only `userdata` and `boot_a`. The modem, radio calibration and identity partitions (`efs`, `efs_backup`, `persist`, `modem_userdata`), the bootloader, `vbmeta`, `dtbo`, `vendor_boot`, `init_boot` and `super` are never written, so Google's factory image restores the phone fully.

### Step 4. First start

The phone shows Google's unlocked-bootloader warning, then Luma. The first start takes a minute or two while Luma expands to fill the phone's storage. Luma signs in on its own; the lock screen password is **1234**. Change it in Settings right away.

Calls, texts and mobile data work with a physical SIM or an eSIM already on the phone. In Alpha 1 the modem carries calls or mobile data one at a time: turn on **Mobile Data** in Quick Settings for data, and off again for calls and texts. Android apps run alongside Luma's own; Google sign-in works through microG.

## If something goes wrong

- To start over, repeat Step 3. Reinstalling `userdata` erases the phone.
- To try the boot file without installing it, use `fastboot boot luma-pixel9-alpha1-boot.bin`.
- To go back to Android, flash [Google's factory image for the Pixel 9](https://developers.google.com/android/images#tokay) with its `flash-all` script, then lock the bootloader from the bootloader menu if you want to.
- Never flash partition tables, relock the bootloader while Luma is installed, or erase partitions other than those listed here.

## Instructions for an AI assistant

Paste this section into an AI assistant to have it walk you through the install.

> You are helping someone install Luma Mobile Alpha 1 on a Google Pixel 9 (`tokay`) with `fastboot`. Follow the four steps in this guide exactly, one at a time.
>
> - Ask which system the computer runs (macOS, Fedora or Luma, Windows) and give the matching command to install `fastboot` and `zstd`.
> - The files are `luma-pixel9-alpha1-userdata.sparse.zst` (https://simplyluma.com/get/mobile/pixel9/alpha1/system), `luma-pixel9-alpha1-boot.bin` (https://simplyluma.com/get/mobile/pixel9/alpha1/boot) and `SHA256SUMS` (https://simplyluma.com/get/mobile/pixel9/alpha1/sums). Save them under those file names.
> - Verify the downloads with `SHA256SUMS` before flashing. Stop if a checksum fails.
> - Before any flash, confirm with `fastboot devices` that exactly one phone is connected and that `fastboot getvar product` reports `tokay`. Stop if either differs. The Pixel 9 Pro, Pro XL and Pro Fold are different phones.
> - Explain that unlocking and flashing `userdata` erase the phone, and wait for the person to confirm their backup first.
> - Only run the commands in this guide. Never relock the bootloader while Luma is installed, never flash partition tables, never erase other partitions, and never change `vbmeta`.
> - If a command fails, show the exact error and stop. Do not improvise a fix that writes to the phone.
> - Never ask for or record passwords, eSIM activation codes or account tokens.
> - For phones other than the Pixel 9, say clearly that Luma does not support them yet and that any attempt is at the person's own risk.
