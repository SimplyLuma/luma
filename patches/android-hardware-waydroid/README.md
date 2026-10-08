# Luma Waydroid hardware integration

These patches apply to Waydroid's `android_hardware_waydroid` `lineage-20`
branch at commit `85f31a9103b84faba24eb7cbca47750d12e64e4b`.

Project Luma keeps Waydroid visibly credited as the initial Android container
engine. This patch family owns only Luma's converged host-window integration:

- one Prairie client-side title component for Android application surfaces;
- real Wayland move, minimize, maximize, restore, and close controls;
- per-task window resize instead of a shared Android-display hotplug;
- preservation of the host window through bounded Android activity relaunches;
- shared App Kit window and content-island geometry from the platform SDK;
- task-aware Android Back navigation in the Prairie title component;
- host-owned pointer presentation on desktop and tablet, so crossing an
  Android window never swaps to Android's density-scaled cursor;
- one posture switch so handheld presentation can omit desktop controls without
  maintaining a second Android runtime.

The live development lane may install the rebuilt `hwcomposer.waydroid.so`
through Waydroid's documented vendor overlay. That overlay is disposable test
state, not a release artifact. Accepted changes are rebuilt into both pinned
x86_64 and AArch64 vendor images, whose complete resolved manifests and hashes
are recorded under `config/android/`.

Do not implement this surface as a GNOME Shell extension, window-matching CSS,
or a second wrapper window. Android content and its window controls must remain
one Wayland toplevel with one lifecycle and one input coordinate space.

## Effective build order

1. `0001-prairie-native-android-window-chrome.patch`
2. `0002-prairie-host-pointer-policy.patch`
3. `0003-luma-appkit-content-island.patch`
4. `0004-luma-surface-treatment-frame.patch`
5. `0007-lumaui-current-inset-frame.patch`
6. `0008-luma-desktop-window-behavior-recovery.patch`

Historical `0005` requires intermediate sources absent from both the pinned
upstream tree and the preserved Sep12 files. Preserve `0005`, `0006` and that
backup as evidence; do not append them blindly to a clean build. `0008`
reconciles their desktop InputMethod suppression, close handling and transparent
calibration against the actual pinned source. It carries forward the Sep12
logical-to-pixel correction and uses a tested in-process resize worker. It
now retries a missed final size twice at 1.5-second intervals. Actual Android
task-layer bounds acknowledge completion; clipped host buffers do not. Close
and newer configure generations cancel stale retries without a window pointer.

Both clean and incremental builders apply the effective six-patch sequence and
compare the three shared frame headers, resize-worker header and Figtree notice. The
incremental state includes both new patch hashes, so an older prepared tree
cannot silently retain its older behavior.

`0007` takes geometry and colors from current `lumaui.window` and `lumaui.colors`
tokens, with the same native symbolic control SVGs: 46px title row, 30px controls,
8px left/right/bottom content gutters, 20px outer and 12px island radii. Android
Back sits immediately before Minimize. The platform SDK installs the generated
glyph header alongside the compatibility frame header.

`0008` leaves move/resize ownership with `xdg_toplevel_move/resize` and the real
pointer/touch seat serial. A sleeping worker coalesces pending requests per task;
it delivers the final resize immediately or a held-still drag after 1.5 seconds.
Android socket waits never run on the Wayland input thread. Host chrome painting
during resize is limited to 30Hz, with the final geometry always painted.
Untransformed task layers retain their natural scale and clip at the content
boundary. The composed-canvas path still uses its existing crop/input transform.

Source checks are not image qualification. Run
`python3 tests/android/windowing-patch-series.py PINNED_HARDWARE_CODELOAD_ARCHIVE`
and `bash tests/smoke/android-windowing-source.sh` for applicability and compiled
primitive/worker checks. Complete HWC/image compilation, actual mouse/touch
move/resize/maximize/restore, missing-size recovery, accessibility, visual
shadow parity, media-layer regression and upgrade/rollback remain required.

The patch includes the pinned Figtree 2.0.3 variable font used by native Luma
title bars. Its expected SHA-256 is
`26ad3db9b31ff7dde67a91ff515d022d2f495cd506590699cf264f0bfe6fb714`.
The unmodified copyright/OFL notice is pinned to Google Fonts commit
`a60a77e14f28abd4ef243a1b5dfc48df0cec5205`, SHA-256
`140d37233e7f3ce7313798befa9600893bcceaf41a55fa0fa5ad52f7f657a268`.
It is included in the source and installed at
`/vendor/etc/licenses/prairie-figtree/OFL.txt` alongside the font module.
It also vendors the official wayland-protocols 1.31
`fractional-scale-v1.xml` definition required by this pinned upstream hardware
revision; its SHA-256 is
`5941de5d28f427ecdadddc8623a6f6af0a30b0ab4726847236ba7a7652b81316`.
The patch deliberately leaves `vendor.waydroid.task@1.0` unchanged. Prairie's
per-task resize request uses a versioned local `SOCK_SEQPACKET` channel between
the hardware composer and task service. The service validates the Android
system UID with `SO_PEERCRED`, exact message size/version, and task bounds
before calling Android's task manager. This avoids silently changing a frozen
HIDL transport ABI.

The desktop component is intentionally absent when Android property
`persist.luma.device_class=handheld`. Desktop, tablet, and handheld therefore
use the same compositor and Android runtime; posture changes only presentation.

The second patch consumes SurfaceFlinger's cursor layer without assigning it a
Wayland cursor role on desktop and tablet. Mutter remains the one cursor owner,
including theme, size, scaling, and accessibility settings. Handheld retains
Waydroid's upstream cursor path for the cases where Android itself owns the
complete presentation surface.

The third patch consumes the platform's toolkit-independent frame primitive and
its generated design tokens. Both preparation paths compare the vendored header
bytes against `src/luma-platform/compat` and reject drift. The frame remains a
subsurface of the app's existing toplevel. In SurfaceFlinger-composed RGB mode,
Wayland viewporter fits the existing canvas to the island; pointer, touch and
stylus coordinates use the inverse scale. Resizing this mode does not change
Android's display or its working video composition policy. Other task windows
retain the existing task-resize bridge. No additional resident process is added.

This third patch is a source candidate: full HWC compilation, coherent ARM
system/vendor image construction, runtime graphics/input/accessibility and
cold-boot/rollback checks remain open. Do not admit it from source checks alone.
See `docs/changes/2026-09-06-android-appkit-frame.md`.

`0009-luma-frame-buffer-thread-ownership.patch` fixes an installed clean-boot
Scudo double-free in `redraw_titlebar`: the HWC creation thread and Wayland
configure dispatcher could replace the same proxy/mapping simultaneously.
Frame storage and geometry share a per-window recursive lock. Opaque never
reused listener generations resolve to strong window owners through a bounded
weak registry; retirement precedes collection removal, and cached pointer/touch
input retains generations rather than dereferencing destroyed surface proxies.
Native concurrency checks run the actual redraw body with mapped memory and
require the unlocked control to fail. Compiled callback tests exercise retired
handles, in-flight lifetime, concurrency, stale generations and capacity refusal.
Installed repeated close/reopen and resize controls remain mandatory.

`0010-luma-authoritative-task-removal.patch` adds the backward-compatible window
HAL 1.3 task-removal method. Android TaskStackListener supplies the authoritative
task ID; only system_server UID 1000 can retire its host surface. Healthy
minimized windows remain alive. Resize work is cancelled before collection
locking, callback admission is retired before erase, and existing in-flight
listeners keep their strong owners. Native task-removal tests cover unauthorized
UIDs, unknown/zero IDs, minimized survivors and callback lifetime.

`0011-luma-mandatory-initial-task-resize.patch` prevents an old composed layer
from acknowledging and cancelling a newly queued resize before its first socket
delivery. The worker owns the first dispatch under the existing generation lock;
retry, cancellation and value-only thread ownership remain unchanged. A blocked
worker regression fails with the preceding header and passes with the corrected
header. Composed layer bounds remain weaker than package-manager task bounds;
installed close/reopen content-size stress is required before acceptance.

`0012-luma-shared-composer-rpc-threadpool.patch` matches the module's HIDL
thread pool configuration to the maintained composer service's four-thread,
caller-joins pool. Configuring one thread after service registration has started
the shared pool causes libhwbinder to abort; configuring earlier can silently
reduce the service's capacity. Both initializers now request the same existing
four-thread ceiling. Genuine Binder failures and Wayland disconnect reporting
remain intact. Actual repeated session restart and cold-boot qualification are
required; the preceding image's startup tombstone remains a failed control.
