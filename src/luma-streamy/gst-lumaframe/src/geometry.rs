// SPDX-License-Identifier: MPL-2.0
//! The layer model `lumaframe` draws, in plain Rust.
//!
//! Everything here mirrors `lumaframe.frag` line for line: the element uses
//! [`Layout`] to compute the output size and the shader uniforms, and the
//! tests use [`Layout::shade`] as the reference the GPU output is compared
//! against. Coordinates are canvas pixels with the origin at the top-left of
//! the padded output; the content rect starts at (`margin`, `margin`).

use gst::glib;

/// Where the stroke lies relative to the content box edge.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash, glib::Enum)]
#[repr(u32)]
#[enum_type(name = "GstLumaFrameStrokePosition")]
pub enum StrokePosition {
    /// Inside the box, covering the content's edge.
    #[default]
    #[enum_value(name = "Inside the box edge", nick = "inside")]
    Inside = 0,
    /// Straddling the box edge, half inside and half outside.
    #[enum_value(name = "Centred on the box edge", nick = "center")]
    Center = 1,
    /// Outside the box, in the margin; the box itself is unchanged.
    #[enum_value(name = "Outside the box edge", nick = "outside")]
    Outside = 2,
}

impl StrokePosition {
    /// How far a stroke `width` wide reaches beyond the box edge.
    pub fn reach(self, width: f64) -> f64 {
        match self {
            StrokePosition::Inside => 0.0,
            StrokePosition::Center => 0.5 * width,
            StrokePosition::Outside => width,
        }
    }
}

/// The element's properties, already range-checked by GObject.
#[derive(Clone, Debug, PartialEq)]
pub struct Settings {
    pub box_width: f64,
    pub box_height: f64,
    pub margin: f64,
    pub content_x: f64,
    pub content_y: f64,
    pub content_width: f64,
    pub content_height: f64,
    pub crop_left: f64,
    pub crop_top: f64,
    pub crop_right: f64,
    pub crop_bottom: f64,
    pub corner_radius: f64,
    pub stroke_width: f64,
    pub stroke_color: u32,
    pub stroke_position: StrokePosition,
    pub shadow_blur: f64,
    pub shadow_offset_x: f64,
    pub shadow_offset_y: f64,
    pub shadow_color: u32,
    pub opacity: f64,
}

pub const DEFAULT_STROKE_COLOR: u32 = 0xFFFF_FFFF;
pub const DEFAULT_SHADOW_COLOR: u32 = 0x6600_0000;

impl Default for Settings {
    fn default() -> Self {
        Settings {
            box_width: 0.0,
            box_height: 0.0,
            margin: 0.0,
            content_x: 0.0,
            content_y: 0.0,
            content_width: 0.0,
            content_height: 0.0,
            crop_left: 0.0,
            crop_top: 0.0,
            crop_right: 0.0,
            crop_bottom: 0.0,
            corner_radius: 0.0,
            stroke_width: 0.0,
            stroke_color: DEFAULT_STROKE_COLOR,
            stroke_position: StrokePosition::Inside,
            shadow_blur: 0.0,
            shadow_offset_x: 0.0,
            shadow_offset_y: 0.0,
            shadow_color: DEFAULT_SHADOW_COLOR,
            opacity: 1.0,
        }
    }
}

/// 0xAARRGGBB to straight-alpha RGBA in 0..1.
pub fn argb_to_rgba(argb: u32) -> [f64; 4] {
    let c = |shift: u32| ((argb >> shift) & 0xff) as f64 / 255.0;
    [c(16), c(8), c(0), c(24)]
}

/// The content box size on the canvas: `box-width`/`box-height`, or the
/// input size on an axis where they are 0.
pub fn box_size(s: &Settings, input: (i32, i32)) -> (f64, f64) {
    let pick = |b: f64, i: i32| if b > 0.0 { b } else { i.max(1) as f64 };
    (pick(s.box_width, input.0), pick(s.box_height, input.1))
}

/// The canvas size: the box plus `margin` on every side.
pub fn canvas_size(s: &Settings, input: (i32, i32)) -> (f64, f64) {
    let (bw, bh) = box_size(s, input);
    (bw + 2.0 * s.margin, bh + 2.0 * s.margin)
}

/// The output size the element asks for: the canvas rounded to whole pixels.
pub fn output_size(s: &Settings, input: (i32, i32)) -> (i32, i32) {
    let (cw, ch) = canvas_size(s, input);
    let px = |v: f64| v.round().clamp(1.0, i32::MAX as f64) as i32;
    (px(cw), px(ch))
}

/// The smallest `margin` that holds the whole layer: the stroke's reach
/// beyond the box plus, with a shadow, three sigmas of blur (1.5 ×
/// `shadow-blur`) and the larger offset, rounded up to whole pixels. The
/// shadow is a blur of the stroke's outer edge, so it starts past the reach.
pub fn margin_for(s: &Settings) -> f64 {
    let reach = s.stroke_position.reach(s.stroke_width);
    let shadow = if (s.shadow_color >> 24) > 0 {
        1.5 * s.shadow_blur + s.shadow_offset_x.abs().max(s.shadow_offset_y.abs())
    } else {
        0.0
    };
    (reach + shadow).ceil()
}

/// A `GstVideoCropMeta` rectangle: x, y, width, height in source pixels.
pub type CropMeta = (u32, u32, u32, u32);

/// The whole input in texture coordinates: origin and extent.
pub const WHOLE_INPUT: [f64; 4] = [0.0, 0.0, 1.0, 1.0];

/// The part of the source the box shows before `crop-*`, in source pixels
/// (x, y, width, height), clamped to the input.
///
/// With `content-width` 0 a buffer's crop meta, if it has a non-empty one,
/// gives the whole rect. Otherwise `content-x`/`-y` set the origin and a
/// `content-width`/`-height` of 0 runs to the input's edge on that axis, so
/// the defaults are the whole input.
pub fn content_rect(s: &Settings, input: (i32, i32), meta: Option<CropMeta>) -> (f64, f64, f64, f64) {
    let (iw, ih) = (input.0.max(1) as f64, input.1.max(1) as f64);
    let (x, y, w, h) = match meta {
        Some((mx, my, mw, mh)) if s.content_width <= 0.0 && mw > 0 && mh > 0 => {
            (mx as f64, my as f64, mw as f64, mh as f64)
        }
        _ => (s.content_x, s.content_y, s.content_width, s.content_height),
    };
    let axis = |o: f64, len: f64, full: f64| {
        let o = o.clamp(0.0, full - 1.0);
        let rest = full - o;
        (o, if len > 0.0 { len.min(rest) } else { rest })
    };
    let (x, w) = axis(x, w, iw);
    let (y, h) = axis(y, h, ih);
    (x, y, w, h)
}

/// [`content_rect`] in texture coordinates: origin and extent, 0..1.
pub fn content_region(s: &Settings, input: (i32, i32), meta: Option<CropMeta>) -> [f64; 4] {
    let (iw, ih) = (input.0.max(1) as f64, input.1.max(1) as f64);
    let (x, y, w, h) = content_rect(s, input, meta);
    [x / iw, y / ih, w / iw, h / ih]
}

/// Source texture coordinates of the visible part, origin and extent: the
/// `crop-*` fractions cut from `region` (origin and extent, 0..1).
pub fn crop_rect(s: &Settings, region: [f64; 4]) -> [f64; 4] {
    [
        region[0] + s.crop_left * region[2],
        region[1] + s.crop_top * region[3],
        region[2] * (1.0 - s.crop_left - s.crop_right).max(0.0),
        region[3] * (1.0 - s.crop_top - s.crop_bottom).max(0.0),
    ]
}

/// Where a point of the box (0..1 on each axis) samples the source, for the
/// whole input as content.
pub fn crop_map(s: &Settings, box_uv: (f64, f64)) -> (f64, f64) {
    let c = crop_rect(s, WHOLE_INPUT);
    (
        c[0] + box_uv.0.clamp(0.0, 1.0) * c[2],
        c[1] + box_uv.1.clamp(0.0, 1.0) * c[3],
    )
}

/// Signed distance from `p` to a rounded rect (negative inside).
pub fn rounded_rect_sdf(p: (f64, f64), centre: (f64, f64), half: (f64, f64), radius: f64) -> f64 {
    let qx = (p.0 - centre.0).abs() - half.0 + radius;
    let qy = (p.1 - centre.1).abs() - half.1 + radius;
    let outside = (qx.max(0.0).powi(2) + qy.max(0.0).powi(2)).sqrt();
    outside + qx.max(qy).min(0.0) - radius
}

/// Area coverage of a pixel `aa` canvas px wide by a shape at distance `d`.
pub fn coverage(d: f64, aa: f64) -> f64 {
    (0.5 - d / aa).clamp(0.0, 1.0)
}

/// erf(x), Abramowitz and Stegun 7.1.27 (absolute error below 5e-4).
pub fn erf(x: f64) -> f64 {
    let a = x.abs();
    let t = 1.0 + a * (0.278393 + a * (0.230389 + a * (0.000972 + a * 0.078108)));
    let t2 = t * t;
    x.signum() * (1.0 - 1.0 / (t2 * t2))
}

/// Number of slices the shadow's vertical integral uses.
pub const SHADOW_SLICES: usize = 6;
/// The Gaussian is integrated over ±this many sigma.
pub const SHADOW_EXTENT: f64 = 4.0;

/// Gaussian-blurred rounded rect, 0..1, at `p` relative to the rect centre.
///
/// The blur is separable: across x the cross-section of a rounded rect at a
/// given height is a single interval, so its blur is a difference of two
/// erf terms. Down y the cross-section width changes only in the corners, so
/// that axis is integrated in slices, each weighted by its exact Gaussian
/// mass (an erf difference) and evaluated at its midpoint.
pub fn rounded_rect_shadow(p: (f64, f64), half: (f64, f64), radius: f64, sigma: f64) -> f64 {
    let k = std::f64::consts::FRAC_1_SQRT_2 / sigma;
    let lo = (p.1 - half.1).max(-SHADOW_EXTENT * sigma);
    let hi = (p.1 + half.1).min(SHADOW_EXTENT * sigma);
    if hi <= lo {
        return 0.0;
    }
    let step = (hi - lo) / SHADOW_SLICES as f64;
    let mut sum = 0.0;
    let mut t0 = lo;
    for _ in 0..SHADOW_SLICES {
        let t1 = t0 + step;
        // Gaussian mass of [t0, t1] in units of y.
        let mass = 0.5 * (erf(t1 * k) - erf(t0 * k));
        // The rect's cross-section at the slice's midpoint height.
        let y = p.1 - 0.5 * (t0 + t1);
        let dy = (y.abs() - (half.1 - radius)).max(0.0);
        let w = half.0 - radius + (radius * radius - dy * dy).max(0.0).sqrt();
        let across = 0.5 * (erf((p.0 + w) * k) - erf((p.0 - w) * k));
        sum += mass * across;
        t0 = t1;
    }
    sum
}

/// Below this many output pixels of sigma the shadow uses the plain
/// anti-aliased edge instead of the Gaussian.
pub const SHADOW_SIGMA_MIN_PX: f64 = 0.25;

/// Everything the shader needs for one frame, in canvas pixels.
#[derive(Clone, Debug, PartialEq)]
pub struct Layout {
    /// Canvas size (box plus margins).
    pub canvas: (f64, f64),
    /// Canvas pixels per output pixel.
    pub aa: f64,
    pub centre: (f64, f64),
    pub half: (f64, f64),
    pub radius: f64,
    pub stroke: f64,
    /// How far the stroke's inner edge lies inside the box edge.
    pub stroke_in: f64,
    /// How far the stroke's outer edge lies outside the box edge.
    pub stroke_out: f64,
    /// The outer edge of box and stroke: half size and corner radius, the
    /// box grown by `stroke_out` with concentric corners. The shadow is cast
    /// by this shape.
    pub outer_half: (f64, f64),
    pub outer_radius: f64,
    pub stroke_color: [f64; 4],
    /// Source origin and extent in texture coordinates.
    pub crop: [f64; 4],
    pub shadow_color: [f64; 4],
    pub shadow_offset: (f64, f64),
    /// Gaussian sigma: half of `shadow-blur`, as for CSS box-shadow.
    pub sigma: f64,
    pub opacity: f64,
    /// One output pixel's extent in texture coordinates.
    pub footprint: (f64, f64),
    /// Samples per axis spread over the footprint: 1 at up to 1.5 source
    /// pixels per output pixel, at most 4 (a sharp downscale, no aliasing).
    pub taps: f64,
}

impl Layout {
    pub fn new(s: &Settings, input: (i32, i32), output: (i32, i32)) -> Layout {
        Layout::for_frame(s, input, output, None)
    }

    /// The layout for one input buffer, with its crop meta if it has one.
    pub fn for_frame(s: &Settings, input: (i32, i32), output: (i32, i32), meta: Option<CropMeta>) -> Layout {
        let (bw, bh) = box_size(s, input);
        let canvas = canvas_size(s, input);
        let short = 0.5 * bw.min(bh);
        // Scale from output pixels to canvas pixels, averaged over the axes:
        // 1 when the output is the size the element asked for.
        let aa = 0.5 * (canvas.0 / output.0.max(1) as f64 + canvas.1 / output.1.max(1) as f64);
        let half = (0.5 * bw, 0.5 * bh);
        let radius = s.corner_radius.clamp(0.0, short);
        // The part of the stroke inside the box stops at the box's centre.
        let stroke = match s.stroke_position {
            StrokePosition::Inside => s.stroke_width.clamp(0.0, short),
            StrokePosition::Center => s.stroke_width.clamp(0.0, 2.0 * short),
            StrokePosition::Outside => s.stroke_width.max(0.0),
        };
        let stroke_out = s.stroke_position.reach(stroke);
        let crop = crop_rect(s, content_region(s, input, meta));
        let footprint = (crop[2] * aa / bw.max(1e-6), crop[3] * aa / bh.max(1e-6));
        let texels = (footprint.0 * input.0.max(1) as f64).max(footprint.1 * input.1.max(1) as f64);
        let taps = (texels / 1.5 - 1e-6).ceil().clamp(1.0, 4.0);
        Layout {
            canvas,
            aa,
            centre: (s.margin + 0.5 * bw, s.margin + 0.5 * bh),
            half,
            radius,
            stroke,
            stroke_in: stroke - stroke_out,
            stroke_out,
            outer_half: (half.0 + stroke_out, half.1 + stroke_out),
            outer_radius: radius + stroke_out,
            stroke_color: argb_to_rgba(s.stroke_color),
            crop,
            shadow_color: argb_to_rgba(s.shadow_color),
            shadow_offset: (s.shadow_offset_x, s.shadow_offset_y),
            sigma: 0.5 * s.shadow_blur,
            opacity: s.opacity.clamp(0.0, 1.0),
            footprint,
            taps,
        }
    }

    /// Source texture coordinate the canvas point `p` samples.
    pub fn source_coord(&self, p: (f64, f64)) -> (f64, f64) {
        let u = ((p.0 - (self.centre.0 - self.half.0)) / (2.0 * self.half.0)).clamp(0.0, 1.0);
        let v = ((p.1 - (self.centre.1 - self.half.1)) / (2.0 * self.half.1)).clamp(0.0, 1.0);
        (self.crop[0] + u * self.crop[2], self.crop[1] + v * self.crop[3])
    }

    /// Shadow alpha (before the shadow colour's own alpha) at `p`: cast by
    /// the outer edge of box and stroke.
    pub fn shadow(&self, p: (f64, f64)) -> f64 {
        let q = (p.0 - self.shadow_offset.0, p.1 - self.shadow_offset.1);
        if self.sigma < SHADOW_SIGMA_MIN_PX * self.aa {
            coverage(
                rounded_rect_sdf(q, self.centre, self.outer_half, self.outer_radius),
                self.aa,
            )
        } else {
            let rel = (q.0 - self.centre.0, q.1 - self.centre.1);
            rounded_rect_shadow(rel, self.outer_half, self.outer_radius, self.sigma)
        }
    }

    /// The straight-alpha RGBA of the output pixel whose centre is the canvas
    /// point `p`, given the source sample `src` (straight alpha) there.
    pub fn shade(&self, p: (f64, f64), src: [f64; 4]) -> [f64; 4] {
        // Distance from the box edge; the stroke's outer edge is the same
        // rounded rect grown by `stroke_out`, so its distance is d - stroke_out.
        let d = rounded_rect_sdf(p, self.centre, self.half, self.radius);
        let inside = coverage(d, self.aa);
        let outer = coverage(d - self.stroke_out, self.aa);
        // Premultiplied colour of the part of the pixel inside the box.
        let mut a = src[3];
        let mut rgb = [src[0] * a, src[1] * a, src[2] * a];
        if self.stroke > 0.0 && inside > 0.0 {
            // The share of the box's covered area under the stroke, laid
            // over the source.
            let band = (inside - coverage(d + self.stroke_in, self.aa)).max(0.0);
            let sa = self.stroke_color[3] * band / inside;
            for (c, s) in rgb.iter_mut().zip(self.stroke_color) {
                *c = s * sa + *c * (1.0 - sa);
            }
            a = sa + a * (1.0 - sa);
        }
        a *= inside;
        for c in rgb.iter_mut() {
            *c *= inside;
        }
        if self.stroke > 0.0 {
            // The stroke beyond the box edge, over nothing.
            let sa = self.stroke_color[3] * (outer - inside).max(0.0);
            for (c, s) in rgb.iter_mut().zip(self.stroke_color) {
                *c += s * sa;
            }
            a += sa;
        }
        if self.shadow_color[3] > 0.0 {
            // The shadow lies outside the box and stroke only (as CSS
            // box-shadow), so a translucent source or stroke never shows it
            // through.
            let sh = self.shadow(p) * self.shadow_color[3] * (1.0 - outer);
            for (c, s) in rgb.iter_mut().zip(self.shadow_color) {
                *c += s * sh;
            }
            a += sh;
        }
        a *= self.opacity;
        if a <= 0.0 {
            return [0.0; 4];
        }
        let un = self.opacity / a;
        [rgb[0] * un, rgb[1] * un, rgb[2] * un, a]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn close(a: f64, b: f64, tol: f64) -> bool {
        (a - b).abs() <= tol
    }

    #[test]
    fn sdf_of_a_sharp_rect() {
        let c = (10.0, 10.0);
        let h = (5.0, 3.0);
        assert!(close(rounded_rect_sdf((10.0, 10.0), c, h, 0.0), -3.0, 1e-12));
        assert!(close(rounded_rect_sdf((17.0, 10.0), c, h, 0.0), 2.0, 1e-12));
        assert!(close(rounded_rect_sdf((10.0, 14.0), c, h, 0.0), 1.0, 1e-12));
        // Outside a corner the distance is Euclidean to the corner point.
        assert!(close(rounded_rect_sdf((18.0, 17.0), c, h, 0.0), 5.0, 1e-12));
    }

    #[test]
    fn sdf_of_a_rounded_rect() {
        let c = (0.0, 0.0);
        let h = (10.0, 10.0);
        let r = 4.0;
        // The corner arc is centred at (6, 6) with radius 4.
        let on_arc = (6.0 + 4.0 * 0.6, 6.0 + 4.0 * 0.8);
        assert!(close(rounded_rect_sdf(on_arc, c, h, r), 0.0, 1e-12));
        // The bounding-box corner is outside by sqrt(32) - 4.
        assert!(close(
            rounded_rect_sdf((10.0, 10.0), c, h, r),
            32f64.sqrt() - 4.0,
            1e-12
        ));
        // Straight edges are unaffected by the radius.
        assert!(close(rounded_rect_sdf((10.0, 0.0), c, h, r), 0.0, 1e-12));
        assert!(close(rounded_rect_sdf((0.0, -12.0), c, h, r), 2.0, 1e-12));
    }

    #[test]
    fn coverage_is_exact_on_pixel_centres_of_an_aligned_edge() {
        // Pixel centres sit half a pixel from an integer-aligned edge.
        assert_eq!(coverage(-0.5, 1.0), 1.0);
        assert_eq!(coverage(0.5, 1.0), 0.0);
        assert_eq!(coverage(0.0, 1.0), 0.5);
        // At half scale a canvas pixel is half an output pixel.
        assert_eq!(coverage(0.5, 2.0), 0.25);
    }

    #[test]
    fn erf_matches_reference_values() {
        // erf values from tables.
        for (x, e) in [
            (0.0, 0.0),
            (0.5, 0.5204999),
            (1.0, 0.8427008),
            (2.0, 0.9953223),
            (3.0, 0.9999779),
        ] {
            assert!(close(erf(x), e, 5e-4), "erf({x}) = {}", erf(x));
            assert!(close(erf(-x), -e, 5e-4));
        }
    }

    /// Brute-force 2D Gaussian blur of the rounded-rect indicator.
    fn blurred_reference(p: (f64, f64), half: (f64, f64), r: f64, sigma: f64) -> f64 {
        let n = 400;
        let ext = 5.0 * sigma;
        let step = 2.0 * ext / n as f64;
        let mut sum = 0.0;
        let mut weight = 0.0;
        for i in 0..n {
            for j in 0..n {
                let dx = -ext + (i as f64 + 0.5) * step;
                let dy = -ext + (j as f64 + 0.5) * step;
                let g = (-(dx * dx + dy * dy) / (2.0 * sigma * sigma)).exp();
                weight += g;
                if rounded_rect_sdf((p.0 - dx, p.1 - dy), (0.0, 0.0), half, r) <= 0.0 {
                    sum += g;
                }
            }
        }
        sum / weight
    }

    #[test]
    fn shadow_integral_matches_a_brute_force_blur() {
        let half = (40.0, 25.0);
        for (r, sigma) in [(0.0, 6.0), (12.0, 6.0), (12.0, 15.0), (25.0, 4.0)] {
            for p in [
                (0.0, 0.0),
                (40.0, 0.0),
                (0.0, 25.0),
                (40.0, 25.0),
                (34.0, 20.0),
                (48.0, 30.0),
                (60.0, 0.0),
            ] {
                let got = rounded_rect_shadow(p, half, r, sigma);
                let want = blurred_reference(p, half, r, sigma);
                assert!(close(got, want, 0.012), "r={r} sigma={sigma} p={p:?}: {got} vs {want}");
            }
        }
    }

    #[test]
    fn shadow_is_half_on_a_straight_edge_and_fades_out() {
        let half = (100.0, 100.0);
        assert!(close(rounded_rect_shadow((100.0, 0.0), half, 10.0, 5.0), 0.5, 2e-3));
        assert!(close(rounded_rect_shadow((0.0, 0.0), half, 10.0, 5.0), 1.0, 2e-3));
        assert!(rounded_rect_shadow((130.0, 0.0), half, 10.0, 5.0) < 1e-3);
        // Monotonic outward.
        let mut last = 1.0;
        for x in 0..40 {
            let v = rounded_rect_shadow((90.0 + x as f64, 0.0), half, 10.0, 5.0);
            assert!(v <= last + 1e-9);
            last = v;
        }
    }

    #[test]
    fn crop_maps_the_box_onto_the_visible_part() {
        let s = Settings {
            crop_left: 0.25,
            crop_right: 0.25,
            crop_top: 0.1,
            crop_bottom: 0.4,
            ..Default::default()
        };
        assert_eq!(crop_map(&s, (0.0, 0.0)), (0.25, 0.1));
        let (u, v) = crop_map(&s, (1.0, 1.0));
        assert!(close(u, 0.75, 1e-12) && close(v, 0.6, 1e-12));
        let (u, v) = crop_map(&s, (0.5, 0.5));
        assert!(close(u, 0.5, 1e-12) && close(v, 0.35, 1e-12));
        assert_eq!(crop_map(&Settings::default(), (0.3, 0.7)), (0.3, 0.7));
    }

    #[test]
    fn output_size_follows_box_and_margin() {
        let d = Settings::default();
        assert_eq!(output_size(&d, (320, 240)), (320, 240));
        let s = Settings {
            margin: 40.0,
            ..Default::default()
        };
        assert_eq!(output_size(&s, (320, 240)), (400, 320));
        let s = Settings {
            box_width: 640.0,
            box_height: 360.0,
            margin: 40.0,
            ..Default::default()
        };
        assert_eq!(output_size(&s, (1920, 1080)), (720, 440));
        let s = Settings {
            box_width: 100.4,
            box_height: 0.0,
            margin: 0.3,
            ..Default::default()
        };
        assert_eq!(output_size(&s, (50, 60)), (101, 61));
        let s = Settings {
            box_width: 0.2,
            box_height: 0.2,
            ..Default::default()
        };
        assert_eq!(output_size(&s, (50, 60)), (1, 1));
    }

    #[test]
    fn layout_clamps_radius_and_stroke() {
        let s = Settings {
            box_width: 100.0,
            box_height: 40.0,
            corner_radius: 500.0,
            stroke_width: 90.0,
            ..Default::default()
        };
        let l = Layout::new(&s, (10, 10), (100, 40));
        assert_eq!(l.radius, 20.0);
        assert_eq!(l.stroke, 20.0);
        assert_eq!(l.aa, 1.0);
        assert_eq!(l.sigma, 0.0);
    }

    #[test]
    fn defaults_are_a_passthrough() {
        let l = Layout::new(&Settings::default(), (8, 6), (8, 6));
        let src = [0.2, 0.4, 0.6, 1.0];
        for y in 0..6 {
            for x in 0..8 {
                let p = (x as f64 + 0.5, y as f64 + 0.5);
                let out = l.shade(p, src);
                for i in 0..4 {
                    assert!(close(out[i], src[i], 1e-12), "{x},{y}: {out:?}");
                }
                let (u, v) = l.source_coord(p);
                assert!(close(u, p.0 / 8.0, 1e-12) && close(v, p.1 / 6.0, 1e-12));
            }
        }
        // A translucent source passes through too: the default shadow sits
        // under the content and is clipped to outside it.
        let out = l.shade((0.5, 0.5), [0.2, 0.4, 0.6, 0.5]);
        assert!(close(out[3], 0.5, 1e-12) && close(out[0], 0.2, 1e-12));
    }

    #[test]
    fn stroke_shadow_and_opacity() {
        let s = Settings {
            box_width: 100.0,
            box_height: 60.0,
            margin: 20.0,
            corner_radius: 10.0,
            stroke_width: 4.0,
            stroke_color: 0xFFFF0000,
            shadow_blur: 10.0,
            shadow_offset_x: 6.0,
            shadow_offset_y: 6.0,
            opacity: 0.5,
            ..Default::default()
        };
        let l = Layout::new(&s, (50, 50), (140, 100));
        let src = [0.0, 1.0, 0.0, 1.0];
        // Inside the stroke band on the left edge.
        let px = l.shade((22.0, 50.0), src);
        assert!(close(px[0], 1.0, 1e-9) && close(px[1], 0.0, 1e-9) && close(px[3], 0.5, 1e-9));
        // Content proper.
        let px = l.shade((70.0, 50.0), src);
        assert!(close(px[1], 1.0, 1e-9) && close(px[3], 0.5, 1e-9));
        // Shadow falls further below-right than above-left.
        let below = l.shade((70.0, 84.0), src)[3];
        let above = l.shade((70.0, 16.0), src)[3];
        assert!(below > above + 0.02, "{below} vs {above}");
        assert!(below < 0.5 * 0.4 + 1e-9);
        assert!(l.shade((1.0, 1.0), src)[3] < 1e-6);
    }

    const POSITIONS: [StrokePosition; 3] = [StrokePosition::Inside, StrokePosition::Center, StrokePosition::Outside];

    fn stroked(position: StrokePosition, radius: f64) -> Settings {
        Settings {
            box_width: 100.0,
            box_height: 60.0,
            margin: 20.0,
            corner_radius: radius,
            stroke_width: 8.0,
            stroke_color: 0xFFFF0000,
            stroke_position: position,
            shadow_color: 0,
            ..Default::default()
        }
    }

    #[test]
    fn inside_is_the_default_and_leaves_the_outer_edge_on_the_box() {
        assert_eq!(Settings::default().stroke_position, StrokePosition::Inside);
        let l = Layout::new(&stroked(StrokePosition::Inside, 12.0), (10, 10), (140, 100));
        assert_eq!((l.stroke, l.stroke_in, l.stroke_out), (8.0, 8.0, 0.0));
        assert_eq!((l.outer_half, l.outer_radius), (l.half, l.radius));
    }

    #[test]
    fn stroke_position_grows_the_outer_edge_with_concentric_corners() {
        for (position, out) in POSITIONS.into_iter().zip([0.0, 4.0, 8.0]) {
            for r in [0.0, 12.0, 30.0] {
                let l = Layout::new(&stroked(position, r), (10, 10), (140, 100));
                assert_eq!((l.stroke_in, l.stroke_out), (8.0 - out, out), "{position:?}");
                assert_eq!(l.outer_half, (50.0 + out, 30.0 + out));
                assert_eq!(l.outer_radius, r + out);
                assert_eq!(l.half, (50.0, 30.0), "the box itself is unchanged");
                // The outer edge is the box's offset curve: same corner centres.
                for p in [
                    (70.0, 50.0),
                    (0.0, 0.0),
                    (125.0, 85.0),
                    (130.0, 50.0),
                    (66.0, 3.0),
                    (20.0, 20.0),
                ] {
                    let grown = rounded_rect_sdf(p, l.centre, l.outer_half, l.outer_radius);
                    let offset = rounded_rect_sdf(p, l.centre, l.half, l.radius) - out;
                    assert!(close(grown, offset, 1e-9), "{position:?} r={r} {p:?}");
                }
            }
        }
    }

    #[test]
    fn stroke_positions_on_a_straight_edge() {
        let red = [1.0, 0.0, 0.0, 1.0];
        let green = [0.0, 1.0, 0.0, 1.0];
        // The box's left edge is at x = 20; row 50 is mid-height.
        for (position, first) in POSITIONS.into_iter().zip([20, 16, 12]) {
            let l = Layout::new(&stroked(position, 12.0), (10, 10), (140, 100));
            let at = |x: usize| l.shade((x as f64 + 0.5, 50.0), green);
            assert_eq!(at(first - 1)[3], 0.0, "{position:?}");
            for x in first..first + 8 {
                assert_eq!(at(x), red, "{position:?} stroke at {x}");
            }
            assert_eq!(at(first + 8), green, "{position:?} content");
            assert_eq!(output_size(&stroked(position, 12.0), (10, 10)), (140, 100));
        }
    }

    #[test]
    fn outside_stroke_follows_the_grown_corner() {
        let l = Layout::new(&stroked(StrokePosition::Outside, 12.0), (10, 10), (140, 100));
        // Top-left corner arc centre at (32, 32); along its diagonal the box
        // ends at 12, the stroke at 20.
        let k = std::f64::consts::FRAC_1_SQRT_2;
        let at = |dist: f64| l.shade((32.0 - dist * k, 32.0 - dist * k), [0.0, 1.0, 0.0, 1.0]);
        assert_eq!(at(10.0), [0.0, 1.0, 0.0, 1.0]);
        assert_eq!(at(14.0), [1.0, 0.0, 0.0, 1.0]);
        assert_eq!(at(19.0), [1.0, 0.0, 0.0, 1.0]);
        assert_eq!(at(21.0)[3], 0.0);
    }

    #[test]
    fn translucent_outside_stroke_is_over_nothing_and_shadow_starts_beyond_it() {
        for position in POSITIONS {
            let s = Settings {
                stroke_color: 0x80FF0000,
                shadow_color: 0xFF000000,
                shadow_offset_x: 4.0,
                ..stroked(position, 0.0)
            };
            let l = Layout::new(&s, (10, 10), (140, 100));
            let reach = l.stroke_out;
            let src = [0.0, 1.0, 0.0, 1.0];
            // Right edge of the box at x = 120. The pixel just inside the
            // stroke's outer edge: half-alpha red over nothing (outside the
            // box) or over the source (inside position), never the shadow.
            let px = l.shade((120.0 + reach - 0.5, 50.0), src);
            let half = 128.0 / 255.0;
            let want = if position == StrokePosition::Inside {
                [half, 1.0 - half, 0.0, 1.0]
            } else {
                [1.0, 0.0, 0.0, half]
            };
            assert!((0..4).all(|i| close(px[i], want[i], 1e-12)), "{position:?}: {px:?}");
            // Past the stroke: the sharp shadow, cast by the outer edge.
            assert_eq!(l.shade((120.0 + reach + 3.5, 50.0), src), [0.0, 0.0, 0.0, 1.0]);
            assert_eq!(l.shade((120.0 + reach + 4.5, 50.0), src)[3], 0.0);
        }
    }

    #[test]
    fn stroke_clamps_by_position() {
        let s = Settings {
            box_width: 100.0,
            box_height: 40.0,
            stroke_width: 90.0,
            ..Default::default()
        };
        let layout = |position| {
            let l = Layout::new(
                &Settings {
                    stroke_position: position,
                    ..s.clone()
                },
                (10, 10),
                (100, 40),
            );
            (l.stroke, l.stroke_in, l.stroke_out)
        };
        assert_eq!(layout(StrokePosition::Inside), (20.0, 20.0, 0.0));
        assert_eq!(layout(StrokePosition::Center), (40.0, 20.0, 20.0));
        assert_eq!(layout(StrokePosition::Outside), (90.0, 0.0, 90.0));
    }

    #[test]
    fn margin_holds_stroke_reach_and_shadow() {
        let mut s = Settings {
            stroke_width: 6.0,
            shadow_blur: 10.0,
            shadow_offset_x: -3.0,
            shadow_offset_y: 8.0,
            ..Default::default()
        };
        assert_eq!(margin_for(&s), 23.0);
        s.stroke_position = StrokePosition::Center;
        assert_eq!(margin_for(&s), 26.0);
        s.stroke_position = StrokePosition::Outside;
        assert_eq!(margin_for(&s), 29.0);
        s.shadow_color = 0x00FF_FFFF;
        assert_eq!(margin_for(&s), 6.0);
        s.stroke_width = 2.5;
        s.stroke_position = StrokePosition::Center;
        assert_eq!(margin_for(&s), 2.0);
        assert_eq!(margin_for(&Settings::default()), 0.0);
        // At that margin the layer's edge pixels are (all but) transparent.
        let s = Settings {
            box_width: 100.0,
            box_height: 60.0,
            corner_radius: 12.0,
            stroke_width: 6.0,
            stroke_position: StrokePosition::Outside,
            shadow_blur: 10.0,
            shadow_offset_y: 8.0,
            ..Default::default()
        };
        let s = Settings {
            margin: margin_for(&s),
            ..s
        };
        let (w, h) = output_size(&s, (10, 10));
        let l = Layout::new(&s, (10, 10), (w, h));
        for x in 0..w {
            for y in [0, h - 1] {
                let a = l.shade((x as f64 + 0.5, y as f64 + 0.5), [1.0; 4])[3];
                assert!(a < 0.5 / 255.0, "{x},{y}: {a}");
            }
        }
    }

    #[test]
    fn content_rect_defaults_to_the_whole_input() {
        let d = Settings::default();
        assert_eq!(content_rect(&d, (5120, 1440), None), (0.0, 0.0, 5120.0, 1440.0));
        assert_eq!(content_region(&d, (97, 61), None), WHOLE_INPUT);
        // Bit for bit the crop of before: fractions of the whole input.
        let s = Settings {
            crop_left: 0.13,
            crop_top: 0.07,
            crop_right: 0.21,
            crop_bottom: 0.33,
            ..Default::default()
        };
        let whole = crop_rect(&s, content_region(&s, (97, 61), None));
        assert_eq!(whole, [0.13, 0.07, 1.0 - 0.13 - 0.21, 1.0 - 0.07 - 0.33]);
    }

    #[test]
    fn content_rect_selects_and_clamps() {
        let s = Settings {
            content_x: 100.0,
            content_y: 50.0,
            content_width: 1124.0,
            content_height: 667.0,
            ..Default::default()
        };
        assert_eq!(content_rect(&s, (5120, 1440), None), (100.0, 50.0, 1124.0, 667.0));
        // Past the input's edge it is cut there.
        assert_eq!(content_rect(&s, (1000, 600), None), (100.0, 50.0, 900.0, 550.0));
        // A size of 0 runs to the edge on that axis.
        let s = Settings {
            content_x: 20.0,
            content_height: 30.0,
            ..Default::default()
        };
        assert_eq!(content_rect(&s, (200, 100), None), (20.0, 0.0, 180.0, 30.0));
        let s = Settings {
            content_x: 9999.0,
            ..Default::default()
        };
        assert_eq!(content_rect(&s, (200, 100), None), (199.0, 0.0, 1.0, 100.0));
    }

    #[test]
    fn crop_meta_is_the_content_unless_content_width_is_set() {
        let meta = Some((0, 0, 1124, 667));
        let d = Settings::default();
        assert_eq!(content_rect(&d, (5120, 1440), meta), (0.0, 0.0, 1124.0, 667.0));
        // An empty meta is ignored.
        assert_eq!(content_rect(&d, (64, 48), Some((3, 3, 0, 10))), (0.0, 0.0, 64.0, 48.0));
        let s = Settings {
            content_width: 512.0,
            ..Default::default()
        };
        assert_eq!(content_rect(&s, (5120, 1440), meta), (0.0, 0.0, 512.0, 1440.0));
    }

    #[test]
    fn downscales_sample_the_whole_footprint() {
        // A 1124 px wide window region drawn 640 px wide: 1.76 source pixels per
        // output pixel, two taps per axis.
        let s = Settings {
            content_width: 1124.0,
            content_height: 667.0,
            box_width: 640.0,
            box_height: 380.0,
            ..Default::default()
        };
        let l = Layout::new(&s, (5120, 1440), (640, 380));
        assert_eq!(l.taps, 2.0);
        assert!(close(l.footprint.0 * 5120.0, 1124.0 / 640.0, 1e-9));
        // One to one: a single tap.
        let s = Settings {
            box_width: 640.0,
            box_height: 360.0,
            ..Default::default()
        };
        assert_eq!(Layout::new(&s, (640, 360), (640, 360)).taps, 1.0);
        // Never more than four.
        assert_eq!(Layout::new(&s, (7680, 4320), (640, 360)).taps, 4.0);
    }

    #[test]
    fn crop_applies_within_the_content() {
        let s = Settings {
            content_x: 1000.0,
            content_y: 200.0,
            content_width: 1000.0,
            content_height: 400.0,
            box_width: 100.0,
            box_height: 100.0,
            crop_left: 0.25,
            crop_right: 0.25,
            crop_top: 0.1,
            ..Default::default()
        };
        let l = Layout::new(&s, (4000, 1000), (100, 100));
        let want = [0.3125, 0.24, 0.125, 0.36];
        assert!((0..4).all(|i| close(l.crop[i], want[i], 1e-12)), "{:?}", l.crop);
        // The box's corners sample the cropped region's corners.
        let (u, v) = l.source_coord((0.0, 0.0));
        assert!(close(u * 4000.0, 1250.0, 1e-9) && close(v * 1000.0, 240.0, 1e-9));
        let (u, v) = l.source_coord((100.0, 100.0));
        assert!(close(u * 4000.0, 1750.0, 1e-9) && close(v * 1000.0, 600.0, 1e-9));
    }
}
