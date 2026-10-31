# Display exclusions

A device profile can install its JSON as `/usr/share/luma/display-geometry.json`.
An owner override at `/etc/luma/display-geometry.json` takes precedence. Displays
without metadata keep their existing insets.

Schema 1 specifies one panel: its connector (optional), native pixel dimensions
and rectangular camera/corner exclusions. Schema 2 lists several panels under
`displays`, each with a required, unique connector and the same fields; use it
for devices with more than one built-in display, such as foldables:

```json
{"schema": 2, "displays": [
  {"connector": "DSI-1", "nativeWidth": 1080, "nativeHeight": 2424, "cutouts": []},
  {"connector": "DSI-2", "nativeWidth": 2076, "nativeHeight": 2152, "cutouts": []}
]}
```

On every monitor change the Shell reads the primary display's connector and
transform from Mutter and selects the matching profile (a schema 1 profile
without a connector matches any built-in primary). A primary display with no
profile, or an external one, uses the normal insets until the next change
selects a profile again; no profile is discarded. The shared Shell projects them through Mutter's native
monitor transform. Corner exclusions move only affected status content; centered
exclusions do not add padding to the left clock. Full-screen surfaces fill the
panel while placing their header content clear of the exclusion.

The Titan profile uses a conservative 132 × 132 native-pixel top-left exclusion,
calibrated visually on the owner's device. It is not an exact hardware measurement.
Refine that profile independently of shared layout code.

The Pixel 9 Pro Fold profile (`google-comet.json`) lists provisional corner
exclusions for both panels. It is an estimate, not a measurement: calibrate it
on the device, add the camera openings, and install it from the device image.
