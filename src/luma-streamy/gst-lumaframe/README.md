# lumaframe

GStreamer plugin `lumavideofx` (`libgstlumavideofx.so`) with one element,
`lumaframe`: a `GLFilter` that draws one layer of a composited scene in a
single GL pass, for `glvideomixer`.

- the source stretched into a box, masked by an anti-aliased rounded rect;
- a stroke inside, centred on or outside the box edge, following the
  corners;
- an analytic Gaussian drop shadow in a transparent margin (the closed-form
  blurred rounded rect described in "Fast Rounded Rectangle Shadows",
  madebyevan.com; erf across x, exact Gaussian-mass slices down y);
- a crop of the source and an opacity for the whole layer.

Caps are `video/x-raw(memory:GLMemory), format=RGBA, texture-target=2D` on
both pads. Output is straight alpha, so `glvideomixer`'s default blend
composites it; colours stay in the input's space. The shader is GLSL ES 1.00 /
GLSL 1.20 and runs on desktop GL and GLES.

## Properties

All are `double` unless noted and changeable in PLAYING. Sizes are canvas
pixels.

| Property | Range, default | Meaning |
| --- | --- | --- |
| `box-width`, `box-height` | ≥ 0, 0 | Content box size; 0 = input size on that axis |
| `margin` | ≥ 0, 0 | Transparent room on every side for the shadow and an outside stroke |
| `content-x`, `content-y` | ≥ 0, 0 | Origin of the source region the box shows, source pixels |
| `content-width`, `content-height` | ≥ 0, 0 | Size of that region, source pixels; 0 = to the input's edge. With `content-width` 0, a buffer's `GstVideoCropMeta` gives the region |
| `crop-left`, `-top`, `-right`, `-bottom` | 0–0.49, 0 | Fraction of the content region cut on that side; the rest fills the box |
| `corner-radius` | ≥ 0, 0 | Clamped to half the box's shorter side |
| `stroke-width` | ≥ 0, 0 | Outline along the box edge; 0 = none |
| `stroke-color` | uint 0xAARRGGBB, 0xFFFFFFFF | Outline colour |
| `stroke-position` | enum `inside`, `center`, `outside`; `inside` | Which side of the box edge the outline lies on |
| `shadow-blur` | ≥ 0, 0 | Blur radius as CSS `box-shadow`: Gaussian sigma = blur / 2; 0 = sharp |
| `shadow-offset-x`, `-y` | any, 0 | Shadow offset; positive is right / down |
| `shadow-color` | uint 0xAARRGGBB, 0x66000000 | Alpha is the shadow's opacity; 0 = no shadow |
| `opacity` | 0–1, 1 | Multiplies content, stroke and shadow |

Output size is `round(box-width + 2·margin) × round(box-height + 2·margin)`.
A change that alters it renegotiates before the next buffer; any other change
only updates uniforms. If downstream fixes another size, the layer is scaled
to it. Place a layer in `glvideomixer` at its canvas position minus `margin`.

`content-*` pick the part of the source the box shows, before `crop-*`:
for a window cast inside a larger buffer, the window's rect. With
`content-width` 0, a `GstVideoCropMeta` on the input buffer gives that rect
per buffer, so a window resize follows; `glupload` and `glcolorconvert` pass
the meta through. The region is clamped to the input. Changing it only
updates uniforms: the box size (and so the output size) stays `box-width` ×
`box-height`, or the input size where those are 0.

`stroke-position` moves the stroke without moving the box: `inside` covers
the content's edge, `center` straddles it (±`stroke-width`/2) and `outside`
lies wholly in the margin. Corners stay concentric: the stroke's outer edge
is the box grown by its reach (0, `stroke-width`/2 or `stroke-width`) with
radius `corner-radius` + reach. The shadow is cast by that outer edge and
starts beyond it. The inward part of a stroke is clamped at the box's centre.

The margin must hold the stroke's reach plus the shadow. Three sigmas of blur
(1.5 × `shadow-blur`) leave less than 0.2 % of the shadow's opacity, so:

```
margin = ceil(reach + 1.5 · shadow-blur + max(|shadow-offset-x|, |shadow-offset-y|))
reach  = 0 (inside), stroke-width / 2 (center), stroke-width (outside)
```

with the shadow terms 0 when `shadow-color`'s alpha is 0. In Rust it is
`gstlumavideofx::geometry::margin_for(&settings)`; in Python:

```python
import math

def lumaframe_margin(stroke_width, stroke_position, shadow_blur,
                     shadow_x, shadow_y, shadow_alpha):
    reach = {"inside": 0.0, "center": 0.5, "outside": 1.0}[stroke_position] * stroke_width
    shadow = 1.5 * shadow_blur + max(abs(shadow_x), abs(shadow_y)) if shadow_alpha > 0 else 0.0
    return float(math.ceil(reach + shadow))
```

`glvideomixer` (1.28) recomputes a pad's geometry only when a pad property
changes, not when its input caps do: when a layer's size changes, set the
mixer pad's `width`/`height` to the new output size at the same time. Several
GL pipelines in one process should share one `GstGLDisplay` (set a
`gst.gl.GLDisplay` context on each pipeline); with separate displays one
pipeline's teardown can break EGL context creation in another.

The shadow is drawn outside the box and stroke only (as CSS), so it never
shows through a translucent source or stroke. Fully transparent output
pixels are transparent black.
With the defaults the element is a passthrough.

## Example

```sh
gst-launch-1.0 videotestsrc ! glupload ! glcolorconvert ! \
  lumaframe box-width=640 box-height=360 margin=57 corner-radius=24 \
    stroke-width=4 stroke-position=outside shadow-blur=30 shadow-offset-y=8 ! \
  glvideomixer background=checker ! gldownload ! videoconvert ! autovideosink
```

## Build and test

Needs Rust 1.92+, GStreamer 1.24+ development files (`gstreamer1-devel`,
`gstreamer1-plugins-base-devel`) and, for the pipeline tests, a GL driver.

```sh
cargo build --release --locked          # target/release/libgstlumavideofx.so
GST_PLUGIN_PATH=$PWD/target/release gst-inspect-1.0 lumaframe

cargo test --locked --lib               # geometry and shadow maths
GST_GL_PLATFORM=egl GST_GL_WINDOW=surfaceless LIBGL_ALWAYS_SOFTWARE=1 \
  cargo test --locked --test pipeline   # real pipelines, headless Mesa llvmpipe
```

`src/geometry.rs` is the CPU model of the shader (`src/lumaframe.frag`); the
pipeline tests compare GPU output with it pixel by pixel, so change both
together.

## Licence

MPL-2.0.
