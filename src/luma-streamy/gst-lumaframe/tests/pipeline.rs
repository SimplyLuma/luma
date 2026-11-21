// SPDX-License-Identifier: MPL-2.0
//! lumaframe in real GL pipelines. Needs a GL context; headless with Mesa:
//! GST_GL_PLATFORM=egl GST_GL_WINDOW=surfaceless (see README.md).

use std::sync::{Once, OnceLock};

use gst::glib;
use gst::prelude::*;
use gst_gl::prelude::*;
use gstlumavideofx::geometry::{self, Layout, Settings, StrokePosition};

/// One GL display for every pipeline in the process. Each pipeline making
/// its own would share the same EGLDisplay underneath, and one pipeline's
/// eglTerminate on teardown breaks context creation in another running in
/// parallel.
fn gl_display_context() -> gst::Context {
    static DISPLAY: OnceLock<gst_gl::GLDisplay> = OnceLock::new();
    let display = DISPLAY.get_or_init(gst_gl::GLDisplay::new);
    let mut context = gst::Context::new(gst_gl::GL_DISPLAY_CONTEXT_TYPE, true);
    context.get_mut().unwrap().set_gl_display(display);
    context
}

fn init() {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| {
        gst::init().unwrap();
        gstlumavideofx::plugin_register_static().unwrap();
    });
}

#[derive(Clone, Debug)]
struct Frame {
    w: usize,
    h: usize,
    data: Vec<u8>,
}

impl Frame {
    fn new(w: usize, h: usize, f: impl Fn(usize, usize) -> [u8; 4]) -> Frame {
        let mut data = Vec::with_capacity(w * h * 4);
        for y in 0..h {
            for x in 0..w {
                data.extend_from_slice(&f(x, y));
            }
        }
        Frame { w, h, data }
    }

    fn px(&self, x: usize, y: usize) -> [u8; 4] {
        let i = (y * self.w + x) * 4;
        self.data[i..i + 4].try_into().unwrap()
    }
}

struct Harness {
    pipeline: gst::Pipeline,
    src: gst_app::AppSrc,
    sink: gst_app::AppSink,
    frame: gst::Element,
    pts: u64,
}

impl Harness {
    /// appsrc (RGBA) ! glupload ! glcolorconvert ! lumaframe [! downstream] ! gldownload ! appsink
    fn new(input_w: usize, input_h: usize, downstream: Option<&str>, props: &[(&str, glib::Value)]) -> Harness {
        Harness::with_format("RGBA", input_w, input_h, downstream, props)
    }

    /// As [`Harness::new`] with another input format, which glcolorconvert
    /// converts to RGBA.
    fn with_format(
        format: &str,
        input_w: usize,
        input_h: usize,
        downstream: Option<&str>,
        props: &[(&str, glib::Value)],
    ) -> Harness {
        init();
        let forced = downstream.map(|caps| format!("! {caps} ")).unwrap_or_default();
        let desc = format!(
            "appsrc name=src format=time ! glupload ! glcolorconvert ! lumaframe name=frame {forced}\
             ! gldownload ! video/x-raw,format=RGBA ! appsink name=sink sync=false"
        );
        let pipeline = gst::parse::launch(&desc).unwrap().downcast::<gst::Pipeline>().unwrap();
        let src = pipeline.by_name("src").unwrap().downcast::<gst_app::AppSrc>().unwrap();
        let sink = pipeline
            .by_name("sink")
            .unwrap()
            .downcast::<gst_app::AppSink>()
            .unwrap();
        let frame = pipeline.by_name("frame").unwrap();
        src.set_caps(Some(
            &gst::Caps::builder("video/x-raw")
                .field("format", format)
                .field("width", input_w as i32)
                .field("height", input_h as i32)
                .field("framerate", gst::Fraction::new(30, 1))
                .field("pixel-aspect-ratio", gst::Fraction::new(1, 1))
                .build(),
        ));
        for (name, value) in props {
            frame.set_property_from_value(name, value);
        }
        pipeline.set_context(&gl_display_context());
        pipeline.set_state(gst::State::Playing).unwrap();
        Harness {
            pipeline,
            src,
            sink,
            frame,
            pts: 0,
        }
    }

    fn push(&mut self, input: &Frame) -> Frame {
        self.send(input);
        self.pull()
    }

    fn send(&mut self, input: &Frame) {
        self.send_with_crop_meta(input, None);
    }

    /// Push `input`, with a GstVideoCropMeta of (x, y, width, height) if given.
    fn send_with_crop_meta(&mut self, input: &Frame, crop: Option<(u32, u32, u32, u32)>) {
        let mut buffer = gst::Buffer::from_slice(input.data.clone());
        {
            let b = buffer.get_mut().unwrap();
            b.set_pts(gst::ClockTime::from_mseconds(self.pts * 33));
            b.set_duration(gst::ClockTime::from_mseconds(33));
            if let Some(rect) = crop {
                gst_video::VideoCropMeta::add(b, rect);
            }
        }
        self.pts += 1;
        self.src.push_buffer(buffer).unwrap();
    }

    fn pull(&self) -> Frame {
        let Some(sample) = self.sink.try_pull_sample(gst::ClockTime::from_seconds(20)) else {
            let bus = self.pipeline.bus().unwrap();
            while let Some(msg) = bus.pop() {
                if let gst::MessageView::Error(e) = msg.view() {
                    panic!("pipeline error: {} ({:?})", e.error(), e.debug());
                }
            }
            panic!("no output buffer");
        };
        let info = gst_video::VideoInfo::from_caps(sample.caps().unwrap()).unwrap();
        let map = sample.buffer().unwrap().map_readable().unwrap();
        let (w, h, stride) = (info.width() as usize, info.height() as usize, info.stride()[0] as usize);
        let mut data = Vec::with_capacity(w * h * 4);
        for y in 0..h {
            data.extend_from_slice(&map[y * stride..y * stride + w * 4]);
        }
        Frame { w, h, data }
    }

    fn set(&self, name: &str, value: impl Into<glib::Value>) {
        self.frame.set_property_from_value(name, &value.into());
    }
}

impl Drop for Harness {
    fn drop(&mut self) {
        let _ = self.pipeline.set_state(gst::State::Null);
    }
}

fn run(input: &Frame, props: &[(&str, glib::Value)]) -> Frame {
    Harness::new(input.w, input.h, None, props).push(input)
}

fn solid(w: usize, h: usize, rgba: [u8; 4]) -> Frame {
    Frame::new(w, h, |_, _| rgba)
}

fn props(list: &[(&'static str, f64)]) -> Vec<(&'static str, glib::Value)> {
    list.iter().map(|(k, v)| (*k, v.to_value())).collect()
}

fn settings(list: &[(&str, f64)], colours: &[(&str, u32)]) -> Settings {
    let mut s = Settings::default();
    for (k, v) in list {
        let v = *v;
        match *k {
            "box-width" => s.box_width = v,
            "box-height" => s.box_height = v,
            "margin" => s.margin = v,
            "content-x" => s.content_x = v,
            "content-y" => s.content_y = v,
            "content-width" => s.content_width = v,
            "content-height" => s.content_height = v,
            "crop-left" => s.crop_left = v,
            "crop-top" => s.crop_top = v,
            "crop-right" => s.crop_right = v,
            "crop-bottom" => s.crop_bottom = v,
            "corner-radius" => s.corner_radius = v,
            "stroke-width" => s.stroke_width = v,
            "shadow-blur" => s.shadow_blur = v,
            "shadow-offset-x" => s.shadow_offset_x = v,
            "shadow-offset-y" => s.shadow_offset_y = v,
            "opacity" => s.opacity = v,
            _ => panic!("{k}"),
        }
    }
    for (k, v) in colours {
        match *k {
            "stroke-color" => s.stroke_color = *v,
            "shadow-color" => s.shadow_color = *v,
            _ => panic!("{k}"),
        }
    }
    s
}

/// Compare every output pixel with the CPU model for a solid-colour source.
fn assert_matches_model(out: &Frame, s: &Settings, input: (i32, i32), src: [u8; 4], tol: i32) {
    let l = Layout::new(s, input, (out.w as i32, out.h as i32));
    let srcf = src.map(|c| c as f64 / 255.0);
    let mut worst = (0, 0, 0);
    for y in 0..out.h {
        for x in 0..out.w {
            let p = (
                (x as f64 + 0.5) * l.canvas.0 / out.w as f64,
                (y as f64 + 0.5) * l.canvas.1 / out.h as f64,
            );
            let want = l.shade(p, srcf);
            assert!(want[3] <= 1.0 + 1e-9, "model alpha {} at {x},{y}", want[3]);
            let want = want.map(|c| (c * 255.0).round() as i32);
            let got = out.px(x, y).map(|c| c as i32);
            // Colour is meaningless where the pixel is (nearly) transparent.
            let channels = if want[3] < 8 { 3..4 } else { 0..4 };
            for i in channels {
                let e = (want[i] - got[i]).abs();
                if e > worst.0 {
                    worst = (e, x, y);
                }
                assert!(e <= tol, "pixel {x},{y}: got {got:?}, model {want:?}");
            }
        }
    }
    let alphas: std::collections::BTreeSet<u8> = out.data.chunks(4).map(|p| p[3]).collect();
    eprintln!(
        "largest difference from the model: {} at {},{}; {} distinct alpha levels",
        worst.0,
        worst.1,
        worst.2,
        alphas.len()
    );
    // LUMAFRAME_DUMP=dir writes the frame as a PAM image for a look.
    if let Ok(dir) = std::env::var("LUMAFRAME_DUMP") {
        let name = format!("{dir}/{}x{}.pam", out.w, out.h);
        let mut pam = format!(
            "P7\nWIDTH {}\nHEIGHT {}\nDEPTH 4\nMAXVAL 255\nTUPLTYPE RGB_ALPHA\nENDHDR\n",
            out.w, out.h
        )
        .into_bytes();
        pam.extend_from_slice(&out.data);
        std::fs::write(name, pam).unwrap();
    }
}

fn close(a: [u8; 4], b: [u8; 4], tol: i32) -> bool {
    a.iter()
        .zip(b.iter())
        .all(|(x, y)| (*x as i32 - *y as i32).abs() <= tol)
}

#[test]
fn defaults_are_a_passthrough() {
    // A gradient with every channel varying, some of it translucent.
    let input = Frame::new(97, 61, |x, y| {
        [
            (x * 255 / 96) as u8,
            (y * 255 / 60) as u8,
            ((x * 7 + y * 13) % 256) as u8,
            if y < 30 { 255 } else { 64 + (x as u8) },
        ]
    });
    let out = run(&input, &[]);
    assert_eq!((out.w, out.h), (input.w, input.h));
    for y in 0..input.h {
        for x in 0..input.w {
            assert!(
                close(out.px(x, y), input.px(x, y), 1),
                "{x},{y}: {:?} vs {:?}",
                out.px(x, y),
                input.px(x, y)
            );
        }
    }
}

#[test]
fn explicit_zero_effects_are_a_passthrough() {
    let input = Frame::new(64, 48, |x, y| [(x * 4) as u8, (y * 5) as u8, 200, 255]);
    let p = props(&[
        ("box-width", 64.0),
        ("box-height", 48.0),
        ("margin", 0.0),
        ("corner-radius", 0.0),
        ("stroke-width", 0.0),
        ("shadow-blur", 0.0),
        ("opacity", 1.0),
    ]);
    let out = run(&input, &p);
    assert_eq!((out.w, out.h), (64, 48));
    assert!(out
        .data
        .iter()
        .zip(input.data.iter())
        .all(|(a, b)| (*a as i32 - *b as i32).abs() <= 1));
}

#[test]
fn rounded_corners_are_transparent() {
    let out = run(&solid(100, 100, [0, 0, 255, 255]), &props(&[("corner-radius", 30.0)]));
    for (x, y) in [(0, 0), (99, 0), (0, 99), (99, 99), (3, 3)] {
        assert_eq!(out.px(x, y)[3], 0, "corner {x},{y}");
    }
    for (x, y) in [(50, 50), (0, 50), (50, 0), (99, 50), (30, 30)] {
        assert_eq!(out.px(x, y), [0, 0, 255, 255], "{x},{y}");
    }
    // Anti-aliased: partial coverage exists on the arc.
    let partial = (0..100).flat_map(|y| (0..100).map(move |x| (x, y))).filter(|&(x, y)| {
        let a = out.px(x, y)[3];
        a > 10 && a < 245
    });
    assert!(partial.count() > 40);
}

#[test]
fn stroke_is_drawn_inside_the_edge() {
    let out = run(
        &solid(80, 60, [0, 0, 255, 255]),
        &[
            ("stroke-width", 4.0.to_value()),
            ("stroke-color", 0xFF00FF00u32.to_value()),
        ],
    );
    assert_eq!((out.w, out.h), (80, 60));
    for (x, y) in [(0, 30), (3, 30), (79, 30), (40, 0), (40, 59), (76, 30)] {
        assert_eq!(out.px(x, y), [0, 255, 0, 255], "stroke at {x},{y}");
    }
    for (x, y) in [(4, 30), (40, 30), (75, 30), (40, 4)] {
        assert_eq!(out.px(x, y), [0, 0, 255, 255], "content at {x},{y}");
    }
}

#[test]
fn shadow_lies_outside_the_box_and_follows_the_offset() {
    let mut p = props(&[("margin", 40.0), ("shadow-blur", 20.0), ("shadow-offset-y", 12.0)]);
    p.push(("shadow-color", 0xC0000000u32.to_value()));
    let out = run(&solid(100, 60, [255, 255, 255, 255]), &p);
    assert_eq!((out.w, out.h), (180, 140));
    let below = out.px(90, 40 + 60 + 10);
    let above = out.px(90, 40 - 10);
    let left = out.px(40 - 11, 70);
    let right = out.px(40 + 100 + 10, 70);
    assert!(below[3] > above[3] + 30, "below {below:?} above {above:?}");
    assert!(above[3] > 0 && below[3] < 0xC0);
    assert!(
        (left[3] as i32 - right[3] as i32).abs() <= 1,
        "left {left:?} right {right:?}"
    );
    assert_eq!(&below[..3], &[0, 0, 0], "shadow colour");
    assert_eq!(out.px(0, 0)[3], 0);
    assert_eq!(out.px(90, 70), [255, 255, 255, 255], "content");
    // Edges of the box stay content-coloured.
    assert_eq!(out.px(40, 40), [255, 255, 255, 255]);
}

#[test]
fn crop_picks_the_right_region() {
    // Quadrants: red, green / blue, white.
    let input = Frame::new(64, 64, |x, y| match (x < 32, y < 32) {
        (true, true) => [255, 0, 0, 255],
        (false, true) => [0, 255, 0, 255],
        (true, false) => [0, 0, 255, 255],
        (false, false) => [255, 255, 255, 255],
    });
    let cases = [
        (("crop-left", "crop-bottom"), [0, 255, 0, 255]),
        (("crop-left", "crop-top"), [255, 255, 255, 255]),
        (("crop-right", "crop-top"), [0, 0, 255, 255]),
        (("crop-right", "crop-bottom"), [255, 0, 0, 255]),
    ];
    for ((a, b), want) in cases {
        let out = run(
            &input,
            &props(&[(a, 0.49), (b, 0.49), ("box-width", 100.0), ("box-height", 50.0)]),
        );
        assert_eq!((out.w, out.h), (100, 50));
        for (x, y) in [(50, 25), (10, 10), (90, 40), (10, 40), (90, 10)] {
            assert_eq!(out.px(x, y), want, "{a}+{b} at {x},{y}");
        }
    }
}

#[test]
fn opacity_scales_alpha() {
    let out = run(&solid(32, 32, [10, 200, 30, 255]), &props(&[("opacity", 0.5)]));
    let px = out.px(16, 16);
    assert!(close(px, [10, 200, 30, 128], 1), "{px:?}");
    let out = run(&solid(32, 32, [10, 200, 30, 128]), &props(&[("opacity", 0.5)]));
    assert!(close(out.px(16, 16), [10, 200, 30, 64], 1));
}

#[test]
fn everything_at_once_matches_the_model() {
    let list = [
        ("box-width", 120.0),
        ("box-height", 80.0),
        ("margin", 24.0),
        ("corner-radius", 18.0),
        ("stroke-width", 3.0),
        ("shadow-blur", 16.0),
        ("shadow-offset-x", 5.0),
        ("shadow-offset-y", 7.0),
        ("opacity", 0.8),
    ];
    let colours = [("stroke-color", 0xFFFF2000u32), ("shadow-color", 0x99102030u32)];
    let mut p = props(&list);
    p.extend(colours.iter().map(|(k, v)| (*k, v.to_value())));
    let src = [40, 160, 220, 255];
    let out = run(&solid(64, 48, src), &p);
    assert_eq!((out.w, out.h), (168, 128));
    assert_matches_model(&out, &settings(&list, &colours), (64, 48), src, 3);
}

#[test]
fn size_change_while_playing_renegotiates() {
    let input = solid(64, 48, [255, 0, 0, 255]);
    let mut h = Harness::new(64, 48, None, &[]);
    let out = h.push(&input);
    assert_eq!((out.w, out.h), (64, 48));

    h.set("box-width", 100.0);
    h.set("box-height", 50.0);
    h.set("margin", 10.0);
    let out = h.push(&input);
    assert_eq!((out.w, out.h), (120, 70));
    assert_eq!(out.px(5, 5)[3], 0);
    assert_eq!(out.px(60, 35), [255, 0, 0, 255]);

    // Not a size change: same caps, new look.
    h.set("corner-radius", 20.0);
    h.set("opacity", 0.5);
    let out = h.push(&input);
    assert_eq!((out.w, out.h), (120, 70));
    assert_eq!(out.px(11, 11)[3], 0);
    assert!(close(out.px(60, 35), [255, 0, 0, 128], 1));

    h.set("margin", 0.0);
    let out = h.push(&input);
    assert_eq!((out.w, out.h), (100, 50));
}

#[test]
fn downstream_size_scales_the_layer() {
    let list = [
        ("box-width", 100.0),
        ("box-height", 50.0),
        ("margin", 10.0),
        ("corner-radius", 10.0),
        ("stroke-width", 2.0),
    ];
    let src = [0, 255, 0, 255];
    let mut h = Harness::new(
        40,
        30,
        Some("video/x-raw(memory:GLMemory),width=240,height=140"),
        &props(&list),
    );
    let out = h.push(&solid(40, 30, src));
    assert_eq!((out.w, out.h), (240, 140));
    assert_matches_model(&out, &settings(&list, &[]), (40, 30), src, 3);
}

#[test]
fn resizes_inside_glvideomixer_while_playing() {
    let input = solid(64, 48, [255, 0, 0, 255]);
    let mut h = Harness::new(
        64,
        48,
        Some("glvideomixer name=mix background=transparent ! video/x-raw(memory:GLMemory),width=320,height=240"),
        &props(&[("box-width", 100.0), ("box-height", 60.0), ("corner-radius", 8.0)]),
    );
    let out = h.push(&input);
    assert_eq!((out.w, out.h), (320, 240));
    assert_eq!(out.px(50, 30), [255, 0, 0, 255]);
    assert_eq!(out.px(150, 100)[3], 0);
    assert_eq!(out.px(0, 0)[3], 0, "rounded corner composited as transparent");

    // glvideomixer only recomputes a pad's geometry when a pad property
    // changes, not when its input caps do, so a resize sets the pad size too.
    // It also holds a buffer back across a caps change until the next one
    // arrives when not live: send two, take one.
    h.set("box-width", 200.0);
    h.set("box-height", 150.0);
    let pad = h.pipeline.by_name("mix").unwrap().static_pad("sink_0").unwrap();
    pad.set_property("width", 200i32);
    pad.set_property("height", 150i32);
    h.send(&input);
    h.send(&input);
    let out = h.pull();
    assert_eq!((out.w, out.h), (320, 240));
    assert_eq!(out.px(150, 100), [255, 0, 0, 255]);
    assert_eq!(out.px(250, 200)[3], 0);
    assert_eq!(out.px(0, 0)[3], 0);
}

const POSITIONS: [StrokePosition; 3] = [StrokePosition::Inside, StrokePosition::Center, StrokePosition::Outside];

/// Golden pixels of an opaque 4 px stroke on an 80×60 box in an 8 px margin,
/// down the middle of the top edge (straight at every radius) and across
/// the top-left corner's diagonal.
#[test]
fn stroke_positions_golden_pixels() {
    const GREEN: [u8; 4] = [0, 255, 0, 255];
    const BLUE: [u8; 4] = [0, 0, 255, 255];
    // First stroke row, diagonal pixels in the stroke at radius 12 and just
    // past its outer edge (corner arc centre at 20,20: concentric radii).
    for (position, first, (in_stroke, past)) in [
        (StrokePosition::Inside, 8, (12, 10)),
        (StrokePosition::Center, 6, (11, 9)),
        (StrokePosition::Outside, 4, (10, 7)),
    ] {
        for radius in [0.0, 12.0, 1000.0] {
            let mut p = props(&[
                ("box-width", 80.0),
                ("box-height", 60.0),
                ("margin", 8.0),
                ("corner-radius", radius),
                ("stroke-width", 4.0),
            ]);
            p.push(("stroke-color", 0xFF00FF00u32.to_value()));
            p.push(("shadow-color", 0u32.to_value()));
            p.push(("stroke-position", position.to_value()));
            let out = run(&solid(32, 24, BLUE), &p);
            assert_eq!((out.w, out.h), (96, 76), "the box and output size are unchanged");
            let at = |y| out.px(48, y);
            assert_eq!(at(first - 1)[3], 0, "{position:?} r={radius} above the stroke");
            for y in first..first + 4 {
                assert_eq!(at(y), GREEN, "{position:?} r={radius} stroke row {y}");
            }
            assert_eq!(at(first + 4), BLUE, "{position:?} r={radius} content");
            assert_eq!(out.px(48, 38), BLUE);
            if radius == 12.0 {
                assert_eq!(out.px(in_stroke, in_stroke), GREEN, "{position:?} corner stroke");
                assert_eq!(out.px(past, past)[3], 0, "{position:?} past the corner");
            }
        }
    }
}

#[test]
fn shadow_starts_outside_the_stroke() {
    // Sharp opaque shadow 4 px right of the outer edge; the box's right edge
    // is at x = 88 and row 38 is mid-height.
    for (position, reach) in POSITIONS.into_iter().zip([0, 2, 4]) {
        let mut p = props(&[
            ("box-width", 80.0),
            ("box-height", 60.0),
            ("margin", 8.0),
            ("corner-radius", 12.0),
            ("stroke-width", 4.0),
            ("shadow-offset-x", 4.0),
        ]);
        p.push(("stroke-color", 0x80FF0000u32.to_value()));
        p.push(("shadow-color", 0xFF000000u32.to_value()));
        p.push(("stroke-position", position.to_value()));
        let out = run(&solid(32, 24, [0, 0, 255, 255]), &p);
        let edge = 88 + reach;
        // Translucent stroke over the source inside the box, over nothing
        // outside it: never over the shadow.
        let last = out.px(edge - 1, 38);
        if position == StrokePosition::Inside {
            assert!(close(last, [128, 0, 127, 255], 1), "{position:?}: {last:?}");
        } else {
            assert!(close(last, [255, 0, 0, 128], 1), "{position:?}: {last:?}");
        }
        for x in edge..edge + 4 {
            assert_eq!(out.px(x, 38), [0, 0, 0, 255], "{position:?} shadow at {x}");
        }
        assert_eq!(out.px(edge + 4, 38)[3], 0, "{position:?}");
    }
}

#[test]
fn stroke_positions_match_the_model() {
    for position in POSITIONS {
        for radius in [0.0, 12.0, 1000.0] {
            let mut s = settings(
                &[
                    ("box-width", 100.0),
                    ("box-height", 60.0),
                    ("corner-radius", radius),
                    ("stroke-width", 6.0),
                    ("shadow-blur", 12.0),
                    ("shadow-offset-x", 4.0),
                    ("shadow-offset-y", 6.0),
                ],
                &[("stroke-color", 0xC0FF2000u32), ("shadow-color", 0x99102030u32)],
            );
            s.stroke_position = position;
            s.margin = geometry::margin_for(&s);
            let mut p = props(&[
                ("box-width", s.box_width),
                ("box-height", s.box_height),
                ("margin", s.margin),
                ("corner-radius", radius),
                ("stroke-width", s.stroke_width),
                ("shadow-blur", s.shadow_blur),
                ("shadow-offset-x", s.shadow_offset_x),
                ("shadow-offset-y", s.shadow_offset_y),
            ]);
            p.push(("stroke-color", s.stroke_color.to_value()));
            p.push(("shadow-color", s.shadow_color.to_value()));
            p.push(("stroke-position", position.to_value()));
            let src = [40, 160, 220, 200];
            let out = run(&solid(64, 48, src), &p);
            eprintln!("{position:?} radius {radius}, margin {}", s.margin);
            assert_matches_model(&out, &s, (64, 48), src, 3);
            // The margin holds it all: the frame's border is transparent.
            for x in 0..out.w {
                assert_eq!(out.px(x, 0)[3], 0);
                assert_eq!(out.px(x, out.h - 1)[3], 0);
            }
            for y in 0..out.h {
                assert_eq!(out.px(0, y)[3], 0);
                assert_eq!(out.px(out.w - 1, y)[3], 0);
            }
        }
    }
}

#[test]
fn stroke_position_changes_while_playing_without_renegotiating() {
    let input = solid(64, 48, [0, 0, 255, 255]);
    let mut h = Harness::new(
        64,
        48,
        None,
        &[
            ("box-width", 80.0.to_value()),
            ("box-height", 60.0.to_value()),
            ("margin", 8.0.to_value()),
            ("stroke-width", 4.0.to_value()),
            ("stroke-color", 0xFF00FF00u32.to_value()),
            ("shadow-color", 0u32.to_value()),
        ],
    );
    assert_eq!(
        h.frame.property::<StrokePosition>("stroke-position"),
        StrokePosition::Inside
    );
    let out = h.push(&input);
    assert_eq!(out.px(48, 3)[3], 0);
    assert_eq!(out.px(48, 9), [0, 255, 0, 255]);
    h.set("stroke-position", StrokePosition::Outside);
    let out = h.push(&input);
    assert_eq!((out.w, out.h), (96, 76));
    assert_eq!(out.px(48, 5), [0, 255, 0, 255]);
    assert_eq!(out.px(48, 9), [0, 0, 255, 255]);
}

/// Quadrants: red, green / blue, white.
fn quadrants(w: usize, h: usize) -> Frame {
    Frame::new(w, h, |x, y| match (x < w / 2, y < h / 2) {
        (true, true) => [255, 0, 0, 255],
        (false, true) => [0, 255, 0, 255],
        (true, false) => [0, 0, 255, 255],
        (false, false) => [255, 255, 255, 255],
    })
}

/// Points of a 100×50 box away from the quadrant edges, which the linear
/// filter blends.
const SAMPLES: [(usize, usize); 6] = [(50, 25), (10, 10), (90, 40), (10, 40), (90, 10), (30, 30)];

#[test]
fn content_rect_selects_the_source_region() {
    let input = quadrants(64, 48);
    let mut h = Harness::new(
        64,
        48,
        None,
        &props(&[
            ("box-width", 100.0),
            ("box-height", 50.0),
            ("content-x", 32.0),
            ("content-width", 32.0),
            ("content-height", 24.0),
        ]),
    );
    let out = h.push(&input);
    assert_eq!((out.w, out.h), (100, 50));
    for (x, y) in SAMPLES {
        assert_eq!(out.px(x, y), [0, 255, 0, 255], "green quadrant at {x},{y}");
    }
    // Uniforms only while PLAYING; crop-* then cut fractions of the content.
    h.set("content-y", 24.0);
    let out = h.push(&input);
    assert_eq!((out.w, out.h), (100, 50));
    for (x, y) in SAMPLES {
        assert_eq!(out.px(x, y), [255, 255, 255, 255], "white quadrant at {x},{y}");
    }
    h.set("content-x", 0.0);
    h.set("content-y", 0.0);
    h.set("content-width", 64.0);
    h.set("content-height", 48.0);
    h.set("crop-left", 0.49);
    h.set("crop-bottom", 0.49);
    let out = h.push(&input);
    for (x, y) in SAMPLES {
        assert_eq!(out.px(x, y), [0, 255, 0, 255], "cropped content at {x},{y}");
    }
}

#[test]
fn content_rect_matches_the_model() {
    let list = [
        ("box-width", 120.0),
        ("box-height", 80.0),
        ("margin", 10.0),
        ("corner-radius", 12.0),
        ("content-x", 100.0),
        ("content-y", 40.0),
        ("content-width", 200.0),
        ("content-height", 120.0),
        ("crop-left", 0.1),
        ("crop-top", 0.2),
    ];
    let src = [200, 60, 30, 255];
    let out = run(&solid(512, 256, src), &props(&list));
    assert_eq!((out.w, out.h), (140, 100));
    assert_matches_model(&out, &settings(&list, &[]), (512, 256), src, 3);
}

#[test]
fn crop_meta_selects_the_content_per_buffer() {
    let input = quadrants(64, 48);
    let mut h = Harness::new(64, 48, None, &props(&[("box-width", 100.0), ("box-height", 50.0)]));
    // Record whether the meta reaches lumaframe through glupload and
    // glcolorconvert.
    let seen = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
    let s = seen.clone();
    h.frame
        .static_pad("sink")
        .unwrap()
        .add_probe(gst::PadProbeType::BUFFER, move |_, info| {
            if let Some(b) = info.buffer() {
                s.lock()
                    .unwrap()
                    .push(b.meta::<gst_video::VideoCropMeta>().map(|m| m.rect()));
            }
            gst::PadProbeReturn::Ok
        });

    h.send_with_crop_meta(&input, Some((32, 24, 32, 24)));
    let out = h.pull();
    assert_eq!((out.w, out.h), (100, 50), "a crop meta does not change the size");
    for (x, y) in SAMPLES {
        assert_eq!(out.px(x, y), [255, 255, 255, 255], "white quadrant at {x},{y}");
    }
    // The next buffer's meta moved: the box follows.
    h.send_with_crop_meta(&input, Some((32, 0, 32, 24)));
    let out = h.pull();
    for (x, y) in SAMPLES {
        assert_eq!(out.px(x, y), [0, 255, 0, 255], "green quadrant at {x},{y}");
    }
    // No meta: the whole input.
    let out = h.push(&input);
    assert_eq!(out.px(1, 1), [255, 0, 0, 255]);
    assert_eq!(out.px(98, 48), [255, 255, 255, 255]);
    // content-width set: the properties win over the meta.
    h.set("content-width", 32.0);
    h.set("content-height", 24.0);
    h.send_with_crop_meta(&input, Some((32, 24, 32, 24)));
    let out = h.pull();
    for (x, y) in SAMPLES {
        assert_eq!(out.px(x, y), [255, 0, 0, 255], "red quadrant at {x},{y}");
    }
    assert_eq!(
        *seen.lock().unwrap(),
        [
            Some((32, 24, 32, 24)),
            Some((32, 0, 32, 24)),
            None,
            Some((32, 24, 32, 24))
        ],
        "crop meta at lumaframe's sink"
    );
}

#[test]
fn crop_meta_survives_colour_conversion() {
    // BGRA in: glcolorconvert converts rather than passing the buffer on.
    let rgba = quadrants(64, 48);
    let bgra = Frame::new(64, 48, |x, y| {
        let [r, g, b, a] = rgba.px(x, y);
        [b, g, r, a]
    });
    let mut h = Harness::with_format(
        "BGRA",
        64,
        48,
        None,
        &props(&[("box-width", 100.0), ("box-height", 50.0)]),
    );
    h.send_with_crop_meta(&bgra, Some((0, 24, 32, 24)));
    let out = h.pull();
    for (x, y) in SAMPLES {
        assert_eq!(out.px(x, y), [0, 0, 255, 255], "blue quadrant at {x},{y}");
    }
}

#[test]
fn revision_commits_changes_together_and_stamps_buffers() {
    let input = solid(64, 48, [255, 0, 0, 255]);
    let mut h = Harness::new(64, 48, None, &[]);
    h.set("revision", 1u64);
    h.send(&input);
    let sample = h.sink.try_pull_sample(gst::ClockTime::from_seconds(20)).unwrap();
    assert_eq!(sample.buffer().unwrap().offset(), 1);

    // Held until the next revision commits it.
    h.set("margin", 10.0);
    let out = h.push(&input);
    assert_eq!((out.w, out.h), (64, 48));
    h.set("revision", 2u64);
    h.send(&input);
    let sample = h.sink.try_pull_sample(gst::ClockTime::from_seconds(20)).unwrap();
    assert_eq!(sample.buffer().unwrap().offset(), 2);
    let info = gst_video::VideoInfo::from_caps(sample.caps().unwrap()).unwrap();
    assert_eq!((info.width(), info.height()), (84, 68));
}

#[test]
fn downscale_averages_the_footprint() {
    // Alternating one-pixel columns, drawn at a quarter of the width: an even
    // grey, never one column's colour.
    let (w, h) = (256, 16);
    let mut data = Vec::with_capacity(w * h * 4);
    for _y in 0..h {
        for x in 0..w {
            let v = if x % 2 == 0 { 255 } else { 0 };
            data.extend_from_slice(&[v, v, v, 255]);
        }
    }
    let input = Frame { w, h, data };
    let out = run(&input, &props(&[("box-width", 64.0), ("box-height", 16.0)]));
    for x in 4..60 {
        let p = out.px(x, 8);
        assert!((p[0] as i32 - 128).abs() <= 40, "column {x}: {p:?}");
    }
}

/// `source-latency` replaces upstream's minimum latency in the answer
/// downstream gets (a live compositor waits that long for a layer).
#[test]
fn source_latency_replaces_upstream_minimum() {
    init();
    let pipeline = gst::parse::launch(
        "videotestsrc is-live=true ! video/x-raw,width=64,height=48,framerate=30/1 ! glupload ! \
         lumaframe name=f ! fakesink",
    )
    .unwrap()
    .downcast::<gst::Pipeline>()
    .unwrap();
    // The tests' one GL display: a display of its own breaks the others running in parallel.
    pipeline.set_context(&gl_display_context());
    pipeline.set_state(gst::State::Playing).unwrap();
    let _ = pipeline.state(gst::ClockTime::from_seconds(5));
    let frame = pipeline.by_name("f").unwrap();
    let src = frame.static_pad("src").unwrap();
    let sink = frame.static_pad("sink").unwrap();
    let min = |pad: &gst::Pad, peer: bool| {
        let mut q = gst::query::Latency::new();
        assert!(if peer {
            pad.peer_query(&mut q)
        } else {
            pad.query(&mut q)
        });
        let (live, min, _max) = q.result();
        assert!(live);
        min
    };
    let upstream = min(&sink, true);
    assert!(upstream > gst::ClockTime::ZERO, "a live source answers some latency");
    assert_eq!(frame.property::<i64>("source-latency"), -1);
    assert_eq!(min(&src, false), upstream, "passed through by default");
    frame.set_property("source-latency", 0i64);
    assert_eq!(min(&src, false), gst::ClockTime::ZERO);
    frame.set_property("source-latency", 2_000_000i64);
    assert_eq!(min(&src, false), gst::ClockTime::from_mseconds(2));
    pipeline.set_state(gst::State::Null).unwrap();
}
