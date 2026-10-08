# RAW development correction — 2026-09-22

## Audit finding

The previous implementation decoded actual RAW sensor data, but converted it to
8-bit, gamma-encoded RGB before adjustments. Exposure multiplied those encoded
values by 2^EV, clipping them before a per-channel highlight LUT. Lowering
Highlights could then darken a white patch but could not recover the distinctions
that had already been discarded. LibRaw's default automatic brightening also
changed the starting image. These were substantive processing defects, not just
UI problems. The old claim of genuine sensor decoding was insufficient to claim
a correct RAW development pipeline.

## Owned correction

LibRaw still owns decoding, sensor black subtraction/normalization, as-shot WB,
demosaicing and orientation. Its public C API now requests 16-bit camera-space
output, linear gamma, no auto brightening, no per-image maximum adjustment and
highlight mode 1. That mode normalizes WB by the largest gain so the integer
camera channels keep their headroom. Darkroom restores that normalization in
float32 and applies LibRaw's camera-to-linear-sRGB matrix in floating point,
rather than letting the library clip its matrix result into uint16 sRGB.
Negative and above-display-white matrix values remain available until output.

The immutable camera sample cache retains at most one 192 MiB source. Preview
resizing operates on floating camera-channel planes; the cached fit and drag
sources remain linear. Exposure is multiplication by 2^EV in linear light.
Relative Warmth/Tint gains apply in camera space before the matrix. They are
relative to as-shot WB, not estimated Kelvin values. Contrast pivots around
linear middle gray. Shadows targets the toe, Highlights uses a monotonic
luminance shoulder, Whites changes the upper range, and positive Blacks lifts
the black point. Colour operations and the RAW tone curve run before 8-bit
quantization. These are Luma's defined controls, not claimed replicas of Adobe's
proprietary rendering or slider calibration.

NumPy supplies compiled float arithmetic; Pillow resamples float planes. No
resident helper, service, disk image cache, or GPU framework was added. The final
delivery conversion runs in 256-row strips to bound intermediate arrays. The
same path feeds previews and full-resolution export. An 8-bit image enters the
existing spatial finishing/crop/layer/retouch pipeline only after RAW development.
The export UI offers 8-bit delivery. Model/API requests for 16-bit export are now
rejected explicitly; the former implementation silently wrote 8-bit RGB even
when 16-bit TIFF or PNG had been requested.

The detail audit also found unsigned/clamped local-detail subtraction plus a
brightness offset, which changed flat fields. Texture/Clarity now use signed
local differences at separate, resolution-scaled radii. Dehaze was a global
contrast approximation, so it is no longer offered in the inspector. Older
recipes retain that legacy operation for compatibility; it is not a new haze
removal implementation.

## Evidence observed

- 43 unit tests pass, including exact photographic EV ratios; high-bit-depth
  sample preservation; monotonic controls; highlight/shadow range isolation;
  camera-matrix headroom; tone curves before quantization; unchanged alpha;
  flat-field invariance; and rejection of false 16-bit export requests.
- A generated CFA DNG regression exposes the former clipping bug: the previous
  renderer leaves one colour in the test highlight patch after recovery; the
  new renderer retains multiple distinguishable tones. The regression was run
  against the prior committed package source and failed, then passed here.
- The public CC0 X-T20 RAF decodes to 6032 × 4028 uint16 camera samples. Its
  linear RGB preview spans approximately -0.040 to 1.532, demonstrating retained
  gamut/headroom outside display range. Unclipped midtones agree with LibRaw's
  independent 16-bit linear sRGB output with mean absolute difference 0.000084
  across 560,694 preview pixels (the remaining small difference includes integer
  scaling/demosaic rounding in the reference path).
- Prepared 960px RAF exposure development measured 21–27ms on the ThinkPad.
  GTK showed 21 distinct frames during 30 rapid slider changes and refined after
  settling. The first decode remains about six seconds on this CPU.
- Full-resolution RAF export with exposure/highlights/shadows/WB adjustments
  completed at 6032 × 4028 in 13.21 seconds, including initial decode and PNG
  encoding; peak process RSS was 739 MiB. Original SHA-256 remained unchanged.
- Gallery/preview race/undo/recovery checks and workspace checks pass, including
  light/dark, 360/420/500/1024 widths, comparison and broken-file recovery. Runtime
  tests use disposable files and recovery stores.

## Dependency, limits and rollback

`python3-numpy` is now an explicit build/runtime RPM dependency. Live validation
uses unpacked Fedora NumPy/LibRaw dependency RPMs in the private preview directory;
the host installation is unchanged. See NUMPY-DARKROOM-FLOAT in the source
register. The coordinator owns package/composition and architecture acceptance.

This corrects global RAW development. It does not claim a fully floating-point
layer/retouch pipeline, a tiled renderer, ICC-managed HDR display, 16-bit delivery,
calibrated Kelvin WB, camera-specific creative profiles, lens correction or
reconstruction of sensor-saturated channels. Truly clipped sensor data cannot
be restored by lowering Highlights. JPEG sources have no hidden RAW headroom.
More camera models and AArch64 remain release gates.

Existing recipes retain their numeric values but will render differently with
the corrected tonal math and disabled auto brightening. Originals and recipe
schema are unchanged. Revert this change to restore the previous interpretation;
keep all editable documents and recovery files. Never overwrite an original RAW.

Primary references (no upstream implementation copied):
- https://www.libraw.org/docs/API-C.html
- https://www.libraw.org/docs/API-datastruct.html
- https://github.com/LibRaw/LibRaw/blob/0.22.0/src/postprocessing/postprocessing_utils_dcrdefs.cpp
- https://numpy.org/doc/stable/reference/generated/numpy.einsum.html
