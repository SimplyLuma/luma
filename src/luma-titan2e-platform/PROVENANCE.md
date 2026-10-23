# Provenance: Titan 2 Elite modem and call userspace

Covers `luma-modemd`, `luma-titan2e-speech`, `luma-titan2e-modem-fs`,
`luma-titan2e-modem-up`, `luma-titan2e-esim`, the modem unit, udev rule and D-Bus
policy in `src/luma-titan2e-platform/`, and `packaging/titan2e-ccci/`.

## What is original

All userspace code listed above is original work under Apache-2.0. It reimplements, for
interoperability, the host services the MT6878 modem expects from the application processor:

- the CCCI FS protocol (the modem's NVRAM file store) on `/dev/ccci_fs`;
- the userspace CCCI RPC operations, including AMMS DRDI copies;
- the CCB control-block setup and boot data before each modem start;
- the MIPC requests, indications and incoming-call approval used at boot, for registration,
  IMS and packet data;
- the modem speech on/off/device control on `/dev/ccci_aud`.

The design is Luma's own. It serves files from a working copy, never writes the phone's
partitions, keeps IMEI files read-only with a compare/restore guard, logs every write to the
protected area, and exposes packet data to ModemManager over D-Bus.

## What was studied, and how

The protocols were determined from the stock firmware of a Titan 2 Elite:

- black-box observation: CCCI and MIPC traffic, Android logcat and CCCI dumps on the stock
  system, and stock-kernel cross tests with the Luma daemon;
- disassembly, with symbol and string listings, of the stock modem init/FS daemon, the CCCI
  RPC daemon and the audio HAL speech library, to read message IDs, field layouts and reply
  semantics.

Only interface facts were taken: operation and message IDs, error codes, field offsets and
sizes, framing magic values, and the order of requests. No vendor source, decompiled code,
header, log text, firmware or data table is included. The disassembly listings are kept
outside this repository.

Two small configuration values were taken from the device: the protocol-1 RAT string default
(`N/C/Lf/Lt/W/G`) and the 12-value speech common-parameter setting in `luma-titan2e-speech`.

## GPL material used

- CCCI kernel drivers (`ccci_util`, `eccci`, `ccmni`): MediaTek, GPL-2.0, from
  https://github.com/motorola-mt6878/kernel_device_modules at
  `6586afaa0622648a6e740fb4a83f4b8057346bc2`. They are fetched at build time and modified by
  `packaging/titan2e-ccci/*.patch` (GPL-2.0). `rps/rps_perf.h` and `slbc/slbc_ops.h`
  from the same tree are used unchanged; the build fetches them at that commit and
  checks their SHA-256.
- `CCCI_IOC_*` ioctl numbers, modem state values and RPC operation IDs used by `luma-modemd`
  are the kernel interface defined in that tree (`eccci/inc/ccci_core.h`,
  `eccci/port/port_rpc.h`).
- ALSA PCM ioctls: Linux uapi.

## Runtime use of the phone's own files

At runtime, on the device, Luma reads (never writes) the phone's own `nvdata`, `protect1`,
`protect2`, `nvcfg`, `mcf_ota`, `nvram`, `vendor` (`etc/md`) and `modem` partitions. The modem
firmware is loaded from the phone's own modem partition. None of these files are included in
Luma or redistributed.

## Device data

No device identifiers (IMEI, ICCID, EID, IMSI, phone numbers), NV contents or keys are
included. Test fixtures use synthetic values.
