# FP6 fresh install: wiped modem EFS (assessment, 2026-10-16)

Status: proposal for review. Nothing here has shipped or touched a modem partition.

## What a fresh install sees

A Fairphone factory flash erases `modemst1` and `modemst2`. If Android never boots
afterwards, Luma starts the modem with:

- IMEI `unknown`
- both UIM slots `no-atr-received`
- platform MCFG `DSDS_CDMALess-LA-Milos` stuck at `Pending`

The image's `rmtfs` runs `-r -P -s` (`packaging/rpm/rmtfs-fp6.spec`, `%check`). That
mode serves partitions read-only and keeps the modem's writes in RAM. The modem's
EFS (IMEI/NV, UIM configuration, PDC selections) is therefore never persisted, and
anything it rebuilds is lost at the next modem start.

Android avoids this because its `rmt_storage` writes `modemst*`. The public guide
works around it by asking for one Android boot. No document in this repo describes
exactly what Android's first boot does beyond letting the modem write.

## What already exists

| Mechanism | Writes partitions? | In image? | Fit |
|---|---|---|---|
| `rmtfs -r -P -s` (packaged) | no (RAM shadow) | yes | the cause |
| `-W` (`patches/rmtfs/0003`) | modemst1/2 only, 10 MiB checked | no; manual `persist-fp6-tmo-pdc-remote.sh` | risky to automate |
| `-R` (`patches/rmtfs/0002`) | only an all-zero modemst | no; `recover-fp6-modem-nv-pdc-remote.sh` | refuses a Pending platform (needs empty PDC lists) |
| file store `-o /var/lib/luma/rmtfs -s` | **never** | no; `migrate-fp6-rmtfs-file-store-remote.sh`, `assemble-fp6-alpha1-userdata.py seed_fresh_native_nv` | **recommended** |

The file store copies `modemst1/2`, `fsc`, `fsg` and `study` once into a root-only
directory on userdata. From then on rmtfs serves and persists the modem's EFS there.
The physical partitions are only ever read. `luma-fp6-carrier-pdc` already refuses to
run without this store (`scripts/mobile/luma-fp6-carrier-pdc:136-143`), so the
carrier work below needs it anyway.

## Recommendation

Ship the file store as the default for fresh installs. Partition writes stay out of
the shipped path.

1. **Seed once, from packaged source.** Add a oneshot unit
   `luma-fp6-modem-store.service` (in `luma-fp6-carrier-adapter` or `luma-fp6-platform`):
   - Order: `Before=rmtfs.service`, `ConditionPathExists=!/var/lib/luma/rmtfs`.
   - Device check: FP6 model; each partition is a block device of the expected size;
     `fsg` is not all-zero (if it is, stop and report, because the device needs Fairphone's tool).
   - Seed: `dd` the five partitions into `rmtfs.new`, add the empty `modem_tunning`,
     use `0700/0600` and SELinux labels, `fsync`, rename atomically, and write a
     SHA-256 manifest.
   - This is `seed_fresh_native_nv` and the migrate script turned into a product. Drop
     their per-device hashes, kernel pin and staged TMO MBN, because a fresh user's
     `fsg` is unique.
2. **Select the rmtfs backend in the package, not in `/etc`.** Ship an
   `rmtfs.service` drop-in in `/usr/lib` that runs `rmtfs -o /var/lib/luma/rmtfs -s`
   when the store exists, and keeps today's `-r -P -s` otherwise. A small
   `ExecStart` wrapper that checks for the store is enough. Update the
   `rmtfs-fp6.spec` `%check` to cover both modes. No `-W` or `-R` anywhere.
3. **Finish the platform config.** After the first modem start, `luma-fp6-carrier-pdc`
   already makes `DSDS-LA-Milos` Active (it loads only if missing and activates).
   Change it to tolerate today's Pending-without-indication, as the migrate script
   does: activate, restart the modem, re-list, require Active. With the file store,
   the activation then persists.
4. **Lifecycle.**
   - A factory reset or reinstall wipes userdata and therefore the store. The next
     boot re-seeds from the partitions, which never changed.
   - OTA updates and rollbacks keep `/var`.
   - An Android boot after Luma still sees its own untouched partitions.
   - Nick's old phone (hand-made `/etc` drop-in and store) keeps working: a
     packaged drop-in must not override an existing `/etc` one.

## Open question to measure first (needs a factory-flashed phone and your go)

Does the modem rebuild its EFS from `fsg` into a blank store on its own? That would
bring back the IMEI and the UIM ATR. Or does it need the platform MBN activated
first, or an `fsc` restore cookie?

The migrate script was run on a device with boot-cleared `modemst`. The
TMO-registered result suggests it does rebuild, but the IMEI before and after was
not recorded.

Test, all read-only on the partitions:
1. seed the store
2. start the modem
3. record `mmcli` IMEI, UIM card status and the PDC lists
4. activate DSDS, restart the modem, record the same again
5. reboot, record again

## Risk and estimate

- **Risk is low.** No modem partition is written, and backing out means deleting the
  store and the drop-in.
- **Residual risk:** device-unique NV (IMEI, RF calibration) now sits on userdata.
  It is root-only, but the store must be excluded from any backup or diagnostics
  export.
- **Estimate:** about 1 day of source work, plus half a day on a freshly flashed
  device. Target .20. Code changes follow review of the measurement.

## Implementation (luma-fp6-carrier-adapter 0.1.0-1.luma.6)

- **Seeding:** `luma-fp6-modem-store.service` runs `/usr/libexec/luma-fp6-modem-store`.
  - It runs before rmtfs, once, only when `/var/lib/luma/rmtfs` is absent.
  - It requires both modemst partitions to be 10 MiB and every partition to be ≤ 64 MiB.
  - It refuses a blank `fsg`.
  - Each partition is hashed before and after its copy; the copy must match.
  - The manifest holds only hashes and sizes. The store is promoted by an atomic rename.
- **Backend choice:** the `rmtfs.service.d/50-luma-fp6-modem-store.conf` drop-in
  pulls the seeder in and runs `/usr/libexec/luma-fp6-rmtfs`. That wrapper `exec`s
  `rmtfs -o /var/lib/luma/rmtfs -s` when the store is complete, and `rmtfs -r -P -s`
  otherwise.
  - Stores made by the older migrate script (no manifest) are accepted.
  - A hand-made `/etc/.../50-luma-file-store.conf` sorts before the packaged
    drop-in, so it is superseded by the same command.
- **carrier-pdc:**
  - Runs without `/etc/luma/fp6-carrier-pdc.conf` (platform only).
  - Skips cleanly on the read-only backend unless a profile is configured.
  - Tolerates one Pending platform entry. If the selected platform stays Pending
    after activation, it starts `luma-fp6-modem-restart.service`, which refuses
    while ModemManager or imsd is active. It then requires Active.
- **Limitation:** the platform MBN (`dsds-la-milos.mbn`) is still not in the image.
  A device whose PDC list lacks DSDS (no Android boot ever) therefore fails with
  `platform_profile_missing` and keeps working read-only, as today.
- **Image assembly:** `assemble-fp6-alpha1-userdata.py` accepts exactly this packaged
  drop-in, byte-compared with the source (backend `packaged-first-boot-file-store`),
  and still refuses an existing store or any other override.
- **Tests:** `tests/mobile/test_modem_store.py` (seeding and backend choice);
  `test_carrier_lifecycle.py` (platform-only, read-only skip and refusal, Pending →
  restart → Active); `test_generic_recipient_nv.py` (assembler).
