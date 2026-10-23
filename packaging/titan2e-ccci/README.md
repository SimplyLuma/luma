# Titan 2 Elite cellular modem (MediaTek CCCI)

The MT6878 modem talks to the AP over CCCI (CCIF mailboxes and shared memory for
control, DPMAIF for data). There is no mainline driver; the MediaTek vendor drivers
are built out of tree against the Titan kernel:

| Module | Source |
| --- | --- |
| `titan_md_ovl.ko` | `luma/`: applies `luma/titan-md.dtso` (modem DT nodes) as an overlay |
| `ccci_util_lib.ko` | `drivers/misc/mediatek/ccci_util` |
| `ccmni.ko` | `drivers/misc/mediatek/ccmni` |
| `ccci_auxadc.ko`, `ccci_md_all.ko` | `drivers/misc/mediatek/eccci` |
| `ccci_ccif.ko`, `ccci_dpmaif.ko` | `drivers/misc/mediatek/eccci/hif` |

Upstream source (GPL-2.0, from the MT6878 GPL releases):
https://github.com/motorola-mt6878/kernel_device_modules @ `6586afaa0622648a6e740fb4a83f4b8057346bc2`.
The files the build uses are vendored unmodified in `kernel_device_modules/`
(see its README), with their SHA-256 in `kernel_device_modules.sha256`;
`build-titan2e-ccci-modules.sh` builds from that copy, without network access.

`titan2e-ccci.patch` ports them to Linux 7.2 and the Titan:

- API updates (class_create, syscore, hrtimer_setup, timers, sysctl, GRO, page pool,
  of_gpio, strncpy), collected in `compat/luma_compat.h`.
- LK's `ccci,modem_info_v2` record is rebuilt from the `ccci_tag_mem` reserved region
  (the kernel boots with its own DT, so LK's property is not passed on). The tag area
  stays mapped; args point into it.
- MD MTCMOS sequence (SPM `PWR_CON` 0xe00, external buck isolation, bus protection) for
  kernels without an MT6878 scpsys driver.
- CCIF register access without clock handles (`mediatek,ccif-clk-free-run`); the CCIF
  clocks are left running by LK. Without it every CCIF access is skipped and the first
  modem interrupt storms. An IRQ-storm limiter in the CCIF ISR stays in as a guard.
- Modem GPIO lookups (`gpio_usage_mapping`) resolved on the MT6878 pinctrl; the overlay
  carries the stock `md_attr_node` and `gpio_usage_mapping` values that LK adds to the
  stock DT.
- UFS hold for the modem's reference-clock calibration (`IPC_RPC_AFC_UFC_IO_BLOCK_OP`)
  on the mainline UFS driver (`ufs_hold` parameter, auto-release after 3 s).
- Start ioctl accepted from `luma-modemd`; no-`atag,boot` normal boot.
- The modem drivers use the board's own `infracfg_ao` and `topckgen` syscons
  (`syscon@10001000`, `syscon@10000000`): the phandle lookup falls back to the compatible,
  because the board DT has no `__symbols__` for the overlay to reference. The overlay only adds
  nodes the board DT does not have; these modem nodes belong in the kernel DT series eventually.
- `ccci_auxadc` reports the board's `mediatek,md-adc-channel` (the vendor AUXADC channel
  number the modem expects) instead of the IIO channel index. The modem reads its RF hardware
  ID through it (MT6363 VIN1, pull-up open; kernel patches 0037-0039).
- Bring-up parameters of `ccci_md_all`: `md_dbg_step_ms`, `md_skip_mtcmos`, `md_no_go`,
  `md_dvfsrc_start`, `ufs_hold`; `ccci_ccif`/`ccci_dpmaif`: `ccci_traffic_dump`.

`0002-remove-sysctl-sentinels.patch` completes the counted sysctl API port:
`register_sysctl()` validates all array entries, so the obsolete empty
terminators prevented CCMNI network-device initialization on Linux 7.2.
`0003-parent-ccmni-to-modem.patch` associates these network devices with the
same native modem parent as the WWAN AT port, using `SET_NETDEV_DEV` before
registration. The builder applies both patches after the base port. Physical qualification
and remaining service gates are recorded in
[`titan2e-native-esim.md`](../../docs/research/titan2e-native-esim.md).

Build: `scripts/packages/build-titan2e-kernel.sh`, then
`scripts/packages/build-titan2e-ccci-modules.sh` (needs `dtc`), which installs the modules
under `usr/lib/modules/<release>/extra/titan2e-ccci/`. Unloading `ccci_md_all` while the
modem has been started is not supported.

## Host side (luma-titan2e-platform)

- `luma-titan2e-modem-fs` seeds `/var/lib/luma-modem/fs` once from read-only loop mounts of
  nvdata (`md`, `md_cmn`), protect1/protect2 (`md`), nvcfg and vendor (`etc/md`). The
  partitions are never written.
- `luma-titan2e-modem-up` loads the modules; `luma-modemd` starts the modem and serves:
  - the CCCI FS protocol on `/dev/ccci_fs` (MediaTek FS API: open/read/write/seek/find,
    compound writes), on the working copy only. Drive map: Z: `md`, X: `protect_f/md`,
    Y: `protect_s/md`, V: `md_cmn`, S: `nvcfg`, R: `vendor_etc_md`. Responses carry
    `op_id | 0xFFFF0000`, fragments are 3456 bytes plus a 20-byte header.
  - the userspace RPC ops (`QUERY_AP_SYS_PROPERTY`, `SAR_TABLE_IDX_QUERY`,
    `AMMS_DRDI_CONTROL`): DRDI sets are read from the `md1drdi` image in the modem
    partition into the 64 KiB DRDI shared memory (mmap of `/dev/ccci_rpc`).
  - Compound writes/reads (`CMPT_WRITE` 0x1024, `CMPT_READ` 0x1026) follow the stock
    `ccci_fsd` reply layouts: `{done_map, error}`, handle, bytes written; and
    `{done_map, error}`, file size, bytes read, data, handle.
  - Writes into the protected area (`X:`, `Y:`) are logged with path, offset and size to
    `/var/log/luma-modem-protect.log`.
  - IMEI files (`Z:\NVRAM\NVD_IMEI`) are never created, truncated, deleted or changed: a
    rewrite of identical bytes is acknowledged without writing, anything else is refused.
    Before the modem starts and 60 s after (then every 10 min) they are compared with the
    read-only originals (`luma-titan2e-modem-fs --check-imei`); on a difference the file is
    restored from the original, the modem is stopped and luma-modemd exits.
  - `FS_CCCI_OP_RESTORE` (restore from the nvram partition's backup region) is acknowledged
    only when the file is byte-identical to a copy in that region; nothing is written.
    `BIN_REGION_ACCESS` is refused.
  - `FS_Move` honours its flags (0x1 copy, 0x10000 overwrite): the modem keeps `Y:` as a copy
    of `X:`. `T:` is a read-only copy of the `mcf_ota` partition (modem configuration OTA files).
  - Before each modem start the CCB control block (`/dev/ccci_ccb_ctrl`, modem debug-log
    buffers) is filled as stock `ccci_mdinit` does (`ccci_ccb_init_users`): one 64-byte record per
    `ccb_configs[]` buffer with DL/UL page and buffer sizes and the `0xEEFF0011` tail. The kernel
    keeps the block across AP reboots (`SMEM_NO_CLR_FIRST`); an empty or stale table makes the
    modem assert in `ccci_shm_bm.c`.
  - Once the modem is ready, luma-modemd sends the MIPC requests stock's RIL makes at boot on
  `/dev/ttyCMIPC0`: `SYS_MULTI_SIM_CONFIG` = 2 (dual SIM), `SYS_SET_DSBP` = 3,
  `SYS_SILENT_REBOOT` = 0, RCS off (`AT+EIMSRCS=0,0`), and per SIM protocol
  `NW_SET_RADIO_CAPABILITY`, `NW_SET_RADIO_STATE` off and `SIM_GET_RESET`. The last three start
  the modem's SIM polling; without them SIM2 is never powered.
  - Indications arrive only on `/dev/ttyCMIPC1`, for ids registered there with
  `INTERNAL_REGISTER_IND` (0x0307). luma-modemd watches registration, rejects and IMS on the
  subscription's protocol. The SIP stack runs in the modem: once registered, luma-modemd sends
  `IMS_CFG_ADDITION_SERVICE` and `IMS_SET_CONFIG` (VoLTE, ViLTE, SMS over IMS, IMS; no Wi-Fi
  calling) and answers `IMS_PDN_IND` with `DATA_ACT_CALL` for APN `ims`. After 60 s without
  registration it sets the initial-attach APN again and restarts the radio. `LUMA_MODEM_IMS=0` in
  `/etc/default/luma-modem` disables IMS.
- AMMS DRDI copies follow stock `ccci_rpcd` (every COPY request is served; status 0xff only
    when nothing was copied since INIT).
- `luma-titan2e-modem.service` runs both. It is not enabled by the preset while the modem
  does not complete its NVRAM init.
- The AT channel (`ttyC0`, `CCCI_UART2`) is also registered as a WWAN AT port
  (`/dev/wwan0at0`): ModemManager probes the kernel WWAN framework, not CCCI char nodes
  (subsystem `ccci_node`). The char node and the WWAN port exclude each other.
- `78-luma-titan2e-modem.rules`: ModemManager probes the WWAN AT port as a platform modem and
  groups `ccmni*` with it (`ID_MM_PHYSDEV_UID`).

## Status

The modem boots (`0x544300F1`), completes HS1/runtime data, RF variant detection and the
DRDI copy, and runs its NVRAM init through `luma-modemd` on the stock path (backup restore of
`NVD_IMEI\NV01_000`, compound reads and writes).

The modem image must match the NVRAM: the NVRAM is written by the last stock modem
(`modem_b`, MOLY `V51.2.P7` on the test phone) while LK loads the boot slot's `modem_a`
(`V51.2.P2`). The older image takes its OTA path and deletes/re-initialises NVRAM files
(calibration included) in the working copy. The boot slot needs the newest modem image.

## eSIM (SIM2)

The earlier bring-up observations below are historical. Native platform35 now
selects the soldered eUICC, manages it through the shared eSIM broker and retains
one enabled carrier profile across AP reboots. The same Settings manual/QR UI
is used on Titan and Fairphone. See [current evidence and open gates](../../docs/research/titan2e-native-esim.md). The r66 CCMNI module exposes21 network
interfaces under the real modem parent and ModemManager groups them with its
AT port. Carrier registration, SMS, packet transfer and voice remain unaccepted.

### Historical electrical bring-up (superseded)

SIM2 is routed to the tray or to the soldered eUICC by GPIO184 (`mediatek,sim2-switch`;
stock `esim_switch.ko`: high = eSIM, low = tray, GPIO only). `luma-modemd` leaves GPIO184 at
its boot default (tray).

Stock: the modem is up and polls the empty slots every ~30 s. At the first poll after the switch
it sets VSIM2 to 1.8 V, enables it, and the eUICC answers (`+ESIMS: 1,14`,
`SIM_EUICC_SLOTS_STATUS_IND`); the RIL sends nothing in between.

Luma: the same polls with the same rail sequence (VSIM2 at 1.8 V first, six tries alternating
1.8/3.0 V per poll), and the SIM2 pads are driven (CLK on GPIO86, RST rising on GPIO87, IO on
GPIO88 pulled high, sampled through the GPIO input register), but the eUICC never sends an ATR
(`+ESIMS: 0,1`). This holds with the eSIM selected before the modem starts and with the stock
order (modem up on the tray, switch at runtime while it polls).

Compared with stock and found equal:

- All 196 SoC pins (mode, direction, output, input, drive, pulls) apart from drivers unrelated
  to SIM; the stock kernel claims no eSIM line other than GPIO184. MT6363 GPIOs.
- CCCI runtime data: SIM hot-plug EINT attributes (EINT 0/1, level low, debounce 10000,
  src-pin 1/2, dedicated 0, socket type 0) and `GPIO_SIM*` numbers; the modem's queries.
- MIPC boot sequence and confirms, `SYS_GET_MAPPING`, `SBP_SIM_EUICC_SUPPORT` = 1, `ESIMMAP` 1;
  modem image and `mcf_ota` identical on both slots; modem NV reseeded from the partitions as
  stock last wrote them.
- MT6369 VSIM1/VSIM2 control and voltage (no LDO over-current events), MT6363, MT6319,
  MT6375 and MT6685 rails and clock buffers.

Ruled out: MT6363 VTREF18 (stock `regulator-always-on`): enabled at stock's 1.8 V, still no ATR.

The remaining difference is past the SoC pads (switch or eUICC side) and not visible from
software. Modem-side SIM traces need MediaTek's log decoder (ELT) and the MDDB in `md1img`.

Current remaining gates:

- Reliable registration, SMS and voice through native ModemManager (`wwan0at0`).
- Data: the modem activates PDNs with MediaTek AT commands (`AT+EAPNACT`, `AT+EPDN`) and
  delivers them on `ccmniN`; ModemManager needs a plugin that runs those and reports a
  static IP bearer on the ccmni port.
- Call audio (speech path through the AFE), MT6878 infra_ao clock and MT6363 regulator
  drivers (the modem runs on LK's clock and rail setup).
