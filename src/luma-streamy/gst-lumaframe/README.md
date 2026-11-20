# lumaframe

GStreamer plugin `lumavideofx` (`libgstlumavideofx.so`) with one element,
`lumaframe`: a `GLFilter` that draws one layer of a composited scene in a
single GL pass, for `glvideomixer`.

- the source stretched into a box, masked by an anti-aliased rounded rect;
- a stroke just inside the box edge, following the corners;
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
| `margin` | ≥ 0, 0 | Transparent room on every side for the shadow |
| `crop-left`, `-top`, `-right`, `-bottom` | 0–0.49, 0 | Fraction of the source cut on that side; the rest fills the box |
| `corner-radius` | ≥ 0, 0 | Clamped to half the box's shorter side |
| `stroke-width` | ≥ 0, 0 | Outline inside the box edge; 0 = none |
| `stroke-color` | uint 0xAARRGGBB, 0xFFFFFFFF | Outline colour |
| `shadow-blur` | ≥ 0, 0 | Blur radius as CSS `box-shadow`: Gaussian sigma = blur / 2; 0 = sharp |
| `shadow-offset-x`, `-y` | any, 0 | Shadow offset; positive is right / down |
| `shadow-color` | uint 0xAARRGGBB, 0x66000000 | Alpha is the shadow's opacity; 0 = no shadow |
| `opacity` | 0–1, 1 | Multiplies content, stroke and shadow |

Output size is `round(box-width + 2·margin) × round(box-height + 2·margin)`.
A change that alters it renegotiates before the next buffer; any other change
only updates uniforms. If downstream fixes another size, the layer is scaled
to it. Place a layer in `glvideomixer` at its canvas position minus `margin`.
A margin of `shadow-blur` plus the larger offset holds the whole shadow.

`glvideomixer` (1.28) recomputes a pad's geometry only when a pad property
changes, not when its input caps do: when a layer's size changes, set the
mixer pad's `width`/`height` to the new output size at the same time. Several
GL pipelines in one process should share one `GstGLDisplay` (set a
`gst.gl.GLDisplay` context on each pipeline); with separate displays one
pipeline's teardown can break EGL context creation in another.

The shadow is drawn outside the box only (as CSS), so it never shows through
a translucent source. Fully transparent output pixels are transparent black.
With the defaults the element is a passthrough.

## Example

```sh
gst-launch-1.0 videotestsrc ! glupload ! glcolorconvert ! \
  lumaframe box-width=640 box-height=360 margin=40 corner-radius=24 \
    stroke-width=4 shadow-blur=30 shadow-offset-y=8 ! \
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
