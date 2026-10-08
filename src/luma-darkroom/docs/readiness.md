# Darkroom readiness

## Implemented and testable

- One adaptive desktop/handheld GTK application and one application ID.
- Versioned non-destructive document, atomic save, autosave, recovery, and
  relink state.
- Real exposure, tonal, white-balance, color, texture, clarity, curve,
  sharpening, noise-reduction, crop, masked adjustment-layer, blend, retouch,
  histogram, preview, and source-resolution export paths.
- Layers, masks, human history, undo/redo coalescing, snapshots, conditional
  canvas menu, keyboard tools, drag-open/place, and background rendering.
- Light, Dark, Frost, and Glass presentation classes without changing image
  pixels or histogram data.
- Shared AppKit histogram and keyboard-operable curve widgets.

## Honest release gates

- RAW decoding uses the required system LibRaw library. Generated CFA DNG and
  a compressed Fujifilm X-T20 RAF were verified for preview and full-resolution
  export. Further camera models, compression modes, and AArch64 remain release
  acceptance gates; damaged or unsupported files preserve the previous photo.
- Global RAW development retains 16-bit camera samples and uses float32 through
  exposure, WB, tonal, colour and curve operations. Spatial finishing, layers and
  delivery remain 8-bit. The renderer is whole-frame; a tiled backend and broader
  precision/colour validation remain gates for large-RAW production readiness.
  See [linear RAW evidence and limits](linear-raw-development.md).
- Display ICC conversion is active for embedded profiles. Arbitrary working-
  space transforms, soft-proof paper profiles, gamut overlays, and full
  rendering-intent selection require an installed system color engine.
- Subject, sky, and content-aware masks require reviewed local models. No fake
  automatic-selection result is generated when those models are absent.
- HEIF/AVIF availability follows the installed Pillow codecs and must be
  accepted on the release image.
- Stylus pressure/tilt and physical-device palm rejection require FP6 hardware
  validation below the shared UI.

