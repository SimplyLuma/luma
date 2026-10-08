# Luma Android runtime contract

This directory is the shared Android compatibility input for every Luma form
factor. It is not a desktop-only layer. Hardware-specific admission identities
under `hardware/` are narrow safety data consumed by the shared runtime; they
do not create a second phone implementation.

`profiles/` controls bounded resource policy. Device class selects a default;
docking changes Shell posture but does not turn a phone into a thermally
unbounded desktop. The system service therefore keeps the hardware-class
resource ceiling while the user interface adapts dynamically.

The runtime has three distinct states:

1. **stopped:** no Android applications installed, user paused Android, or
   recovery is in progress;
2. **ready:** the container and Android session are warm, including background
   notification delivery;
3. **active:** one or more Android application windows are visible.

Luma never intentionally freezes Android during normal idle because that also
freezes notification delivery. Waydroid may independently report a warm
container as `FROZEN`; Luma thaws that state through Waydroid's public lifecycle
API before boot-completion and application-service readiness probes.
Desktop and tablet may warm an installed runtime after sign-in. Handheld starts
only when an Android application is opened or the user explicitly resumes it;
after that, Android Doze remains responsible for idle applications until the
runtime is paused or the login session ends. The handheld systemd scope also
has explicit CPU, memory, IO, and task ceilings.

The release image must pin and hash Android system/vendor image inputs. The
development initializer may download from Waydroid's official image channel,
but no production launch or APK installation may fetch a runtime image.

## Host integration defaults

- multi-window mode is enabled;
- the full Android launcher is not presented as the primary Luma surface;
- Android desktop entries remain the application registry source;
- no host home directory is bind-mounted wholesale;
- APK files open with Prairie Android Installer;
- Android permissions remain deny-until-requested; and
- repair never deletes Android user data.

Generated Waydroid launchers are continuously reconciled through
`/usr/bin/luma-android`, so application-grid, search, dock, permissions, and
ordinary launch all enter one supervised lifecycle. A cold launch waits for
Android's public `sys.boot_completed` property before dispatching the requested
package and reasserts it once after the launcher settles. Warm launches remain
single-dispatch.

On Fairphone 6, direct interactive startup additionally requires an exact
running kernel-notes digest in
`hardware/fairphone-fp6-kernel-notes.sha256`. The list is currently empty after
the IFPC candidate failed the full cold workload. The private Pixman/Weston
and loopback-only FreeRDP path in `fp6-software-weston.ini` is retained only as
a root-authorized, reboot-cleared laboratory diagnostic. Repeated 30 and 60 Hz
tests produced GMU/DPU faults despite the compositor being denied DRM devices,
while 20 Hz was physically safe but unusable. Runtime `.57` creates no lab
authorization marker, so ordinary FP6 launches fail closed. A root GPU
watchdog still latches any GMU timeout or hangcheck and stops Waydroid;
unknown, revoked, modified-containment, and latched states all fail closed.

See the [runtime package builder](../../scripts/packages/build-luma-android-runtime.sh) and
[platform scope](../../docs/platforms.md) for the compatibility boundary.

Android application window chrome and resize lifecycle are governed by
the [maintained window integration](../../patches/android-hardware-waydroid/README.md). The
source patches live under `patches/android-hardware-waydroid/`,
`patches/android-frameworks-base/`, and `patches/android-device-waydroid/`.
They are built into the vendor/system product for release rather than injected
by a GNOME extension or hidden with runtime CSS.

## Reproducible source build

`scripts/android/sync-waydroid-source.sh` resolves the pinned LineageOS and
Waydroid manifest. `scripts/android/build-prairie-waydroid-windowing.sh` then
applies Waydroid's complete API-matched Android patch series before applying
the three Prairie deltas. The upstream series is a required product input: it
defines container fundamentals such as Waydroid's `host` init identity and is
not interchangeable with Prairie's smaller windowing patch family.

The Fedora build host must provide Meson, glslangValidator, and Python Mako
(`dnf install meson glslang python3-mako`) in addition to the standard
Android/LineageOS build toolchain. Waydroid's Mesa product module invokes these
host dependencies while constructing full images; the Prairie builder fails
in preflight if any is absent rather than failing late in Ninja.

Mesa's `mesa_clc` is not taken from the Fedora host. Waydroid pins a matching
compiler under `prebuilts/mesa-tools` and its official patch series exposes the
wrapper in `prebuilts/build-tools/path/linux-x86`. The Prairie builder adds
that upstream tool directory to the inherited external-build PATH and verifies
the wrapper before Ninja starts.

Set `LUMA_BUILD_WAYDROID_IMAGES=1` to produce a matched `system.img` and
`vendor.img`. Framework/SystemUI changes must be deployed as that coherent
pair; a newly built privileged APK must never be overlaid onto an independently
downloaded Android image solely because both report the same API level.

`LUMA_BUILD_JOBS` optionally caps Ninja concurrency. Use
`LUMA_BUILD_JOBS=8` on the 32 GiB ThinkPad while its 8 GiB design VM is running;
the Android ABI/link stages can exhaust memory at the host's automatic 18-job
default. The builder rejects zero and non-numeric values rather than silently
passing an unsafe or ambiguous job setting.

For accepted visual iteration, set `LUMA_WAYDROID_INCREMENTAL=1`. The first
incremental invocation applies Waydroid's pinned series and Prairie's three
patches as local commits, records the complete repo-project snapshot plus every
input hash under `.repo`, and leaves that clean prepared state in place.
Subsequent invocations skip the unchanged upstream series and refresh only a
Prairie project whose patch hash changed, so Ninja sees the real narrow delta.
Unknown commits, dirty files, or a changed upstream fingerprint stop the build.
The default lane remains the canonical clean build: it verifies and resets only
a known prepared state, reapplies every pinned input, emits the matched image
pair, and returns the source tree to the resolved manifest on exit.

`scripts/android/deploy-prairie-waydroid-images.sh` is the privileged,
checksum-gated deployment boundary. Stop the per-user Waydroid session and the
system container before invoking it. It stages both images on the destination
filesystem, retains the previous matched pair, restores SELinux labels, and
registers `/etc/waydroid-extra/images` through Waydroid's official forced
initializer. Android user data is not removed. The deployer rolls back both
files and re-runs the initializer if replacement cannot be completed. Start
the container and the normal Luma Android user service only after the script
returns successfully. The image directory carries its own `SHA256SUMS`, so the
matched pair can be transferred and verified without copying inspection-only
framework artifacts from the build output.
