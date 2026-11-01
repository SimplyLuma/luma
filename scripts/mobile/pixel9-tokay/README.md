# Pixel 9 (tokay)

Luma on the Pixel 9 reuses the Pixel 9 Pro Fold (comet) stack in `scripts/mobile/pixel9-fold`
and `patches/linux-zumapro/comet-daily8`. linux-zumapro is tokay-based, so the Fold kernel
builds for tokay with `PROBE_DTB=zumapro-tokay`.

## Kernel

`luma/tokay-daily1` = `luma/comet-daily8-try` (961df0dcb) + `patches/linux-zumapro/tokay-daily1`:

- 0001: tokay USF sensor registry names (upstream listed komodo files).
- 0002: drop the bring-up block that kept PCIe0, cp-power, the CP SPMI bus and the modem disabled.
- 0003: panel: no reset in prepare, display-off only in disable (the panel never came back after screen-off).
- 0004: reserve the firmware-owned RAM the stock tokay DT reserves (seclog, debug-snapshot logs, ABL, GSA page, DSP/TPU, xHCI pool).
- 0005: arm64 luma boot: import the bootloader's runtime no-map carve-outs (`sec_dram`, `sec_pt`, `pkvm_guest_firmware`). Without it Linux used TrustZone memory as RAM: under memory pressure, corrupted reads and hard lockups (a 2.9 GB ostree apply died every time; passes at full speed with it). Shared code: the Fold needs it too.
- 0006-0009: panel experiments and their reverts (net: the 0003 driver).
- 0010: `google,dsc-comp-align = <4>` on `&dsim0`. The DSIM default of 8 expected 2 x 184 compressed pixels a line where the DECON sends 2 x 180: no frame ever completed and the panel kept the boot logo. This is what makes the screen work.

## Initramfs

Fold `fold-luma-initramfs-daily-v7` inputs with:

- tokay firmware in `/luma-fold/firmware` (not in Git): `brcm/brcmfmac4390b1-pcie.{bin,txt,clm_blob,txcap_blob}`,
  `google/zumapro/aoc.bin` and `cirrus/cs35l41-dsp1-spk-prot-tokay{.wmfw,.bin,-r.bin}` from the
  phone's vendor partition; `brcm/BCM.hcd` (`/vendor/firmware/brcm/BCM_200.hcd`) and
  `google/usf/tokay/**.reg` (`/vendor/etc/sensors/registry`) from the tokay vendor image.
- hooks 89/95/99 (fold touch, hinge gate, output cycle) and 86/87-survey removed.
- `initramfs-hooks/77-luma-tokay.sh`: masks luma-fold-gate / luma-fold-outputs (one panel, no
  hinge) and installs `luma-tokay-apn.py` (data-mode APN from the SIM's MCC/MNC via
  mobile-broadband-provider-info instead of the Fold's tello).
- deviceinfo name "Pixel 9" (USB NCM gadget).

`modem/luma-fold-modem.sh` derives the board id from the bootloader's `/chosen/plat` words on
non-Fold boards (tokay: platform 6, product 10, stage 6, rf_sub 1 → RF table rfid 425).

## Shared Luma shell (GNOME Shell)

`initramfs-hooks/89-luma-tokay-session.sh` (initramfs v2): GDM autologin, `/etc/luma-device-class` = mobile,
`LUMA_DEVICE_CLASS=handheld` in `/etc/environment.d`, mmsd-tng in the session, and the shared
`config/mobile/luma-shell-handheld.dconf` as `/etc/dconf/db/luma.d/10-luma-handheld` (copied into the
initramfs inputs as `luma-fold/luma-shell-handheld.dconf`): without it gsd-media-keys keeps the Power key
and its default action is suspend (masked), so Power never blanked the screen.

Kernel command line adds `exynosdrm.pkt_go=1`: DCS commands use the PKT_GO release, so display-off and
backlight writes reach the panel at screen-off (they timed out waiting for a command window that never
opens once frames stop). The Mobile Data toggle is GNOME Shell patch 0303 on main.

## Android

`build-tokay-v22.sh` adds `ANDROID_BINDER_IPC`, `ANDROID_BINDERFS`, `DMABUF_HEAPS`/`DMABUF_HEAPS_SYSTEM`
(Codec2 allocates from /dev/dma_heap; without it media playback fails with NO_MEMORY) and the container netfilter set in
`kernel/android-netfilter.list` (nf_tables, iptables-nft compat, NAT/masquerade v4/v6, bridge netfilter,
UNIX/PACKET/NETLINK_DIAG) with `DYNAMIC_DEBUG` off; the clobber guard still covers the zone.
Images: the Mesa 26.3 pair (luma-android-images 1.luma.11 payload) at `/usr/share/waydroid-extra/images`
with `luma-images.json`; `luma-android-initialize` selects renderD129 (panthor) over renderD128
(exynos-drm). Waydroid and luma-android-runtime need their SELinux modules (`waydroid.pp`,
`luma_android_runtime.cil`) so `luma-waydroid-container` enters `waydroid_t`; commits composed without
scriptlets take labels from the booted policy (`--selinux-policy=/`), not the base.

## Build and install

- `build-tokay-v22.sh` (luma-build, `OSTREE_PATH=/ostree/boot.N/luma/<bootcsum>/0`) →
  `candidates/luma-tokay-v22/boot.img`; `probe/build-tokay.sh` is `build-comet-probe.sh` with the model
  check set to "Pixel 9". Every `ostree admin deploy` flips boot.0/boot.1: build the boot image for the
  new path before rebooting.
- Root: Fold shell6 userdata image (`fastboot flash userdata`), then the alpha2 commit by static
  delta + `ostree admin deploy` + `root.a` (same as the Fold OTA path).
- `compose/compose-on-phone.sh`: layers RPMs from main's pins (CI aarch64 artifacts) on the base commit
  on the phone and deploys it; presets the app host units, adds the waydroid-container ordering drop-in
  and the Android image payload.
- `system/luma-tokay-ims-watchdog.*`: restarts ModemManager and the radio when IMS has not registered
  3 minutes after ModemManager started (monotonic time; calls mode only; at most 5 per boot).
- `system/50-luma-tokay-wifi-powersave.conf`: NetworkManager drop-in (`/etc/NetworkManager/conf.d`),
  Wi-Fi power save off; with it on, the BCM4390 stopped passing traffic for hours while associated.
- `stopgaps/`: `mobile-mode-nm-nudge.py` (data switch asks NetworkManager for the connection when it does
  not autoconnect; also in the initramfs modem input), `daemon-phone-theme.py` and
  `daemon-gnome-lock.py` (prairie-core-apps 1.luma.126; fixed in 1.luma.127).
- `tokay-ssh.sh`: root SSH over the Pixel 9's own USB NCM link by IPv6 link-local, Wi-Fi fallback.

## Verified on hardware (luma-tokay-v20, gnome-shell 1.luma.147)

Standalone boot from boot_a, Mobile presentation, Wi-Fi (BCM4390), Bluetooth, AoC audio, S5400
modem: incoming and outgoing calls (answer from the lock screen), SMS and MMS out, eSIM mobile data
and calls/data mode switching. Prepared app host launches (Clock 0.6 s). Android: SurfaceFlinger
GLES Mesa Mali-G715 MC7 (Panfrost) 26.3, `/storage/emulated` mounted, network validated, one
system_server start, TikTok and X full screen from their launcher entries.

Not verified: SMS/MMS in, camera, suspend, GNSS, sensors. Display mode is 60 Hz only.
