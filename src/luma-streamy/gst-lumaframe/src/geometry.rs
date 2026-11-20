// SPDX-License-Identifier: MPL-2.0
//! The layer model `lumaframe` draws, in plain Rust.
//!
//! Everything here mirrors `lumaframe.frag` line for line: the element uses
//! [`Layout`] to compute the output size and the shader uniforms, and the
//! tests use [`Layout::shade`] as the reference the GPU output is compared
//! against. Coordinates are canvas pixels with the origin at the top-left of
//! the padded output; the content rect starts at (`margin`, `margin`).

/// The element's properties, already range-checked by GObject.
#[derive(Clone, Debug, PartialEq)]
pub struct Settings {
    pub box_width: f64,
    pub box_height: f64,
    pub margin: f64,
    pub crop_left: f64,
    pub crop_top: f64,
    pub crop_right: f64,
    pub crop_bottom: f64,
    pub corner_radius: f64,
    pub stroke_width: f64,
    pub stroke_color: u32,
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
            crop_left: 0.0,
            crop_top: 0.0,
            crop_right: 0.0,
            crop_bottom: 0.0,
            corner_radius: 0.0,
            stroke_width: 0.0,
            stroke_color: DEFAULT_STROKE_COLOR,
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

/// Source texture coordinates of the visible part: origin and extent, 0..1.
pub fn crop_rect(s: &Settings) -> [f64; 4] {
    [
        s.crop_left,
        s.crop_top,
        (1.0 - s.crop_left - s.crop_right).max(0.0),
        (1.0 - s.crop_top - s.crop_bottom).max(0.0),
    ]
}

/// Where a point of the box (0..1 on each axis) samples the source.
pub fn crop_map(s: &Settings, box_uv: (f64, f64)) -> (f64, f64) {
    let c = crop_rect(s);
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
    pub stroke_color: [f64; 4],
    /// Source origin and extent in texture coordinates.
    pub crop: [f64; 4],
    pub shadow_color: [f64; 4],
    pub shadow_offset: (f64, f64),
    /// Gaussian sigma: half of `shadow-blur`, as for CSS box-shadow.
    pub sigma: f64,
    pub opacity: f64,
}

impl Layout {
    pub fn new(s: &Settings, input: (i32, i32), output: (i32, i32)) -> Layout {
        let (bw, bh) = box_size(s, input);
        let canvas = canvas_size(s, input);
        let short = 0.5 * bw.min(bh);
        // Scale from output pixels to canvas pixels, averaged over the axes:
        // 1 when the output is the size the element asked for.
        let aa = 0.5 * (canvas.0 / output.0.max(1) as f64 + canvas.1 / output.1.max(1) as f64);
        Layout {
            canvas,
            aa,
            centre: (s.margin + 0.5 * bw, s.margin + 0.5 * bh),
            half: (0.5 * bw, 0.5 * bh),
            radius: s.corner_radius.clamp(0.0, short),
            stroke: s.stroke_width.clamp(0.0, short),
            stroke_color: argb_to_rgba(s.stroke_color),
            crop: crop_rect(s),
            shadow_color: argb_to_rgba(s.shadow_color),
            shadow_offset: (s.shadow_offset_x, s.shadow_offset_y),
            sigma: 0.5 * s.shadow_blur,
            opacity: s.opacity.clamp(0.0, 1.0),
        }
    }

    /// Source texture coordinate the canvas point `p` samples.
    pub fn source_coord(&self, p: (f64, f64)) -> (f64, f64) {
        let u = ((p.0 - (self.centre.0 - self.half.0)) / (2.0 * self.half.0)).clamp(0.0, 1.0);
        let v = ((p.1 - (self.centre.1 - self.half.1)) / (2.0 * self.half.1)).clamp(0.0, 1.0);
        (self.crop[0] + u * self.crop[2], self.crop[1] + v * self.crop[3])
    }

    /// Shadow alpha (before the shadow colour's own alpha) at `p`.
    pub fn shadow(&self, p: (f64, f64)) -> f64 {
        let q = (p.0 - self.shadow_offset.0, p.1 - self.shadow_offset.1);
        if self.sigma < SHADOW_SIGMA_MIN_PX * self.aa {
            coverage(rounded_rect_sdf(q, self.centre, self.half, self.radius), self.aa)
        } else {
            let rel = (q.0 - self.centre.0, q.1 - self.centre.1);
            rounded_rect_shadow(rel, self.half, self.radius, self.sigma)
        }
    }

    /// The straight-alpha RGBA of the output pixel whose centre is the canvas
    /// point `p`, given the source sample `src` (straight alpha) there.
    pub fn shade(&self, p: (f64, f64), src: [f64; 4]) -> [f64; 4] {
        let d = rounded_rect_sdf(p, self.centre, self.half, self.radius);
        let inside = coverage(d, self.aa);
        // Premultiplied colour of the part of the pixel inside the shape.
        let mut a = src[3];
        let mut rgb = [src[0] * a, src[1] * a, src[2] * a];
        if self.stroke > 0.0 && inside > 0.0 {
            // The stroke's share of the covered area, laid over the source.
            let band = (inside - coverage(d + self.stroke, self.aa)).max(0.0);
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
        if self.shadow_color[3] > 0.0 {
            // The shadow lies outside the shape only (as CSS box-shadow), so a
            // translucent source never shows it through.
            let sh = self.shadow(p) * self.shadow_color[3] * (1.0 - inside);
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
}
