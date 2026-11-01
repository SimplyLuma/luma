# Mobile application startup

The maintained implementation is in `src/luma-platform/apphost/` and
`appkit/luma_appkit/apphost.py`. Core, Monitor, Terminal and Tide RPM entry
points use the same native client. There is one GTK/libadwaita application per
product across Desktop, Tablet and Mobile; this does not introduce a mobile UI
runtime, an alternative app implementation or a browser container.

## Launch path

1. The session starts `luma-app-host.service`. Its single-threaded parent imports
   Python/GI, common kit and stateless core UI modules, font configuration and
   renderer libraries. Package manifests live in `luma-app-host/preload.d/`.
   Never put modules with media initialization, threads, connections, open stores
   or application instances in those manifests. Camera and Tide initialize media
   in the launched child, not in the parent.
2. One child initializes GTK, styles, common controls and a graphics context on
   an **unmapped** window. It waits on a socket, requests no frame callbacks and
   never forks after GTK initialization. The parent alone forks, and checks that
   it still has one thread. A launch consumes this child; a replacement starts
   two seconds later, outside the new app's first frames. Less than 512 MiB
   available RAM skips replacement. Unused spares get a reclaimable OOM score;
   the real app restores its inherited score before executing.
3. The libc-only `luma-app-launch` frontend reaches the prepared process without
   starting another Python interpreter. Original Python launchers remain under
   `/usr/libexec/luma-app-launchers/`. Arguments, environment, working directory,
   standard streams, termination and exit status travel through the protocol.
   Pending executor writes and exit handlers finish before the child exits.
   Windowless agents bypass the host. Native source launchers use `python3 -sP`,
   matching the accelerator's interpreter and preserving safe import paths.
4. App processes join their actual GIO/systemd application scope. A bounded
   follow-up handles GIO's asynchronous scope creation. The proxy waits for the
   application's lifetime, including when the host is restarted. The host uses
   selector and signal wakeups, with no periodic idle polling.
5. Mobile keeps Home visible until a real app window is ready, then uses a short
   80 ms opacity transition. Tablet retains its 160 ms transition and reduced
   motion remains immediate. No simulated loading screen hides unchanged work.

The host validates peer UID, credentials, process label, namespaces, seccomp and
no-new-privileges state. It refuses callers with a different confinement context;
Flatpak remains in its original sandbox. Import-time environment, interpreter
flags, linker paths and graphics context are checked. Incompatible imports
execute normally; a different compatible display configuration gets a fresh
child without the prepared display. The native handshake has a short timeout.
A commit acknowledgment gates execution, so an abandoned request can fall back
without running the application twice. Protocol versions must match.

Prepared Python modules are session state: restart the host after updating their
source or manifests. An app already running continues to use its current code.

## Native Settings and Filer

The handheld-only `luma-native-app-prewarm.timer` runs a one-shot controller after
session startup. The explicit native manifest names only the OS Settings and
Filer executables. It prepares at most two services sequentially, requires
1 GiB available RAM before each, and skips an already running service. Desktop,
Tablet-only hardware and greeter sessions do not acquire these native caches.
Identity comes from AppContext capability policy, not window width.

A scoped `LD_PRELOAD` module runs the app's normal startup/activation to construct
its initial view while deferring presentation. The app's actual view is reused
on the next activation. This is an opt-in adapter for these two packaged native
apps, not a global preload or a transformation of arbitrary GTK programs. Test
these apps when changing their activation/presentation APIs before extending the
manifest. The module consumes its preparation environment before application
threads exist, so launched children do not inherit it.

There is no recurring preparation job. Unused native caches receive OOM score
500 (or their existing higher score), are released on GMemoryMonitor low-memory
warnings and restore normal priority on activation. The preparation hold is
released on real activation/presentation, after which ordinary application lifecycle applies.
A document-open request that chooses another window also retires the untouched
prepared view, so it cannot invisibly hold the application open.
Unlike an arbitrary expiry followed by repeated relaunches, cache survival is
bounded by memory policy and real use. The controller bounds readiness waits
and does not prepare browser, media or third-party applications. Preparation
creates real app state in memory; it is not an empty icon splash.

## Remove repeated work

Kit appearance signals that resolve to the same tokens and treatment no longer
reload every CSS provider. Real dark/light, material and high-contrast changes
still reload. Image finalization creates checked-hash Python bytecode with the
image's own interpreter: OSTree's normalized source timestamps otherwise
invalidate Fedora's timestamp-based caches, forcing recompilation on immutable
storage. Hash validation still detects changed development source. This has an
import/mtime regression test; a complete new image compose remains a separate
qualification step.

## Titan measurements, 2026-10-30/31

Native Shell instrumentation timestamps launch dispatch, window map and first
focused app frame. A process-cold comparison closes the app's windows and stops
its known UI process, then returns Home before launch. The shared runtime is
prepared; this is not a cold boot/storage-cache benchmark. A warm comparison
returns Home and reopens the existing window. Native prepared views are labeled
separately: their process and initial UI already exist. Network data completion
and the final fade are **not** included in first-frame timings.

Before this change, Weather first frames took 2.08–2.13 seconds, Contacts about
2.27 seconds, and Monitor about 1.62 seconds. The retained device receipts include
new-window and resident-window comparisons, not just process dispatch time.
Representative readings, including the fresh-session check:

| Application | New UI process / prepared runtime | Resident window |
| --- | ---: | ---: |
| Weather, fresh session | 532 ms | 57 ms |
| Contacts, fresh session | 547 ms | 45 ms |
| Clock | 550 ms | 53 ms |
| Phone | 426 ms | 15 ms |
| Photos | 361 ms | 61 ms |
| Tasks | 524 ms | 57 ms |
| Messages | 675 ms | 48 ms |
| Notes | 668 ms | 60 ms |
| Calendar | 771 ms | 45 ms |
| Monitor | 554 ms | 69 ms |
| Terminal, fresh session | 391 ms | 53 ms |
| Tide | 753 ms | 58 ms |
| Settings, prepared native view | 381 ms | 45 ms |
| Filer, prepared native view | 466 ms | 55 ms |

These are individual real-device observations, not percentile guarantees or a
claim that all cold launches finish within 125 ms. Settings/Filer from fully
stopped, unprepared processes were 1.54/1.12 seconds. A killed cache, first boot
or memory pressure can legitimately take that normal fallback path. Profile
remaining construction/render time before broadening cache policy. CPU/GPU
frequency floors were briefly compared and restored because they did not help.
No continuous performance boost or generic background-app freezing is installed.
After a fresh login, the host, spare and two unused native caches totaled about
490 MiB proportional memory; all four accumulated zero CPU ticks over a ten-second
idle sample. This is a real memory tradeoff, not a measured battery-life result.
The host must start after `gnome-session-initialized.target`: starting alongside
Shell captured its environment before DISPLAY/WAYLAND_DISPLAY were published,
which made every later launch decline the prepared display. A clean GDM session
restart/password login and repeat timing caught and verified this ordering fix.

## Research and lifecycle boundary

[AOSP ZygoteInit](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/core/java/com/android/internal/os/ZygoteInit.java)
preloads classes, resources, graphics libraries and text resources for shared
startup. The useful principle here is to pay common initialization before the
request, share read-only work, and measure the first real frame. Luma retains its
Python/GTK ABI and isolation model rather than copying Android's runtime.
[Android's performance guidance](https://developer.android.com/topic/performance/measuring-performance)
separates cold, warm and hot startup and gives targets of 500/200/150 ms; TTID
and full data/display readiness must not be conflated.

[AOSP CachedAppOptimizer](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/services/core/java/com/android/server/am/CachedAppOptimizer.java)
coordinates freezing with Binder transactions and process lifecycle. Luma's
clock, call, sync and media services cannot safely be frozen simply because an
app window is hidden. A future cache/freezer needs explicit background roles,
IPC wakeup, memory-pressure eviction and restoration tests. No freezer is part
of this change.

Apple publishes components such as
[dyld's prebuilt loader implementation](https://github.com/apple-oss-distributions/dyld/blob/main/doc/dyld4.md),
including validated precomputed loading work and shared read-only mappings. That
supports removing redundant startup work; it is not the full UIKit/SpringBoard
application lifecycle source. Its
[launch-time guidance](https://developer.apple.com/documentation/xcode/reducing-your-app-s-launch-time)
also distinguishes launch from resumption and emphasizes measuring actual work.

Linux already provides
[GApplication uniqueness and activation](https://docs.gtk.org/gio/class.Application.html).
Phosh/postmarketOS use the same native GTK application ecosystem; changing the
shell alone does not remove each application's initialization. Historical
[Fedora readahead work](https://www.fedoraproject.org/wiki/Features/30SecondStartup/ReadAheadReloaded)
addresses boot/I/O prefetch, not a reason to run a periodic app scanner on a
battery device. This implementation uses bounded, explicit runtime preparation
and ordinary resident-window reuse instead.

## Cold-launch and first-frame audit, 2026-11-01

A second physical Titan audit ran two process-cold launches for each of 22
native Luma/system app entries. The display was 1080×1200 at 2× (540×600 logical).
The shared runtime was prepared; each target UI process was confirmed gone
before dispatch. Normal close preceded termination of any remaining known UI
PID. Background agents were preserved. Idle inhibition prevented automatic lock
from invalidating the run. No screenshots, profiling or extra redraw requests
were taken during timing. The measurement ends at the first focused, visible
app frame; it excludes network completion and is not a cold-boot benchmark.

Three distinct fixes came from this audit:

- GTK window realization synchronously decoded filtered application SVGs.
  Phone spent about 7.4 seconds inside `present()` waiting on libglycin; Python
  window construction took about 0.1 seconds. The icon package now renders
  fixed-size PNGs at build time, retaining the SVG sources and vector fallback.
  Logical sizes 16–128 at scales 1–4 cover GTK's window-icon enumeration without
  requesting oversized textures. Scale metadata matters: a 96px raster alone
  does not satisfy 96 logical pixels at 2×. Actual GTK lookups test every
  enumerated size at all four scales. Image sandboxing is unchanged.
- Mobile now chooses fullscreen geometry in Mutter's initial configure, before
  the client's first buffer. Previously Notes, Clock, Tasks and other apps
  sometimes appeared at remembered 360px window widths before expanding.
  Desktop and Tablet placement keep their existing policy. Returning a window
  first opened in Mobile to Desktop does not restore a zero-sized rectangle.
- The shared window `LayerHost` adapts watched widths before native overlay
  allocation, rather than rebuilding responsive UI after painting. Hidden
  controls, embedded previews and controls reparented during adaptation share
  that incoming width. GTK's overlay layout remains responsible for placement;
  there is no per-frame polling or extra idle animation. First-snapshot tests
  at 390/540/800/1100px widths fail on the preceding kit and pass with the fix.

The final run exposed 540×600 windows from the first visible frame in all 44
launches. An additional probe recorded Notes' first actual GTK snapshots with
its phone layout and list page already selected and stable. Phone likewise
selected its phone layout before its first snapshot. Actual touch reopening
passed three times each for Notes and Phone, retaining their existing UI PIDs.
Prepared module
provenance was also checked: the development host had retained installed modules
from before a source overlay became readable. A coherent source replacement and
host restart corrected that; existing app children survive the restart.

| Application | First visible frame, two cold UI processes |
| --- | ---: |
| Notes | 902–1194 ms |
| Phone | 506–564 ms |
| Weather | 510–529 ms |
| Contacts | 594–647 ms |
| Clock | 550–645 ms |
| Calendar | 605–676 ms |
| Tasks | 533–631 ms |
| Messages | 734–745 ms |
| Photos | 438–466 ms |
| Camera | 378–418 ms |
| Voice Memos | 482–489 ms |
| Tide | 1523–1537 ms |
| Monitor | 620–620 ms |
| Terminal | 1046–1071 ms |
| Settings | 1095–1293 ms |
| Filer | 1153–1239 ms |
| Luma Connect | 837–937 ms |
| Depot | 1840–2387 ms |
| Charlie | 1602–1889 ms |
| Search settings | 647–1085 ms |
| Mods | 1302–1375 ms |
| Valet | 1034–1181 ms |

Phone's preceding two readings were 7984/7933 ms; the final readings were
506/564 ms, about 93% less waiting in this setup. A separate controlled
same-source icon comparison also fell from 7863 to 514 ms. These are Phone
results, not a claim that every app or device became that much faster.

Notes and Phone no longer need an initial desktop-shaped UI followed by a
phone-shaped rebuild. Depot and Charlie still have larger startup budgets;
this audit does not claim every app is instant. Depot profiling shows catalogue,
AppStream and installed-package work across its asynchronous providers as well
as initial imports and view construction. Tide also performs asynchronous source
and artwork work. Concurrent profiler totals are not main-thread blocking time;
inspect thread state before attributing a network call to the first-frame path.
Full package/image builds and broad cross-device qualification remain separate
from the tested Titan source deployment.

### Launch continuity and platform references

Android uses starting windows and can reuse a task snapshot until the real
activity draws. iOS also captures UI when suspending an app and can show that
snapshot on restoration. Neither technique makes a slow app interactive sooner;
state, geometry and privacy must agree before replacing a snapshot with live
content. This audit fixes measured work and first-frame state without adding a
snapshot cache or exposing stale private app content.

Primary references: [Android startup timing and TTID/TTFD](https://developer.android.com/topic/performance/issues/launch-time),
[AOSP task snapshots](https://source.android.com/docs/core/perf/task-snapshots),
[Apple launch profiling](https://developer.apple.com/documentation/xcode/reducing-your-app-s-launch-time),
[Apple snapshot and launch-screen behavior](https://developer.apple.com/documentation/technotes/tn3118-debugging-your-apps-launch-screen).
