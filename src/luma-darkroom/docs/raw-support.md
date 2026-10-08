# RAW input and Canvas property metrics

The RAW-open failure was caused by accepting camera files without packaging a
RAW decoder. The app now requires LibRaw >= 0.22 and uses its documented C API
through a small ctypes adapter. It decodes actual sensor data, honors camera
orientation/white balance, and supplies sRGB pixels to the existing pipeline.
Both preview and export use this path. No package downloads run inside the app.

The preview session uses the exact Fedora LibRaw 0.22.2 RPM unpacked in its
private runtime directory; the installed system is unchanged. Production gets
the same dependency through RPM Requires. The preview override is
`LUMA_DARKROOM_LIBRAW`; production discovers the reentrant system library.

Validation observed:
- 30 application tests passed, including a generated CFA DNG with no embedded
  thumbnail, full-resolution export, malformed input, and source byte identity.
- The DNG regression was run against the prior engine and reproduced the exact
  missing-decoder error before passing with the new decoder.
- A CC0 compressed Fujifilm X-T20 RAF from raw.pixls.us decoded at 6032 × 4028
  and exported at full resolution. Source and checksum are in the provenance
  register. This is evidence for that RAF sample, not every Fujifilm camera.
- GTK workspace tests cover failed-open recovery without a blocking dialog and
  retention of the previous photo, recipe, and undo state.

The inspector follows the installed Canvas source's current compact-value
metrics: 11px property labels, 12px values, 24px section titles, 12px side
insets, 8px control spacing, and semantic faint section dividers. AppKit
NumericField owns numeric entry/edit behavior; AppKit Island owns the surfaces.
Zoom moved to the photo toolbar and the old bottom status strip was removed.

Package release validation is the coordinator's batch task. No live machine
package was installed or replaced. Roll back by quitting the source preview and
launching the installed app, or reverting this follow-up commit. Original files
and document formats are unchanged.

## RAW precision correction

The earlier 8-bit development boundary described above is superseded by the
[linear RAW development correction](linear-raw-development.md). Camera samples
remain 16-bit, and primary RAW adjustments use float32 before display conversion.
The linked document records tests, dependency changes and remaining limits.
