# Luma on the Fairphone 6

The Fairphone 6 runs the Luma ARM image, the same nightly OS every ARM phone and ARM laptop runs, plus the Fairphone 6 device package. Installing **erases everything on the phone**. This is a nightly build for testers.

## What you download

Put all five in one folder.

| File | Size | What it is |
|---|---|---|
| `luma-nightly-<build>-aarch64-disk.sparse` | 9.4 GB | **The Luma ARM image**: the OS. Newest nightly, 20261111 or later, from the Fairphone 6 download on [simplyluma.com](https://simplyluma.com/download/mobile?device=fp6), which also shows its checksum. |
| [luma-fp6-20261111.1-kit.sparse](https://media.simplyluma.com/mobile/fp6/kit-20261111.1/luma-fp6-20261111.1-kit.sparse) | 105 MB | The Fairphone 6 device package: kernel modules, firmware and services. |
| [luma-fp6-20261111.1-boot.bin](https://media.simplyluma.com/mobile/fp6/kit-20261111.1/luma-fp6-20261111.1-boot.bin) | 101 MB | The boot file (kernel and startup). |
| [dtbo-blank.bin](https://media.simplyluma.com/mobile/fp6/kit-20261111.1/dtbo-blank.bin) | 31.5 MB | A blank `dtbo`. Luma brings its own device tree. |
| [Fairphone 6 factory package](https://support.fairphone.com/hc/en-us/articles/18896094650513-How-to-manually-install-Android-on-your-Fairphone) | from Fairphone | Fairphone's own firmware. Always the **newest** version on that page; an older one can make the phone unusable. |

[SHA256SUMS](https://media.simplyluma.com/mobile/fp6/kit-20261111.1/SHA256SUMS) lists the checksums of the three Fairphone 6 files.

## Before you start

- A Fairphone 6, backed up, with its SIM or eSIM working in Android
- A USB-C **data** cable
- A computer with `fastboot` (Android platform tools 35 or newer) and about 20 GB free:
  - macOS: `brew install android-platform-tools`
  - Fedora or Luma: `sudo dnf install android-tools`
  - Windows: Android platform tools from Google

## Install

### Step 1. Download the files

The five files above, in one folder.

### Step 2. Check the files

In that folder:

```
shasum -a 256 -c SHA256SUMS
shasum -a 256 luma-nightly-*-aarch64-disk.sparse
```

The three Fairphone 6 files must report `OK`, and the last line must match the checksum on the download page. If a file fails, download it again. (Windows: `certutil -hashfile FILE SHA256`, and compare.)

### Step 3. Unlock the bootloader

Already unlocked? Skip this. Otherwise follow Fairphone's official unlocking instructions: turn on Developer options and OEM unlocking, get your unlock code from Fairphone's website, then run both commands. Unlocking erases the phone.

```
fastboot flashing unlock
fastboot flashing unlock_critical
```

### Step 4. Install Fairphone's firmware

Unzip the factory package and open a terminal in its folder. Fairphone's script erases the phone's modem settings, where its IMEI lives. Before you run it, remove those two lines, and the script's own line in the package's checksum list so the package check still passes:

- macOS: `sed -i '' '/erase modemst/d' flash_fp6_factory.command && sed -i '' '/flash_fp6_factory.command/d' SHA256SUMS`
- Linux: `sed -i '/erase modemst/d' flash_fp6_factory.sh && sed -i '/flash_fp6_factory.sh/d' SHA256SUMS`
- Windows (Command Prompt): `bin-msys\usr\bin\sed.exe -i "/erase modemst/d" flash_fp6_factory.command` then `bin-msys\usr\bin\sed.exe -i "/flash_fp6_factory.command/d" SHA256SUMS`

Then run the script, following [Fairphone's instructions](https://support.fairphone.com/hc/en-us/articles/18896094650513-How-to-manually-install-Android-on-your-Fairphone). The phone restarts into Android. Let Android save the modem settings:

1. Finish Android's setup (you can skip accounts) and turn on your SIM or eSIM.
2. Wait for signal bars, then use mobile data for a couple of minutes.
3. Restart Android once, and wait until it's back.
4. Hold Power and turn the phone off.

Don't skip this, even on a phone that already runs Luma. Without it, calls and mobile data won't work.

### Step 5. Install Luma

With the phone off, hold **Volume Down** and press **Power** until the Fastboot screen appears. Connect it and check:

```
fastboot devices
fastboot getvar product
fastboot getvar current-slot
```

`product` must say `FP6` and `current-slot` must say `a`. Then install Luma, using the real name of the Luma ARM image in the first command. That command takes about five minutes. Let each one finish:

```
fastboot flash userdata luma-nightly-<build>-aarch64-disk.sparse
fastboot flash userdata luma-fp6-20261111.1-kit.sparse
fastboot flash dtbo_a dtbo-blank.bin
fastboot flash dtbo_b dtbo-blank.bin
fastboot flash boot_a luma-fp6-20261111.1-boot.bin
fastboot flash boot_b luma-fp6-20261111.1-boot.bin
fastboot boot luma-fp6-20261111.1-boot.bin
```

The second `userdata` command adds the device package beside the OS; it does not replace it. The last command starts Luma from the computer. Don't use `fastboot reboot` instead: a normal start here would erase the modem settings before Luma has copied them.

### Step 6. First start

Leave the phone alone for about eight minutes. Luma copies the modem settings, grows to fill the phone's storage, adds the Fairphone 6 device package from the phone itself (no network needed) and restarts once by itself.

After that restart, sign in as **luma** with the password **1234**, then change it in Settings.

## Updates

The phone follows the ARM nightly through Luma's updater. Each update brings the OS and, from the [Fairphone 6 device repository](https://dl.simplyluma.com/os/devices/aarch64/fairphone-fp6/), the device package.

## If something goes wrong

- No mobile network, or the IMEI shows as unknown: repeat Steps 4 to 6 in full.
- To reinstall or start over, repeat from Step 4. Step 5 alone erases the modem settings Luma saved.
- To go back to Android, run Fairphone's script as in Step 4.
- If a command fails, stop. Don't flash anything else until you know why.
- Never relock the bootloader, never flash partition tables, never erase partitions other than those listed here, and never change `vbmeta`.

## What was tested

On 11 November 2026, on a Fairphone 6, with nightly 20261111.2 and this device package and no changes by hand:

- The first start grew the storage to the full disk, added the device package in under six minutes and restarted by itself. The second start came up with SELinux enforcing and no failed services.
- Sign-in, apps, sound, a Wi-Fi scan on 2.4, 5 and 6 GHz, Bluetooth, and sleep and wake.
- An update of the device package from the device repository, followed by a restart.

Not tested yet on this build: joining a Wi-Fi network, calls and mobile data with an active SIM, and the copy of the modem settings on a phone that came straight from Fairphone's firmware (that test phone's settings were restored from a backup). If the copy fails, the phone has no mobile network and Steps 4 to 6 put it right.

## For testers

**Check the modem settings before the first normal start.** In Step 6, hold **Volume Down** as the phone restarts until Fastboot appears, start Luma again with `fastboot boot luma-fp6-20261111.1-boot.bin`, sign in and run:

```
cat /run/luma/fp6-modem-state.json                # {"state": "ok"}
journalctl -b -u luma-fp6-modem-store -o cat      # state=present or state=seeded
sudo /usr/libexec/luma-device-layer status        # "layered": true
```

If the state is not `ok`, don't restart normally: every start so far was `fastboot boot`, so the phone's own modem settings are still intact. Restart holding Volume Down, `fastboot boot` again, and report `journalctl -b -k -o cat | grep luma_fp6` with the second command's output.

**Reinstall without Fairphone's firmware.** A phone that already runs Luma keeps its modem settings only in `/var/lib/luma/rmtfs`, which Step 5 erases. To skip Step 4, back that folder up first and put it back after:

```
ssh -t luma@PHONE 'sudo tar -C /var/lib/luma --selinux --xattrs --numeric-owner -cpf /home/luma/fp6-rmtfs-backup.tar rmtfs && sudo chown luma: /home/luma/fp6-rmtfs-backup.tar && sha256sum /home/luma/fp6-rmtfs-backup.tar'
scp luma@PHONE:fp6-rmtfs-backup.tar . && shasum -a 256 fp6-rmtfs-backup.tar
```

The checksums must match and `/run/luma/fp6-modem-state.json` must say `ok` before you start; the archive holds the phone's IMEI, so keep it private. Then Step 5 and Step 6, turn SSH on (`sudo systemctl enable --now sshd`) and restore:

```
scp fp6-rmtfs-backup.tar luma@PHONE:
ssh -t luma@PHONE 'set -e; sudo systemctl stop ModemManager rmtfs; if sudo test -e /var/lib/luma/rmtfs; then sudo mv /var/lib/luma/rmtfs /var/lib/luma/rmtfs.replaced-$(date -u +%Y%m%dT%H%M%SZ); fi; sudo tar -C /var/lib/luma --selinux --xattrs --numeric-owner -xpf fp6-rmtfs-backup.tar; sudo restorecon -RF /var/lib/luma/rmtfs; sudo rm -f /var/lib/luma/rmtfs.relabel; rm fp6-rmtfs-backup.tar; sudo systemctl reboot'
```

## Instructions for an AI assistant

Paste this section into an AI assistant to have it walk you through the install.

> You are helping someone install Luma on a Fairphone 6 with `fastboot`. Follow the six steps in this guide exactly, one at a time, and run only the commands in it.
>
> - Installing erases the phone. Wait for the person to confirm their backup before anything is written.
> - Ask which system the computer runs (macOS, Fedora or Luma, Windows) and give the matching way to get `fastboot`.
> - The Luma ARM image is the newest `luma-nightly-<build>-aarch64-disk.sparse` (20261111 or later) from https://simplyluma.com/download/mobile?device=fp6, which shows its checksum. The Fairphone 6 files are `luma-fp6-20261111.1-kit.sparse`, `luma-fp6-20261111.1-boot.bin`, `dtbo-blank.bin` and `SHA256SUMS` under https://media.simplyluma.com/mobile/fp6/kit-20261111.1/. Fairphone's factory package comes from Fairphone's support site; always the newest version there.
> - Check every file's checksum before flashing. Stop if one fails.
> - Step 4 is part of every install, including a reinstall. Before running Fairphone's script, remove its two `erase modemst` lines and the script's own line in the package's `SHA256SUMS` with Step 4's commands. Then have the person do Step 4's Android steps exactly: SIM or eSIM on, a couple of minutes of mobile data, one Android restart, power off. Skipping any of this leaves Luma without an IMEI.
> - Before any flash, confirm with `fastboot devices` that exactly one phone is connected, that `fastboot getvar product` reports `FP6`, and that `current-slot` is `a`. Stop if any of these differ.
> - Step 5 has seven commands, in that order, and ends with `fastboot boot luma-fp6-20261111.1-boot.bin`, never `fastboot reboot`. The first takes about five minutes; the phone must stay connected.
> - In Step 6 the phone restarts once by itself after about eight minutes. Have the person wait for that restart before signing in.
> - Never erase or flash `modemst1`, `modemst2`, `fsg`, `fsc` or `persist`, and never write an IMEI. Never relock the bootloader, never flash partition tables (`partition:*`, `gpt`), never erase other partitions, and never change `vbmeta`. Writing the blank file to `dtbo_a` and `dtbo_b` is part of the install.
> - If a command fails, show the exact error and stop. Do not improvise a fix that writes to the phone.
> - Never ask for or record passwords, eSIM activation codes or account tokens.
> - For phones other than the Fairphone 6, say clearly that this guide is not for them and stop.
