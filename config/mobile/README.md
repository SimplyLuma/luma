# Luma mobile userspace contract

This directory defines the experimental Fedora 44 `aarch64` userspace shared
by the Fairphone 6 behavioral twin and the eventual physical-device bring-up.
It does **not** define a Fairphone kernel, device tree, firmware bundle, Android
boot image, or distributable phone image.

## Optional shared Android application support

The mobile composition consumes the same `luma-android-runtime` source and
installer contract as desktop, Fedora's AArch64 Waydroid engine, and a native
AArch64 build of the same patched Filer source. This keeps APK/APKS/XAPK/APKM
installation, Settings, application registration, and security boundaries in
one shared implementation rather than creating a mobile fork.

`/etc/luma-device-class` selects the handheld policy. Android is not warmed at
login on a phone; opening an Android application starts it on demand. Once
started it can continue delivering notifications and use Android Doze, but the
host scope has tighter CPU, memory, IO, and task limits. Pause remains an
explicit battery-saving control. The composer requires locally verified Luma
RPMs and never downloads Android system/vendor images during an app launch.

The runtime package alone is not physical-device acceptance. A release still
needs an architecture-matched, pinned and hashed Android system/vendor image,
binder support from the external kernel, and graphics/audio/network validation
on the Fairphone. Google services and Play certification are not implied.

This compatibility lane is never the phone control plane. Under
the [shared platform contract](../../docs/platforms.md), boot,
authentication, networking, eSIM, SMS/MMS, calls, call audio, notifications,
power, updates, and recovery must continue to pass with the runtime removed or
masked. Stock Android telephony containers are development oracles only and
are excluded from mobile composition.

The two execution lanes must remain visibly different:

| Lane | Kernel and hardware backend | Shared input |
| --- | --- | --- |
| Behavioral twin | QEMU `virt`, generic Fedora kernel, virtio devices | Fedora 44 mobile userspace contract |
| Physical FP6 | Milos/SM7635 kernel, FP6 DT, device firmware | Fedora 44 mobile userspace contract |

`packages.txt` is the intended first physical-device diagnostic package set.
`ui-packages.txt` is a separately pinned Phosh session layer validated first in
the twin. GDM, GNOME Shell, and the complete Control Center are deliberately
absent from the physical manifest: the diagnostic image must not acquire a
two-gigabyte desktop closure merely to start a mobile compositor.

The initial twin proof starts from Fedora Cloud and proves the architecture,
identity, state simulator, and clean boot before composing either complete
physical layer.

`scripts/mobile/compose-fp6-rootfs.sh` turns these manifests into a native
Fedora 44 AArch64 root-filesystem archive on the isolated lab builder. The
archive deliberately contains no FP6 kernel, device tree, firmware bundle,
partition table, phone credentials, or installation authorization. It is a
userspace input for the later P5 substitution experiment, not a flashable
image.

Composition uses fixed tar ordering/timestamps and four fixed zstd workers.
If package installation completed but finalization was interrupted,
`LUMA_FP6_FINALIZE_ONLY=1` resumes only from an existing tree carrying all
three negative Luma markers. It refuses to replace an existing archive and
regenerates the offline udev and systemd catalog caches before packaging.

The physical manifest includes Fedora-native `qrtr`, `rmtfs`, and `tqftpserv`
for Qualcomm services, `greetd` for the diagnostic Phosh seat, and `sudo` for
key-authenticated rescue administration. It installs the exact shared
`luma-boot-theme` RPM and selects `luma-loading-handheld` through an
image-owned Plymouth configuration. The handheld theme remains native to the
boot boundary; the existing quit units still release any inherited initramfs
splash before Phoc acquires DRM. The composer creates a fixed UID/GID
1000 `luma` account but leaves it locked and embeds no SSH key. It disables SSH
password and root login. An archive's package-contract hash must match the
current manifests; changing either manifest deliberately makes older archives
stale.

The generic rootfs deliberately carries no kernel or generated initramfs. Its
theme/package/config checks therefore prove the composition input, not a
physical cold boot. The physical candidate gate must separately verify that
the accepted FP6 initramfs contains the selected handheld descriptor, script,
renderer, wordmark, and dot asset before boot evidence can be accepted.

The twin is useful for system startup, update/recovery logic, mobile UI work,
screen geometry, rotation, and deterministic battery/connectivity scenarios.
It cannot validate the FP6 display pipeline, GPU, touch controller, Wi-Fi,
Bluetooth, modem, cameras, audio routing, charging, suspend, or thermals.

## FP6 camera userspace runtime

The experimental physical-camera userspace is reproducible independently of
the distribution libcamera packages. On the Fedora 44 AArch64 phone or an
equivalent AArch64 builder, choose two new paths and run:

```sh
./scripts/mobile/build-fp6-libcamera.sh \
  YOUR_NEW_BUILD_DIRECTORY \
  YOUR_NEW_INSTALL_PREFIX
```

The builder requires the Python `jinja2`, `yaml`, and `ply` build modules,
checks out exact libcamera and FP6 prerequisite commits, applies the external
prerequisite patches 1-15 followed by Luma patches 16-29, builds
only the Simple pipeline/IPA and GStreamer plugin, and installs the three
source-managed sensor tuning files. Both target paths must not already exist;
the script never deletes an old runtime or replaces Fedora's system packages.
Runtime selection remains explicit through `LD_LIBRARY_PATH`,
`GST_PLUGIN_PATH_1_0`, and the libcamera IPA path variables until durable RPM
packaging and signing are complete.

See the [public build guide](../../BUILDING.md) and
[platform/device limits](../../docs/platforms.md). Physical-device preparation
requires the selected device's boot, recovery, firmware and storage procedures;
a successful userspace composition does not establish safe installation.

After the postmarketOS control image reaches Phosh, the owner may temporarily
start its disabled SSH service and collect a bounded native-Linux inventory:

```sh
LUMA_FP6_SSH_TARGET=user@172.16.42.1 \
  ./scripts/mobile/inventory-fp6-control.sh
```

The collector makes one SSH connection, runs read-only probes, validates the
Fairphone 6 device-tree identity and postmarketOS userspace, and stores a
private report below ignored build storage. It omits credentials, transport
identifiers, MAC addresses, Wi-Fi names, and LAN addresses, and records
`P5_AUTHORIZED=false`. Stop the temporary SSH service immediately afterward;
the collector intentionally does not manage services or change the phone.

## Offline P5 candidate

`scripts/mobile/prepare-fp6-p5-candidate.sh` is the first Fedora substitution
assembler. It runs unprivileged on an isolated Fedora 44 builder and requires a
caller-supplied OpenSSH public-key file plus a caller-supplied yescrypt or
SHA-512 password hash. Neither credential has a repository default.

The assembler starts from the exact verified control disk, preserves its GPT,
VFAT boot partition, and root UUID, reformats only the root partition in a new
local copy, then installs Fedora. Its allowlist imports only the proven Milos
`7.1.2` modules and firmware; it cannot import postmarketOS/Alpine executables
or libraries. The result is independently round-tripped through Android sparse
conversion and carries `P5_INSTALL_AUTHORIZED=false`.

The first accepted offline candidate was bound to sparse SHA-256
`9ad510942543df408dd5b3c4485eac72a7ab7bf1123d360e1e8bdce4012cba97`.
It was later installed only under separate hash-locked authorization and
proved that Fedora 44 and Phosh can run on the physical FP6. Live acceptance
also exposed missing PAM/D-Bus payload and Plymouth-quit requirements. The
package contract now rejects that historical candidate. A corrected 571-entry
rootfs and 40-check offline v3 candidate have since been independently
accepted. The v3 sparse image is 2,186,334,808 bytes with SHA-256
`4e530d331e9788c3131c069c81f98c1d7b8eb5512b28cfe1e6aff5e1b1afe203`;
its manifest still records no phone access and no install authorization. The
historical candidate's initial approximately
579 MiB of user-available root space expanded to the userdata partition on the
phone, but this remains a diagnostic lane rather than a daily-driver claim.

The generated image is a `userdata` candidate only. It does not include or
require a new Android `boot` image, a DTBO write, a slot change, unlock, or
relock. Artifact creation is not authorization to write the phone. Validate an
already composed candidate with
`scripts/mobile/smoke-fp6-p5-candidate.sh`.

## Live Fedora inventory

`scripts/mobile/inventory-fp6-fedora.sh` is the read-only physical baseline
collector for an already running P5 Fedora userspace. It is separate from the
postmarketOS control collector and rejects any remote identity other than
Fedora 44 on a Fairphone 6 device tree. The report covers the live graphical
session, DRM, input, audio, media, IIO, remote processors, Qualcomm services,
battery, thermals, and bounded boot-error counts while filtering device and
network identifiers.

The first accepted report isolated the modem loop to Fedora 44's
`rmtfs-1.1.1`: the physical `study` partition exists, but the packaged binary
lacks upstream's FP6-specific `/boot/modem_study` mapping. See
the [FP6 rmtfs backport](../../scripts/mobile/build-rmtfs-fp6.sh).

`scripts/mobile/build-rmtfs-fp6.sh` builds the FP6 backport on a native Fedora
44 AArch64 host. In addition to the study mapping, its guarded `-W` recovery
mode keeps every path read-only except exact 10 MiB `modemst1` and `modemst2`
block partitions. It is used only for a bounded write window while the modem
is stopped; normal boot and shutdown use read-only `rmtfs -r -P -s`. `fsg`,
`fsc`, study, tuning, and every other partition remain RAM-shadowed.
`scripts/mobile/smoke-rmtfs-fp6.sh`
verifies the artifact hashes, payload, version ordering, mapping, and exact
service arguments. Physical installation remains a separate acceptance gate.

`scripts/mobile/inspect-fp6-peripherals.sh` is the narrower next-stage
preflight for GNSS, NFC, Bluetooth, Hall, haptics, charging/thermals, USB,
removable storage, and fingerprint-stack presence. It performs no physical
stimulus and never enables a radio, GNSS source, NFC poll, haptic effect, or
biometric operation. Its private report intentionally excludes radio and
network identifiers.

The FP6 GNSS userspace delta is pinned in `fp6-gnss.env`. Build libqmi first
with `scripts/mobile/build-fp6-gnss-rpm.sh libqmi OUTPUT_DIR` on a disposable
native Fedora 44 AArch64 builder. Only after installing those unsigned RPMs in
that disposable builder may the same script build the `modemmanager`
component. The script refuses to run on a Fairphone, never contacts a phone,
and never installs its output. Signing, live installation, location
enablement, and physical fix acceptance remain separate decisions.

The complete bundle can be checked with
`scripts/mobile/verify-fp6-gnss-rpm-bundle.sh BUNDLE_ROOT` on Fedora AArch64.
It verifies the pinned source/spec/patch identities, every RPM hash, package
name, version, architecture, and the negative phone/install/enablement gates.
It never installs an RPM or starts a service.

The one-time physical diagnostic now runs the exact four runtime packages,
with the four Fedora stock RPMs retained root-only on-device for rollback.
This does not grant reusable installation authority or approve unsigned
packages for image inclusion. `scripts/mobile/accept-fp6-gnss-fix.sh` performs
the remaining privacy-preserving outdoor test: it blocks system suspend while
allowing the display to blank, never prints or persists coordinates, disables
GNSS on every exit path, and restores the original 30-second refresh.

The current S3NRN4V NFC driver/device-tree series is retained under
`patches/linux-milos-nfc/` and pinned by `fp6-nfc.env`. The driver's calibration
requests are deliberately nonfatal for this controller: when `hwreg.bin` and
`swreg.bin` are absent it keeps the values already stored in chip flash. Luma
therefore began with a blob-free, reader-only diagnostic candidate. A later
read-only audit recovered the exact two calibration files from this phone's
preserved official stock vendor image. Their source image and byte hashes are
pinned for local diagnostics, but the bytes remain outside the repository and
must not ship until redistribution is accepted.

`scripts/mobile/build-fp6-nfc-candidate.sh` builds the four exact 7.1.2 NFC/NCI
modules and applies the minimal NFC overlay to the physically accepted sensor
DTB on an isolated Fedora AArch64 host. Two clean builds are byte-identical.
`scripts/mobile/prepare-fp6-nfc-boot-candidate.sh` keeps the proven sensor
kernel and ramdisk exact and changes only that DTB. The resulting RAM-boot
image is pinned at SHA-256
`1f7ec55cc841a35e7c7971178b0370f574ca18a1e71635c5c235cd76f5bebf7e`.
The recoverable module and calibration installers do not load modules, power
NFC, or reboot, and the inspector does not poll a tag. `neard-tools` provides
the minimal Fedora reader utility. The bounded acceptance script exposes no
identifier or payload, guarantees power-off cleanup, and never attempts a tag
write. Kernel enumeration and an empty-field power/poll cycle now pass;
physical tag read, suspend, power, and security acceptance remain separate.
Writing, card emulation, payments, and secure elements are explicitly outside
this candidate's scope.

`fp6-fingerprint.env` records the exact stock fingerprint identity and keeps
all biometric and release-acceptance gates closed. The FP6 uses a FocalTech
FT9362; Fairphone now publishes the exact GPL driver source, and the Luma lab
kernel has physically enumerated both its control shim and QSEECOM TEE nodes.
The exact manufacturer `focal64` trustlet from QREL 16.95.0 is hash-pinned for
offline staging but is neither tracked nor approved for redistribution. Its
Android HAL still does not provide a reviewable Linux biometric backend. The
physically accepted listener-v2 kernel now registers exactly FS listener 10
and GPFS listener 28672. Identity-v3 proved that `focal64` first attaches with
`ENOENT`, then reaches privileged load, where the kernel's ELF32-only MDT
assembler returns `EINVAL` before secure-world app-start. A bounds-checked
ELF64/AArch64 kernel fix then booted physically and reached trustlet staging,
where the non-CMA allocator failed a post-boot order-11 request with `ENOMEM`;
secure world still made no signature decision. The next kernel-only DMA-CMA
candidate reserved its intended 32 MiB physically but exposed a deterministic
kernel/module `struct device` ABI mismatch while `evdev` loaded. Identity-v5
stopped before TA staging and restored the accepted listener-v2 rollback.
That DMA-CMA image is rejected.

The reproducible successor changes only the physically accepted ELF64-loader
DTB. It reserves a non-reusable, 16 MiB, 4 MiB-aligned `shared-dma-pool` for
Qualcomm SCM while keeping the kernel, ramdisk, and installed module ABI exact.
Two compositions were byte-identical, but physical identity-v6 proved that
the unconstrained dynamic allocator placed the pool above SCM's 32-bit DMA
window; it stopped before staging and that image is rejected. A corrected,
otherwise identical DT-only successor constrains placement to System RAM
between 2 and 4 GiB. Its two compositions are byte-identical and its boot
SHA-256 is
`c81af5c9ef8a5821c5d15b067eef1d0076b64d5277a9aa9a3c0969f6e4ff6d08`.
Identity-v7 accepted that placement and both expected listeners, but the one
`focal64` start returned generic SCM `EIO`; cleanup was complete and the system
remained healthy. The exact stock vendor-boot DT says commonlib64 is already
loaded by UEFI, and the matching NON-HLOS image contains no separate cmnlib64
payload. The unresolved boundary is therefore the secure-memory transport:
generic TZ memory versus SHM Bridge and the stock dual reusable-pool contract.
The SHM-Bridge half of that difference is isolated in two byte-identical,
kernel-only candidates. They preserve the low-32-bit DTB, ramdisk, and both
installed fingerprint modules exactly. Candidate SHA-256 is
`21663b13539a6a347b11d298c629771620e3b964545770fb658c1ff446c0a54e`.
Its separately authorized slot-B boot-only gate passed: the running boot
prefix, runtime configuration, modules, and pool matched exactly; SHM Bridge
initialized; and the bounded fault window was clean. No trustlet was staged or
loaded. A new, separately authorized identity test is required before any
biometric protocol work. Read
`docs/research/fp6-fingerprint-backend.md` before any fingerprint work. Do not
copy the Android HAL/module into Luma or enable enrollment until the secure-
backend gate and each physical biometric acceptance gate are satisfied.

Identity-v8 subsequently proved successful SHM registration of the complete
7,790,592-byte focal64 staging area, but APP_START still returned SCM `EIO`
with zero response words and created no TA session. The allocator is no longer
the immediate boundary. Fairphone's exact securemsm source shows that FP6
registers both complete QSEE heaps with SHM Bridge and skips apps-region
notification because AppsBL owns it. A reproducible boot-only successor now
mirrors that 20 MiB apps/16 MiB TA whole-heap topology without changing the
module ABI; its candidate SHA-256 is
`5d808420b5827264c007f07fea56bc8e0a6dbb41ee22799d7ea184e034b15fa8`.
Its boot-only physical gate accepted the exact running image, configuration,
modules, both low-32-bit heaps, and both whole-pool SHM bridges, but fourteen
Adreno GMU `GPU_SET` timeouts violated the clean-fault contract. The candidate
is rejected and the exact healthy v7 image was restored to `boot_b` and
reverified. No trustlet was staged or loaded. Do not run a stock-heaps TA-load
test from this rejected image.

Two reproducible v9 boot-only images isolate that regression. The lower-risk
`dt-only` image keeps the physically accepted v7 kernel and changes only the
DTB; SHA-256 is
`9320eee4c5e35d1751cb8fbb1c476132078449d94eeddadddff67716d09ca6f8`.
The `kernel-only` image keeps the accepted v7 DTB and changes only the kernel;
SHA-256 is
`432e30cdac2edf181ce94c548c13c35107205baee089d1150126f773df05c666`.
Neither may load `focal64`. Test `dt-only` first under a new exact boot-only
authorization and roll back immediately on any critical fault.

The `dt-only` physical gate passed with both pools placed contiguously below
4 GiB, exact running identities, zero failed units, and a clean fault window.
The added reservation and inert stock DT contract are therefore not the v8
GMU trigger. The remaining diagnostic is a separately authorized boot-only
test of `kernel-only`; do not load a trustlet from either isolation image.

The `kernel-only` physical gate also passed with exact identities, zero failed
units, and a clean fault window. The regression therefore occurs only when the
new whole-pool paths activate. The current combined design incorrectly makes
QSEECom's TA heap the global Qualcomm SCM coherent pool; registering that
entire pool changes the mapping seen by unrelated SCM clients such as GPU
bring-up. The successor must use a QSEECom-owned reserved-memory allocator and
retain global SCM's accepted per-allocation SHM bridges.

That dedicated-heaps successor is implemented behind an opt-in DT child
contract. It gives QSEECom separate TA and apps allocation devices, routes
each QSEECom buffer class to the stock heap, and leaves the global SCM
allocator unchanged. Two pinned Clang 22 builds and two boot compositions are
byte-reproducible; the offline v10 boot SHA-256 is
`bc12893e0403245e2ea4a1cb6a4801c1efdba515d6f726be18f2e55f1752ab33`.
Its matching `qseecomtee.ko` changes and therefore requires a distinct,
hash-pinned runtime module gate after a boot-only fault gate. The boot-only
physical gate passed on slot B with the exact image/configuration, both pools
below 4 GiB and owned only by the QSEE child, zero failed units, and a clean
two-minute fault window. The subsequent, separately authorized ephemeral
module gate also passed: both whole-pool bridges initialized, the initial 1 MiB
TA-pool allocation was covered, the TEE nodes appeared, and cleanup unloaded
the module and removed all staged files. The phone remained healthy with zero
faults or failed units. No module was installed persistently and every trustlet
or biometric operation remains unattempted.

`fp6-haptics.env` pins the existing AW86938/AW86927-compatible Linux path and
its conservative physical-test limits. The Linux driver embeds its waveform;
the stock Android haptic firmware is neither required nor approved for
redistribution. `scripts/mobile/test-fp6-haptics.py` is read-only unless both
`--pulse` and `--acknowledge-physical-actuation` are present, and refuses a
pulse longer than 150 ms or stronger than magnitude 24576. Do not weaken those
first-test ceilings until physical and thermal acceptance is recorded.

The [shared platform architecture](../../docs/platforms.md) uses
Phosh/Phoc as Luma's handheld renderer and the genuine Luma GNOME/Mutter
session as its desktop and docked renderer. They consume one Fedora platform,
application catalog, user account, data model, design system, and release.
Shell-private settings are projections of the shared Luma shell-state contract,
not independently synchronized product state.

## Live converged-shell iteration

The physical diagnostic lane may hot-install the exact source-built Luma Shell
and integration RPMs without changing its boot image or rescue session. Its
visible identity continues to come from the shared desktop dconf source at
`config/desktop/dconf/db/luma.d/00-luma-desktop`; mobile must not copy or fork
those appearance, dock, favorite-app, wallpaper, or Tiling defaults.

`luma-shell-handheld.dconf` is loaded only after that shared profile. It is a
capability overlay that enables the native GNOME on-screen keyboard, removes
desktop window-control buttons, maximizes ordinary application windows into
the usable work area, and turns the shared dock into a persistent full-width
touch target. Maximized is deliberate: the dock continues to reserve space and
remain visible, unlike exclusive fullscreen. Desktop keeps its compact
floating dock and window controls because none of these values are added to
the shared profile. `scripts/mobile/hot-sync-luma-shell-profile.sh` validates
both inputs before applying them to the current graphical user. It is an
iteration tool, not a replacement for packaged fresh-profile defaults.

That overlay also enables the Luma-authored `handheld@project-luma.local`
extension. Its input regions activate only for a portrait primary display and
turn off on a landscape desktop primary display. It owns bottom-edge Home and
Apps gestures, right-edge Back, top-corner menu gestures, and the
single-visible-normal-window policy. It does not record touch coordinates,
typed content, or application contents. The source and RPM recipe live in this
repository; desktop builders produce the artifact for release parity but the
desktop profile deliberately leaves it disabled.

The same overlay enables Filer's shared `personal-storage-only` policy. It
hides fixed system partitions from the handheld sidebar, leaves removable and
network storage visible, and requires the Prairie warning before direct entry
to the system root. It does not alter mounts, permissions, or desktop Filer.

`environment.d/90-luma-handheld.conf` declares only the hardware capability
class. The diagnostic `luma-shell-session` launcher exports and imports the
same value before starting Shell directly because greetd does not run the
systemd user environment generator. The shared Shell combines that explicit
capability with live primary-monitor geometry: a portrait handheld gets the
camera-cutout-safe panel and immediate OSK pressed state, while a landscape
external primary display uses normal desktop chrome.

The initial greetd diagnostic lane starts Shell directly rather than through
GNOME Session. `systemd-user/luma-graphical-session.target` supplies the normal
systemd graphical-session anchor in that lane so standard user services such
as XDG Desktop Portal are eligible to start and remain bound to the graphical
login. Product images install this target as session composition; they do not
carry the transient `sleep infinity` anchor used for live diagnosis.

Fedora Mutter normally rejects a panel scale when its resulting logical area
is below 600×600. On the FP6 that hides useful 300% and 400% choices even though
the panel divides exactly at those scales. The source-level handheld policy is
`patches/mutter/0001-luma-handheld-scales.patch`; it relaxes only that minimum
area when `LUMA_DEVICE_CLASS=handheld`, while leaving exact mode validation and
all desktop sessions unchanged. `build-mutter-handheld-fp6.sh` builds it from
the pinned Fedora 44 AArch64 source RPM. During P5 bring-up only, the audited
`patch-mutter-handheld-scales.py` bridge may create a private library from the
exact installed FP6 binary. The launcher accepts only its pinned output hash
and exposes it only to the Luma handheld compositor through `LD_LIBRARY_PATH`;
it never replaces Fedora's system library or changes the global loader.

Wallpaper, font, icon, Shell, dock, and Tiling changes should be built from the
same repository inputs used by desktop and installed as exact hashed RPMs.
Most settings and asset changes can be observed live. Installing or changing a
GNOME Shell extension requires restarting only the graphical session; it does
not require rebooting the phone. Kernel, device-tree, firmware, boot-chain, and
clean-boot validation remain the only normal reasons to reboot this lane.

`capture-handheld-interaction-diagnostics.sh` records a bounded, content-free
performance trace for keyboard and gesture trials. It samples scheduler,
memory, page-fault, and Linux pressure-stall state plus redacted Shell
severity/category markers. It deliberately never records key codes, touch
coordinates, typed text, accessibility contents, screenshots, or clipboard
data.

The handheld package also owns mobile panel power policy. `Screen turns off
after` writes GNOME's shared `org.gnome.desktop.session idle-delay` value; the
mobile default is 60 seconds and `Never` remains an explicit user choice.
Because the direct diagnostic session does not run desktop GSD power services,
the handheld extension drives only Mutter's display power mode. GNOME Shell
handles the compositor-delivered `KEY_POWER` event directly while a
session-scoped logind inhibitor prevents a second firmware shutdown action.
The raw-input fallback broker must not run concurrently: doing so turns one
physical press into a Shell toggle on press and another toggle on release.
Fedora, applications, network connectivity, and notifications stay alive. No
general input device, typed content, or key value is logged.

The same package supplies `Mobile Input & Display`, reached from the app grid
or the cog above the OSK. Its keyboard registry distinguishes actual support
from installed binaries: GNOME Shell's native engine is accepted; Stevia and
Squeekboard target Phoc/wlroots; Plasma Keyboard and Maliit target KWin; Lomiri
Keyboard is a Maliit plug-in. Non-native engines stay visible as adapter work
until they type into Luma Wayland clients under Mutter and pass content-free
latency/missed-input diagnostics. A rendered keyboard alone is not accepted.

The converged application composition is additive. The shared
workstation roles in `config/shared/application-packages.txt` are required on
both desktop and phone; `phone-capability-packages.txt` adds telephony, chat,
modem, feedback, and sensor integration without defining a separate mobile
application product. Luma-owned applications are rebuilt from the same source
RPM for each native architecture. The Phosh packages in `ui-packages.txt`
provide the selected handheld presentation engine and must not be mistaken for
or used to subtract from the Luma product application set.

`scripts/mobile/build-luma-filer-fp6.sh` enforces that rule for Filer: it
accepts only the already validated shared Luma source RPM, rebuilds it on
native Fedora 44 AArch64, verifies the shared schema and warning payload, and
emits a hashed offline bundle without contacting the phone.

The product image therefore resolves the union of the shared application
baseline and the relevant capability manifest. It may use service presets and
runtime posture guards to keep phone idle cost low; it must not achieve
leanness by maintaining a second source tree or silently removing the desktop
application catalog. See
the [platform architecture](../../docs/platforms.md) for the
normative shared-state and dual-renderer architecture.

`compose-fp6-rootfs.sh` resolves that union directly: the hardware/base
manifest, experimental UI closure, shared application baseline, and additive
phone capabilities are all inputs to both DNF composition and the recorded
package-contract hash. Changing the desktop application baseline therefore
invalidates a stale phone rootfs instead of requiring a manual copy step.

`systemd-preset/80-luma-handheld.preset` is the initial idle-service policy.
It disables Avahi's always-on discovery listener and Waydroid's eager
container start in handheld posture even when shared desktop applications pull
those packages into the image. This removes neither capability nor forks the
application set: discovery and Android start on demand, and a future docked
capability controller may warm either service deliberately.
