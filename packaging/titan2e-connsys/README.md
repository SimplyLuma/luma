# Titan 2 Elite Wi-Fi and Bluetooth (MediaTek CONNSYS)

The MT6878 connectivity subsystem (MT6637 A-die) has no mainline driver. The
MediaTek vendor drivers are built out of tree against the Titan kernel:

| Module | Source |
| --- | --- |
| `connadp.ko` | `kernel_device_modules` `drivers/misc/mediatek/connectivity`, plus platform shims |
| `conninfra.ko` | `vendor_mediatek_kernel_modules` `connectivity/conninfra` |
| `wmt_chrdev_wifi_connac2.ko` | `connectivity/wlan/adaptor` |
| `wlan_drv_gen4m_6878.ko` | `connectivity/wlan/core/gen4m` |
| `bt_drv_6878.ko` | `connectivity/bt/mt66xx` (6878, BEIF) |

Upstream sources (GPL-2.0, from the MT6878 GPL releases):

- https://github.com/motorola-mt6878/vendor_mediatek_kernel_modules @ `1c51f57c78be8acb3c51bc1911fffcd7b5c90184`
- https://github.com/motorola-mt6878/kernel_device_modules @ `6586afaa0622648a6e740fb4a83f4b8057346bc2`

The directories the build uses are vendored complete and unmodified, symlinks
as they are: `connectivity/{bt,common,conninfra,wlan}` in
`vendor_mediatek_kernel_modules/`, and `drivers/misc/mediatek/{connectivity,include,power_throttling}`,
`drivers/misc/mediatek/{Kconfig,Makefile}` and `drivers/gpu/drm/mediatek/` in
`kernel_device_modules/`. Their SHA-256 are in the matching `.sha256` files and
their symlinks in the `.links` files. They keep their upstream GPL-2.0 notices
(`LICENSES/GPL-2.0.txt`). The build checks them and needs no network access.

Left out: eight binary data files no Titan module builds or loads, the Bluetooth
power tables and debug/config tables for other chips:
`connectivity/bt/linux_v2/bt_power_table/btPowerTable.dat`,
`connectivity/bt/linux_v2/cfg/woble_setting_7961.bin`,
`connectivity/bt/linux_v2/debug_sop_file/{sdio,usb}_debug_{7902,7961}.bin`,
`connectivity/bt/mt66xx/bt_power_table/btPowerTable.dat` and
`connectivity/bt/mt66xx/cfg_example/woble_setting_7961.bin`.
`connectivity/wlan/core/gen4m/ext` points to `../gen4m_ext/`, which the
upstream repository does not contain either.

`titan2e-connsys.patch` ports them to Linux 7.2 and the Titan:

- API updates (cfg80211 callbacks, platform remove, timers, alarms, class_create, strlcpy/strncpy),
  collected in `compat.h` and `gl_cfg80211_luma.h`.
- CONN power domain sequence (SPM and bus protection) for kernels without an MT6878 scpsys driver.
- MT6363/MT6369 connsys LDOs as regulators on the existing SPMI regmaps; MT6685 clock buffer PMIC
  reached over SPMI.
- Stub implementations of vendor services that are absent upstream (display notifier, low-battery
  throttling, ConnFem without front-end data).
- Bluetooth registers an `hci_dev` for BlueZ, takes its BD address through `set_bdaddr`
  (vendor command 0xfc1a).
- Fixes: connv3 unload, A-die selection override, EMI base and Wi-Fi DMA pool overrides.

Build: `scripts/packages/build-titan2e-kernel.sh`, then
`scripts/packages/build-titan2e-connsys-modules.sh`, which builds against that kernel's `kbuild/`
tree (or a built kernel tree given as its first argument) and installs the modules under
`usr/lib/modules/<release>/extra/titan2e-connsys/`. `luma-titan2e-connsys.service`
(luma-titan2e-platform) loads them at boot.

## Firmware and calibration data

None of it is in this repository. LK loads the Wi-Fi, BT and GNSS RAM code from the
`connsys_wifi`, `connsys_bt` and `connsys_gnss` partitions into the consys EMI carve-out on every
boot. On first boot `luma-titan2e-connsys-fw` copies the rest from the phone's own Android
partitions (vendor inside super, nvdata; read-only loop mounts) to
`/var/lib/titan2e-connsys/firmware`:

| File | Origin |
| --- | --- |
| `soc7_0_ram_bt_1_1_hdr.bin`, `soc7_0_ram_bt_1a_1_hdr.bin`, `soc7_0_ram_bt_1b_1_hdr.bin` | vendor `/firmware` |
| `soc7_0_ram_mcu_1_1_hdr.bin`, `soc7_0_ram_mcu_1a_1_hdr.bin`, `soc7_0_ram_mcu_1b_1_hdr.bin` | vendor `/firmware` |
| `wifi.cfg`, `conninfra.cfg`, `BT_FW.cfg` | vendor `/firmware` |
| `WIFI` (Wi-Fi calibration and MAC address) | nvdata `/APCFG/APRDEB/WIFI` (per device) |
| `BT_Addr` (BD address) | nvdata `/APCFG/APRDEB/BT_Addr` (per device) |
