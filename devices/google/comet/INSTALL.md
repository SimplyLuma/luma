# Installing Luma on the Pixel 9 Pro Fold

**Experimental.** Bring-up builds for people who want to help with the port.
Expect missing features and test switches on the kernel command line. Mobile
data, calls, cameras, suspend and haptics are not finished.

Works today: both screens and touchscreens, fold and unfold, GPU, Wi-Fi,
Bluetooth, audio, charging, backlight, sensors, USB 3, GNSS.

## What you need

- A Pixel 9 Pro Fold (`comet`) with an unlockable bootloader.
- A computer with `fastboot` and a USB-C cable.
- A Luma mobile `userdata` image and a Fold boot image built for it. Build the
  boot image as described in
  [scripts/mobile/pixel9-fold/README.md](../../../scripts/mobile/pixel9-fold/README.md);
  it carries firmware from Google's factory image for your phone and is not
  published.
- Google's factory image for the phone, `cp3a.261005.005`, to go back to
  Android.

## Install

Unlocking erases the phone.

1. In Android, enable Developer options, then turn on OEM unlocking.
2. Reboot to the bootloader (hold Power and Volume Down) and unlock:
   ```sh
   fastboot flashing unlock
   ```
3. Write the Luma image and the boot image, and boot slot a:
   ```sh
   fastboot flash userdata luma-mobile-userdata.sparse
   fastboot flash boot_a boot.img
   fastboot --set-active=a
   fastboot reboot
   ```

Luma writes only `boot_a` and `userdata`. The radio calibration and identity
partitions (`efs`, `efs_backup`, `persist`, `modem_userdata`), the modem,
bootloader, `vbmeta`, `dtbo`, `vendor_boot`, `init_boot` and `super`
partitions are never written, so the factory image restores the phone fully.

To try a boot image without installing it, use `fastboot boot boot.img`. If a
boot fails, the phone falls back to the installed `boot_a`; hold Power and
Volume Down to get back to the bootloader.

## Back to Android

Flash Google's factory image for the phone with its `flash-all` script, then
lock the bootloader from the bootloader menu if you want to.
