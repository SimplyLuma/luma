# Shared desktop browser engines

The native Viola host, packaging and UI source are shared between desktop CPUs.
The engine is an architecture-specific retained input, with no Chromium rebuild
needed to change the host UI.

| CPU | Retained engine | Archive SHA-256 |
| --- | --- | --- |
| x86_64 | native39 | `8a2efa725a32d529f7fd45edd9d96c0983436eeaa0b8f1904f79ed4a9b350b4e` |
| aarch64 | native40 | `48c7b0d15486ab5f17e5f6751b2d3b73b4016b4ca9b0ef3e2723c9ed327f7706` |

The ARM desktop preview requires native32 or later. Its source-built engine
includes the native shared-memory frame transport used by the installed
`desktop-arm-preview` profile (engine patch 0002). native33 is native32 with
engine patches 0003 (one tab in a new private window) and 0004 (a site's
HTTP sign-in drawn by the host). native36 is native33 with engine patch 0005
(WebRTC readback fallback outside the context lock). native38 is native36 with
engine patch 0006 (extension prompts drawn by the host), and native40 is native38
with engine patch 0007 (audio streams named Viola); only `chrome` differs.
The ARM64 executable has SHA-256
`404ed0a4906979decbd762f854b8a17f6b76e4d25263060c81cad48aefeae2c5`. On x86,
native37 is native35 with engine patch 0006, and native39 is native37 with 0007.
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
