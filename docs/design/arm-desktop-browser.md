# Shared desktop browser engines

The native Viola host, packaging and UI source are shared between desktop CPUs.
The engine is an architecture-specific retained input, with no Chromium rebuild
needed to change the host UI.

| CPU | Retained engine | Archive SHA-256 |
| --- | --- | --- |
| x86_64 | native43 | `74e601f2714004930ee30ac987259167258678e13bc7fa0aebb835e9a9ddd4c9` |
| aarch64 | native44 | `16e2260e58db29294550594cca36e54fdf70ca4ed241af08857c4fe38ce2ace2` |

The ARM desktop preview requires native32 or later. Its source-built engine
includes the native shared-memory frame transport used by the installed
`desktop-arm-preview` profile (engine patch 0002). native33 is native32 with
engine patches 0003 (one tab in a new private window) and 0004 (a site's
HTTP sign-in drawn by the host). native36 is native33 with engine patch 0005
(WebRTC readback fallback outside the context lock). native38 is native36 with
engine patch 0006 (extension prompts drawn by the host), native40 is native38
with engine patch 0007 (audio streams named Viola), native42 is native40
with engine patch 0008 (extension removal, menus and popups for the host), and
native44 is native42 with engine patch 0009 (render-state churn between windows);
only `chrome` differs.
The ARM64 executable has SHA-256
`227a684dfca3b840f45fe78732af0d838a47086286065b73ce6353c0b791cae4`. On x86,
native37 is native35 with engine patch 0006, native39 is native37 with 0007,
native41 is native39 with 0008, and native43 is native41 with 0009.
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
