# LumaUI conformance tools

These tools compare native application captures with supplied design references.
They measure configured static states and geometry; they do not replace functional,
accessibility, hardware, or release qualification.

## Run a comparison

From the repository root, with the required design references and capture runtime
configured:

```sh
LUMAUI_CONFORM_HOST=local tools/lumaui-conform/run.sh contacts --theme light
LUMAUI_CONFORM_HOST=local tools/lumaui-conform/run.sh contacts --phone
LUMAUI_CONFORM_HOST=local tools/lumaui-conform/run.sh contacts --all
tools/lumaui-conform/run.sh contacts --compare-only YOUR_CAPTURE_DIRECTORY
```

The exit code is 0 for a comparison PASS, 1 for FAIL, and 2 or greater when the
comparison could not run. A preparation or unavailable-runtime result is not PASS.

## Required inputs

The reference design/Studio files and service are external inputs, not a complete
standalone design server bundled by this source publication. Configure
`LUMAUI_CONFORM_BASE` and `LUMAUI_CONFORM_STUDIO_ROOT` for the references you supply.
The capture environment also needs Playwright/Chromium, Node, the application and
its GTK/Luma platform dependencies, and a suitable isolated graphical session.
`LUMAUI_CONFORM_PLAYWRIGHT` can select the Playwright installation.

Application scenarios declare their source, reference states and actions. Use
synthetic fixture data; captures and reports must not contain personal accounts,
messages, credentials, or documents.

## Capture environments

`LUMAUI_CONFORM_HOST=local` performs captures locally. The `server` route requires
a separately configured capture service using `LUMAUI_CONFORM_SERVER` and
`LUMAUI_CONFORM_PORT`. The historical `thinkpad` route also requires an explicitly
configured external host through `LUMAUI_HOST`; its name is a tool configuration,
not a device requirement for Luma itself. These remote routes are not publicly
hosted services supplied with a fresh clone.

Use the scripts under `server/` only in a disposable environment after inspecting
their package, service, source and reference inputs. Do not deploy them onto a
machine with personal state merely to run a comparison. Builder package pins and
reference/runtime versions must correspond to the component being measured.

Reports can be placed in a caller-selected directory with `LUMAUI_CONFORM_OUT`.
`LUMAUI_CONFORM_RENDERER` selects the GSK renderer; `LUMAUI_CONFORM_STATE_TIMEOUT`
sets the configured state timeout. Capture logs should name the actual source,
reference inputs and environment instead of relying on a private workspace path.

## Measurement limits

- Hover, focus and motion are not checked; states are static, captured after they
  settle.
- Letter spacing and line height are checked through text width and line count.
- Photos and hero washes are compared as sampled colour, not complete images.
- GTK popovers and in-window layer menus have different capture geometry.
- Chromium and Pango text measurements differ; configured tolerances must remain
  explicit and cannot establish a functional or hardware acceptance result.
