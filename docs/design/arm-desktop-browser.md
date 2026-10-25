# Shared desktop browser engines

The native Viola host, packaging and UI source are shared between desktop CPUs.
The engine is an architecture-specific retained input, with no Chromium rebuild
needed to change the host UI.

| CPU | Retained engine | Archive SHA-256 |
| --- | --- | --- |
| x86_64 | native30 | `2760bcadbb3864ffb637888b06e9717642c1add31656c2b81c6d0fb7fd5b1028` |
| aarch64 | native32 | `2d10bdd8dc21364ee91cde00abef725fd7e3e13658fd059275057663b129d4b1` |

The ARM desktop preview requires native32 or later. Its source-built engine
includes the native shared-memory frame transport used by the installed
`desktop-arm-preview` profile. The ARM64 executable has SHA-256
`0c4829cf130a9490489e2dfb81d9cb1fe156ff3701511e3c94ca4b0af5f53b40`.
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
