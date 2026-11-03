# Google Pixel 9 (tokay)

Luma on the Google Tensor G4 Pixel 9. It shares the Pixel 9 Pro Fold (`comet`) kernel and boot stack; the build, kernel series and device files are in [scripts/mobile/pixel9-tokay](../../../scripts/mobile/pixel9-tokay/README.md).

## Alpha 1

Build `alpha1`, OSTree commit `147cc551`. Install: [INSTALL.md](INSTALL.md); files at https://simplyluma.com/download/mobile.

Works: display and touch, tap to wake, GPU, Wi-Fi, Bluetooth, speaker, earpiece and microphones, calls (VoLTE) and texts with a SIM or eSIM, mobile data, eSIM download, charging, Android apps (with microG for Google sign-in and push).

Not yet: cameras, suspend (the screen sleeps, the system stays up), over-the-air updates for the Pixel channel.

## Building

- Kernel and boot image: `scripts/mobile/pixel9-tokay/build-tokay-alpha1.sh` (`OSTREE_PATH` from the image build).
- System commit: `compose/compose-release.sh` on a Pixel 9 running Luma (the base commit, the release RPMs, the Android image pair and `system/`).
- Install image: `compose/build-release-image.sh` (fresh `userdata`, user `luma`, password `1234`).

The boot image carries the Pixel 9 firmware from Google's factory image for this phone (Wi-Fi and Bluetooth, the AoC audio and sensor processor, the speaker amplifiers, the sensor registry); it is not in this repository.
