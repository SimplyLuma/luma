# Shared desktop browser engines

The native Viola host, packaging and UI source are shared between desktop CPUs.
The engine is an architecture-specific retained input, with no Chromium rebuild
needed to change the host UI.

| CPU | Retained engine | Archive SHA-256 |
| --- | --- | --- |
| x86_64 | native37 | `4d15cc99a8f59323ab870099d751e38bf40b05d546f3fbe6f0192327e85cc171` |
| aarch64 | native38 | `a1e6faffb076ad96041b1413e95dd4b7eb164f56a8579d466f260be2cf8270a6` |

The ARM desktop preview requires native32 or later. Its source-built engine
includes the native shared-memory frame transport used by the installed
`desktop-arm-preview` profile (engine patch 0002). native33 is native32 with
engine patches 0003 (one tab in a new private window) and 0004 (a site's
HTTP sign-in drawn by the host). native36 is native33 with engine patch 0005
(WebRTC readback fallback outside the context lock). native38 is native36 with
engine patch 0006 (extension prompts drawn by the host); only `chrome` differs.
The ARM64 executable has SHA-256
`a46a945c274262121f93c4f0a56f83b348b2c2d44babf7387719aaf564241df4`. On x86,
native37 is native35 with engine patch 0006.
The earlier mobile native31 input remains historical provenance; it cannot
substitute for the desktop preview's current engine. Upstream notices and license
files remain inside the admitted archive.

An admitted input is not runtime qualification. The ordinary RPM check suite and
real installed-browser checks must pass before release. The architecture chooses the
engine input; it does not select a second application source tree or UI fork.

Both retained engine inputs are available in content-addressed
`media.simplyluma.com/inputs/viola-engine/<archive-sha256>/<filename>` paths,
with original license notices beside them. Native browser CI selects both CPUs
for shared host or packaging changes. An architecture-specific engine manifest
change selects only its CPU; workflow-only edits preserve already checked
packages. An explicit workflow dispatch requests fresh builds on both CPUs.

## Native runtime checks

`desktop-browser-runtime.yml` runs the installed ARM package on a native ARM
worker without rebuilding Chromium. Its fresh GNOME fixture initializes an
unlocked encrypted keyring, as a normal PAM login would, before starting the
browser. Otherwise Chromium can wait for an invisible Secret Service prompt.

The fixture captures the actual compositor through PipeWire and admits real
pointer and keyboard events through the compositor. Acceptance requires matching
window, target and tab identities, trusted DOM events, fresh presented frames,
pixel samples, resizing, minimizing/restoring, and a successful host/engine close
with no outstanding frame leases. A successful RPM run establishes that package's
mechanism; it does not qualify a different Flatpak deployment or an OS image.
Signed Flatpak installation and exact-image install/update checks remain separate
release requirements.
