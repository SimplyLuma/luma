// SPDX-License-Identifier: MPL-2.0
/**
 * SECTION:element-lumaframe
 * @title: lumaframe
 *
 * Draws one layer of a composited scene in a single GL pass: the source
 * stretched into a box with anti-aliased rounded corners, an optional stroke
 * inside, centred on or outside the box edge, an analytic Gaussian drop shadow in a transparent
 * margin around it, a crop of the source and an overall opacity. The output
 * is RGBA with straight alpha, so `glvideomixer`'s default blending
 * composites it directly.
 *
 * Sizes are in canvas pixels. The output is
 * round(box-width + 2 × margin) × round(box-height + 2 × margin), with the
 * input size standing in for a box size of 0. A downstream element that
 * forces another size gets the same layer scaled to fit. Every property can
 * change while PLAYING; a change of size renegotiates before the next buffer.
 *
 * With the defaults the element is a passthrough.
 *
 * ## Example launch line
 * |[
 * gst-launch-1.0 videotestsrc ! glupload ! glcolorconvert ! \
 *   lumaframe box-width=640 box-height=360 margin=40 corner-radius=24 \
 *   stroke-width=4 shadow-blur=30 shadow-offset-y=8 ! \
 *   glvideomixer background=checker ! gldownload ! videoconvert ! autovideosink
 * ]|
 */
use std::sync::{LazyLock, Mutex};

use gst::glib;
use gst::prelude::*;
use gst::subclass::prelude::*;
use gst_base::prelude::*;
use gst_base::subclass::prelude::*;
use gst_gl::prelude::*;
use gst_gl::subclass::prelude::*;
use gst_gl::subclass::GLFilterMode;

use crate::geometry::{self, Layout, Settings, StrokePosition};

static CAT: LazyLock<gst::DebugCategory> =
    LazyLock::new(|| gst::DebugCategory::new("lumaframe", gst::DebugColorFlags::empty(), Some("Luma GL layer")));

const FRAGMENT_SHADER: &str = include_str!("../lumaframe.frag");
const GL_FRAGMENT_SHADER: u32 = 0x8B30;
/// Upper bound of every size property, in canvas pixels.
const MAX_PX: f64 = 32768.0;

#[derive(Default)]
pub struct LumaFrame {
    settings: Mutex<Settings>,
    /// The negotiated input size, for deciding whether a property change
    /// alters the output size.
    input_size: Mutex<Option<(i32, i32)>>,
    /// The current input buffer's crop meta, if it has one.
    crop_meta: Mutex<Option<geometry::CropMeta>>,
    /// Property changes made since `revision` was first set, held until the
    /// next `revision` commits them all at once.
    pending: Mutex<Option<Settings>>,
    /// The last committed revision; 0 until the application sets one.
    revision: Mutex<u64>,
    /// The settings and revision of the buffer being drawn, taken once per
    /// input buffer so a frame never mixes two revisions.
    frame: Mutex<Option<(Settings, u64)>>,
    shader: Mutex<Option<gst_gl::GLShader>>,
    /// The minimum latency answered for everything upstream, in ns; None
    /// passes upstream's answer through.
    source_latency: Mutex<Option<gst::ClockTime>>,
}

#[glib::object_subclass]
impl ObjectSubclass for LumaFrame {
    const NAME: &'static str = "GstLumaFrame";
    type Type = super::LumaFrame;
    type ParentType = gst_gl::GLFilter;
}

fn double(
    name: &'static str,
    nick: &'static str,
    blurb: &'static str,
    min: f64,
    max: f64,
    default: f64,
) -> glib::ParamSpec {
    glib::ParamSpecDouble::builder(name)
        .nick(nick)
        .blurb(blurb)
        .minimum(min)
        .maximum(max)
        .default_value(default)
        .mutable_playing()
        .build()
}

fn colour(name: &'static str, nick: &'static str, blurb: &'static str, default: u32) -> glib::ParamSpec {
    glib::ParamSpecUInt::builder(name)
        .nick(nick)
        .blurb(blurb)
        .default_value(default)
        .mutable_playing()
        .build()
}

impl ObjectImpl for LumaFrame {
    fn properties() -> &'static [glib::ParamSpec] {
        static PROPERTIES: LazyLock<Vec<glib::ParamSpec>> = LazyLock::new(|| {
            vec![
                /*
                 * GstLumaFrame:box-width:
                 *
                 * Width of the content box on the canvas, in canvas pixels.
                 * 0 uses the input width.
                 */
                double(
                    "box-width",
                    "Box width",
                    "Width of the content box in canvas pixels (0 = input width)",
                    0.0,
                    MAX_PX,
                    0.0,
                ),
                /*
                 * GstLumaFrame:box-height:
                 *
                 * Height of the content box on the canvas, in canvas pixels.
                 * 0 uses the input height.
                 */
                double(
                    "box-height",
                    "Box height",
                    "Height of the content box in canvas pixels (0 = input height)",
                    0.0,
                    MAX_PX,
                    0.0,
                ),
                /*
                 * GstLumaFrame:margin:
                 *
                 * Transparent room on every side of the box, in canvas pixels,
                 * where the shadow and an outside or centred stroke are
                 * drawn. Place the layer at its canvas position minus the
                 * margin.
                 */
                double(
                    "margin",
                    "Margin",
                    "Transparent room around the box for the shadow and an outside stroke, canvas pixels, every side",
                    0.0,
                    MAX_PX,
                    0.0,
                ),
                /*
                 * GstLumaFrame:content-x:
                 *
                 * Left edge of the part of the source the box shows, in
                 * source pixels. crop-* then cut fractions of that part.
                 */
                double(
                    "content-x",
                    "Content X",
                    "Left edge of the source region the box shows, source pixels",
                    0.0,
                    MAX_PX,
                    0.0,
                ),
                /*
                 * GstLumaFrame:content-y:
                 *
                 * Top edge of the part of the source the box shows, in source
                 * pixels.
                 */
                double(
                    "content-y",
                    "Content Y",
                    "Top edge of the source region the box shows, source pixels",
                    0.0,
                    MAX_PX,
                    0.0,
                ),
                /*
                 * GstLumaFrame:content-width:
                 *
                 * Width of the part of the source the box shows, in source
                 * pixels; 0 runs to the input's right edge. While it is 0, a
                 * GstVideoCropMeta on the input buffer, if any, gives the
                 * whole region instead (per buffer).
                 */
                double(
                    "content-width",
                    "Content width",
                    "Width of the source region the box shows, source pixels (0 = to the edge, or the buffer's crop meta)",
                    0.0,
                    MAX_PX,
                    0.0,
                ),
                /*
                 * GstLumaFrame:content-height:
                 *
                 * Height of the part of the source the box shows, in source
                 * pixels; 0 runs to the input's bottom edge.
                 */
                double(
                    "content-height",
                    "Content height",
                    "Height of the source region the box shows, source pixels (0 = to the edge)",
                    0.0,
                    MAX_PX,
                    0.0,
                ),
                /*
                 * GstLumaFrame:crop-left:
                 *
                 * Fraction of the content (content-*, a crop meta, or the
                 * whole source) cut from the left. The rest is stretched to
                 * fill the box.
                 */
                double(
                    "crop-left",
                    "Crop left",
                    "Fraction of the source cut from the left",
                    0.0,
                    0.49,
                    0.0,
                ),
                /*
                 * GstLumaFrame:crop-top:
                 *
                 * Fraction of the source height cut from the top.
                 */
                double(
                    "crop-top",
                    "Crop top",
                    "Fraction of the source cut from the top",
                    0.0,
                    0.49,
                    0.0,
                ),
                /*
                 * GstLumaFrame:crop-right:
                 *
                 * Fraction of the source width cut from the right.
                 */
                double(
                    "crop-right",
                    "Crop right",
                    "Fraction of the source cut from the right",
                    0.0,
                    0.49,
                    0.0,
                ),
                /*
                 * GstLumaFrame:crop-bottom:
                 *
                 * Fraction of the source height cut from the bottom.
                 */
                double(
                    "crop-bottom",
                    "Crop bottom",
                    "Fraction of the source cut from the bottom",
                    0.0,
                    0.49,
                    0.0,
                ),
                /*
                 * GstLumaFrame:corner-radius:
                 *
                 * Radius of the box corners in canvas pixels, clamped to half
                 * the box's shorter side.
                 */
                double(
                    "corner-radius",
                    "Corner radius",
                    "Corner radius in canvas pixels (clamped to half the shorter side)",
                    0.0,
                    MAX_PX,
                    0.0,
                ),
                /*
                 * GstLumaFrame:stroke-width:
                 *
                 * Width of the outline along the box edge, following the
                 * corners, in canvas pixels; stroke-position says which side
                 * of the edge. 0 draws none.
                 */
                double(
                    "stroke-width",
                    "Stroke width",
                    "Outline width along the box edge in canvas pixels (0 = none)",
                    0.0,
                    MAX_PX,
                    0.0,
                ),
                /*
                 * GstLumaFrame:stroke-position:
                 *
                 * Where the stroke lies: inside the box edge, covering the
                 * content's edge; centred on it; or outside it, in the
                 * margin, leaving the box and the output size unchanged.
                 * Corners stay concentric: the outer edge's radius is
                 * corner-radius plus the stroke's reach outside the box
                 * (stroke-width for outside, half of it for center). The
                 * shadow is cast by that outer edge. The margin must hold
                 * the reach plus the shadow.
                 */
                glib::ParamSpecEnum::builder_with_default("stroke-position", StrokePosition::Inside)
                    .nick("Stroke position")
                    .blurb("Where the stroke lies: inside, centred on or outside the box edge")
                    .mutable_playing()
                    .build(),
                /*
                 * GstLumaFrame:stroke-color:
                 *
                 * Outline colour as 0xAARRGGBB, straight alpha.
                 */
                colour(
                    "stroke-color",
                    "Stroke colour",
                    "Outline colour, 0xAARRGGBB",
                    geometry::DEFAULT_STROKE_COLOR,
                ),
                /*
                 * GstLumaFrame:shadow-blur:
                 *
                 * Shadow blur radius in canvas pixels, as CSS box-shadow: the
                 * Gaussian's standard deviation is half this value. The
                 * shadow is cast by the outer edge of box and stroke; a
                 * margin of the stroke's reach + 1.5 × shadow-blur + the
                 * larger offset holds all of it. 0 gives a sharp shadow.
                 */
                double(
                    "shadow-blur",
                    "Shadow blur",
                    "Shadow blur radius in canvas pixels (Gaussian sigma = blur / 2, as CSS)",
                    0.0,
                    MAX_PX,
                    0.0,
                ),
                /*
                 * GstLumaFrame:shadow-offset-x:
                 *
                 * Horizontal shadow offset in canvas pixels; positive is right.
                 */
                double(
                    "shadow-offset-x",
                    "Shadow offset X",
                    "Horizontal shadow offset in canvas pixels (positive = right)",
                    -MAX_PX,
                    MAX_PX,
                    0.0,
                ),
                /*
                 * GstLumaFrame:shadow-offset-y:
                 *
                 * Vertical shadow offset in canvas pixels; positive is down.
                 */
                double(
                    "shadow-offset-y",
                    "Shadow offset Y",
                    "Vertical shadow offset in canvas pixels (positive = down)",
                    -MAX_PX,
                    MAX_PX,
                    0.0,
                ),
                /*
                 * GstLumaFrame:shadow-color:
                 *
                 * Shadow colour as 0xAARRGGBB; the alpha is the shadow's
                 * opacity. Alpha 0 draws no shadow. The shadow is only drawn
                 * outside the box, so it never shows through a translucent
                 * source.
                 */
                colour(
                    "shadow-color",
                    "Shadow colour",
                    "Shadow colour, 0xAARRGGBB; alpha is the shadow opacity (0 = none)",
                    geometry::DEFAULT_SHADOW_COLOR,
                ),
                /*
                 * GstLumaFrame:opacity:
                 *
                 * Opacity of the whole layer: content, stroke and shadow.
                 */
                double(
                    "opacity",
                    "Opacity",
                    "Opacity of the whole layer (content, stroke and shadow)",
                    0.0,
                    1.0,
                    1.0,
                ),
                /*
                 * GstLumaFrame:revision:
                 *
                 * Commits every property change since the last commit, at once,
                 * and stamps each output buffer drawn with them (its offset
                 * field), so downstream can match a buffer to the geometry it
                 * was drawn with. Until it is first set (0), changes apply as
                 * they are made.
                 */
                /*
                 * GstLumaFrame:source-latency:
                 *
                 * The minimum latency this element answers for its upstream
                 * in latency queries, in nanoseconds; -1 answers what
                 * upstream says. A live source stamped on arrival
                 * (pipewiresrc do-timestamp) has a whole picture at its
                 * timestamp, so 0 is true for it, and a live compositor
                 * downstream then draws each frame without waiting a source
                 * frame's duration for it.
                 */
                glib::ParamSpecInt64::builder("source-latency")
                    .nick("Source latency")
                    .blurb("Minimum latency answered for upstream in ns (-1 = as upstream answers)")
                    .minimum(-1)
                    .default_value(-1)
                    .mutable_playing()
                    .build(),
                glib::ParamSpecUInt64::builder("revision")
                    .nick("Revision")
                    .blurb("Commits pending changes; stamped on output buffers as their offset (0 = changes apply at once)")
                    .mutable_playing()
                    .build(),
            ]
        });
        PROPERTIES.as_ref()
    }

    fn set_property(&self, _id: usize, value: &glib::Value, pspec: &glib::ParamSpec) {
        if pspec.name() == "source-latency" {
            let ns: i64 = value.get().expect("type checked by GObject");
            *self.source_latency.lock().unwrap() = u64::try_from(ns).ok().map(gst::ClockTime::from_nseconds);
            // A live compositor downstream asks again and takes the new figure.
            let _ = self
                .obj()
                .post_message(gst::message::Latency::builder().src(&*self.obj()).build());
            return;
        }
        let mut live = self.settings.lock().unwrap();
        let before = live.clone();
        if pspec.name() == "revision" {
            if let Some(p) = self.pending.lock().unwrap().take() {
                *live = p;
            }
            *self.revision.lock().unwrap() = value.get().expect("type checked by GObject");
            let after = live.clone();
            drop(live);
            self.reconfigure_if_resized(&before, &after);
            return;
        }
        let deferred = *self.revision.lock().unwrap() > 0;
        let mut pending = self.pending.lock().unwrap();
        let s: &mut Settings = if deferred {
            pending.get_or_insert_with(|| before.clone())
        } else {
            &mut live
        };
        let f = || value.get::<f64>().expect("type checked by GObject");
        match pspec.name() {
            "box-width" => s.box_width = f(),
            "box-height" => s.box_height = f(),
            "margin" => s.margin = f(),
            "content-x" => s.content_x = f(),
            "content-y" => s.content_y = f(),
            "content-width" => s.content_width = f(),
            "content-height" => s.content_height = f(),
            "crop-left" => s.crop_left = f(),
            "crop-top" => s.crop_top = f(),
            "crop-right" => s.crop_right = f(),
            "crop-bottom" => s.crop_bottom = f(),
            "corner-radius" => s.corner_radius = f(),
            "stroke-width" => s.stroke_width = f(),
            "stroke-color" => s.stroke_color = value.get().expect("type checked by GObject"),
            "stroke-position" => s.stroke_position = value.get().expect("type checked by GObject"),
            "shadow-blur" => s.shadow_blur = f(),
            "shadow-offset-x" => s.shadow_offset_x = f(),
            "shadow-offset-y" => s.shadow_offset_y = f(),
            "shadow-color" => s.shadow_color = value.get().expect("type checked by GObject"),
            "opacity" => s.opacity = f(),
            name => unreachable!("unknown property {name}"),
        }
        drop(pending);
        let after = live.clone();
        drop(live);
        gst::debug!(CAT, imp = self, "{} = {:?}", pspec.name(), value);
        self.reconfigure_if_resized(&before, &after);
    }

    fn property(&self, _id: usize, pspec: &glib::ParamSpec) -> glib::Value {
        if pspec.name() == "source-latency" {
            return self
                .source_latency
                .lock()
                .unwrap()
                .map_or(-1i64, |t| t.nseconds() as i64)
                .to_value();
        }
        if pspec.name() == "revision" {
            return self.revision.lock().unwrap().to_value();
        }
        let s = match self.pending.lock().unwrap().clone() {
            Some(p) => p,
            None => self.settings.lock().unwrap().clone(),
        };
        match pspec.name() {
            "box-width" => s.box_width.to_value(),
            "box-height" => s.box_height.to_value(),
            "margin" => s.margin.to_value(),
            "content-x" => s.content_x.to_value(),
            "content-y" => s.content_y.to_value(),
            "content-width" => s.content_width.to_value(),
            "content-height" => s.content_height.to_value(),
            "crop-left" => s.crop_left.to_value(),
            "crop-top" => s.crop_top.to_value(),
            "crop-right" => s.crop_right.to_value(),
            "crop-bottom" => s.crop_bottom.to_value(),
            "corner-radius" => s.corner_radius.to_value(),
            "stroke-width" => s.stroke_width.to_value(),
            "stroke-color" => s.stroke_color.to_value(),
            "stroke-position" => s.stroke_position.to_value(),
            "shadow-blur" => s.shadow_blur.to_value(),
            "shadow-offset-x" => s.shadow_offset_x.to_value(),
            "shadow-offset-y" => s.shadow_offset_y.to_value(),
            "shadow-color" => s.shadow_color.to_value(),
            "opacity" => s.opacity.to_value(),
            name => unreachable!("unknown property {name}"),
        }
    }
}

impl LumaFrame {
    /// A size change renegotiates before the next buffer; anything else is
    /// only uniforms.
    fn reconfigure_if_resized(&self, before: &Settings, after: &Settings) {
        if let Some(input) = *self.input_size.lock().unwrap() {
            if geometry::output_size(before, input) != geometry::output_size(after, input) {
                gst::debug!(CAT, imp = self, "output size changes, reconfiguring");
                self.obj().reconfigure_src();
            }
        }
    }
}

impl GstObjectImpl for LumaFrame {}

impl ElementImpl for LumaFrame {
    fn metadata() -> Option<&'static gst::subclass::ElementMetadata> {
        static METADATA: LazyLock<gst::subclass::ElementMetadata> = LazyLock::new(|| {
            gst::subclass::ElementMetadata::new(
                "Luma frame",
                "Filter/Effect/Video",
                "Draws a layer for compositing in one GL pass: the source in a box with \
                 anti-aliased rounded corners, a stroke, a Gaussian drop shadow in a \
                 transparent margin, crop and opacity; RGBA out with straight alpha",
                "Project Luma <maintainers@projectluma.org>",
            )
        });
        Some(&*METADATA)
    }
}

impl BaseTransformImpl for LumaFrame {
    const MODE: gst_base::subclass::BaseTransformMode = gst_base::subclass::BaseTransformMode::NeverInPlace;
    const PASSTHROUGH_ON_SAME_CAPS: bool = false;
    const TRANSFORM_IP_ON_PASSTHROUGH: bool = false;

    fn propose_allocation(
        &self,
        decide_query: Option<&gst::query::Allocation>,
        query: &mut gst::query::Allocation,
    ) -> Result<(), gst::LoggableError> {
        self.parent_propose_allocation(decide_query, query)?;
        // A crop meta on the input selects the content (content-width 0).
        query.add_allocation_meta::<gst_video::VideoCropMeta>(None);
        Ok(())
    }

    fn query(&self, direction: gst::PadDirection, query: &mut gst::QueryRef) -> bool {
        if !BaseTransformImplExt::parent_query(self, direction, query) {
            return false;
        }
        if direction == gst::PadDirection::Src {
            if let gst::QueryViewMut::Latency(q) = query.view_mut() {
                if let Some(min) = *self.source_latency.lock().unwrap() {
                    let (live, _min, max) = q.result();
                    let max = max.map(|m| m.max(min));
                    q.set(live, min, max);
                }
            }
        }
        true
    }

    fn before_transform(&self, inbuf: &gst::BufferRef) {
        let meta = inbuf.meta::<gst_video::VideoCropMeta>().map(|m| m.rect());
        *self.crop_meta.lock().unwrap() = meta;
        let snapshot = (self.settings.lock().unwrap().clone(), *self.revision.lock().unwrap());
        *self.frame.lock().unwrap() = Some(snapshot);
        self.parent_before_transform(inbuf);
    }

    fn copy_metadata(&self, inbuf: &gst::BufferRef, outbuf: &mut gst::BufferRef) -> Result<(), gst::LoggableError> {
        self.parent_copy_metadata(inbuf, outbuf)?;
        if let Some((_, revision)) = *self.frame.lock().unwrap() {
            if revision > 0 {
                outbuf.set_offset(revision);
            }
        }
        Ok(())
    }

    fn fixate_caps(&self, direction: gst::PadDirection, caps: &gst::Caps, othercaps: gst::Caps) -> gst::Caps {
        // Fixating the source caps from fixed sink caps: ask for the canvas
        // size, unless downstream has already fixed another one.
        let input = caps
            .structure(0)
            .and_then(|s| Some((s.get::<i32>("width").ok()?, s.get::<i32>("height").ok()?, s.to_owned())));
        if othercaps.is_empty() {
            return othercaps;
        }
        let (Some((w, h, sink)), gst::PadDirection::Sink) = (input, direction) else {
            return self.parent_fixate_caps(direction, caps, othercaps);
        };
        let (ow, oh) = geometry::output_size(&self.settings.lock().unwrap(), (w, h));
        // Prefer any downstream structure that allows the canvas size, not
        // just the first: glvideomixer lists its pad's current caps first,
        // which would pin a resized layer to its old size. Only when nothing
        // allows it has downstream forced a size.
        let mut wanted = othercaps.clone();
        for s in wanted.make_mut().iter_mut() {
            s.set("width", ow);
            s.set("height", oh);
        }
        let fitting = othercaps.intersect_with_mode(&wanted, gst::CapsIntersectMode::First);
        let mut othercaps = if fitting.is_empty() { othercaps } else { fitting };
        othercaps.truncate();
        {
            let othercaps = othercaps.make_mut();
            let s = othercaps
                .structure_mut(0)
                .expect("caps from transform_caps are not empty");
            s.fixate_field_nearest_int("width", ow);
            s.fixate_field_nearest_int("height", oh);
            if let Ok(par) = sink.get::<gst::Fraction>("pixel-aspect-ratio") {
                if s.has_field("pixel-aspect-ratio") {
                    s.fixate_field_nearest_fraction("pixel-aspect-ratio", par);
                } else {
                    s.set("pixel-aspect-ratio", par);
                }
            }
            if let Ok(fps) = sink.get::<gst::Fraction>("framerate") {
                if s.has_field("framerate") {
                    s.fixate_field_nearest_fraction("framerate", fps);
                }
            }
        }
        othercaps.fixate();
        gst::debug!(CAT, imp = self, "fixated {caps} to {othercaps} (asked for {ow}x{oh})");
        othercaps
    }
}

impl GLBaseFilterImpl for LumaFrame {
    fn gl_start(&self) -> Result<(), gst::LoggableError> {
        let context =
            GLBaseFilterExt::context(&*self.obj()).ok_or_else(|| gst::loggable_error!(CAT, "no GL context"))?;
        let version = gst_gl::GLSLVersion::None;
        let profile = gst_gl::GLSLProfile::ES | gst_gl::GLSLProfile::COMPATIBILITY;
        let precision = gst_gl::GLShader::string_get_highest_precision(&context, version, profile);
        let shader = gst_gl::GLShader::new(&context);
        let vertex = gst_gl::GLSLStage::new_default_vertex(&context);
        vertex
            .compile()
            .map_err(|e| gst::loggable_error!(CAT, "vertex shader: {e}"))?;
        shader
            .attach_unlocked(&vertex)
            .map_err(|e| gst::loggable_error!(CAT, "{e}"))?;
        let fragment = gst_gl::GLSLStage::with_strings(
            &context,
            GL_FRAGMENT_SHADER,
            version,
            profile,
            &[precision.as_str(), FRAGMENT_SHADER],
        );
        fragment
            .compile()
            .map_err(|e| gst::loggable_error!(CAT, "fragment shader: {e}"))?;
        shader
            .attach_unlocked(&fragment)
            .map_err(|e| gst::loggable_error!(CAT, "{e}"))?;
        shader
            .link()
            .map_err(|e| gst::loggable_error!(CAT, "shader link: {e}"))?;
        *self.shader.lock().unwrap() = Some(shader);
        self.parent_gl_start()
    }

    fn gl_stop(&self) {
        self.shader.lock().unwrap().take();
        self.parent_gl_stop()
    }
}

impl GLFilterImpl for LumaFrame {
    const MODE: GLFilterMode = GLFilterMode::Texture;

    fn transform_internal_caps(
        &self,
        _direction: gst::PadDirection,
        caps: &gst::Caps,
        _filter_caps: Option<&gst::Caps>,
    ) -> Option<gst::Caps> {
        // Any size on the other side; fixate_caps picks the canvas size.
        let mut caps = caps.clone();
        for s in caps.make_mut().iter_mut() {
            s.set("width", gst::IntRange::new(1, i32::MAX));
            s.set("height", gst::IntRange::new(1, i32::MAX));
        }
        Some(caps)
    }

    fn set_caps(&self, incaps: &gst::Caps, outcaps: &gst::Caps) -> Result<(), gst::LoggableError> {
        let size = incaps
            .structure(0)
            .and_then(|s| Some((s.get::<i32>("width").ok()?, s.get::<i32>("height").ok()?)));
        *self.input_size.lock().unwrap() = size;
        gst::debug!(CAT, imp = self, "in {incaps}, out {outcaps}");
        GLFilterImplExt::parent_set_caps(self, incaps, outcaps)
    }

    fn filter_texture(&self, input: &gst_gl::GLMemory, output: &gst_gl::GLMemory) -> Result<(), gst::LoggableError> {
        let shader = self
            .shader
            .lock()
            .unwrap()
            .clone()
            .ok_or_else(|| gst::loggable_error!(CAT, "no shader"))?;
        let settings = match self.frame.lock().unwrap().clone() {
            Some((s, _)) => s,
            None => self.settings.lock().unwrap().clone(),
        };
        let meta = *self.crop_meta.lock().unwrap();
        let l = Layout::for_frame(
            &settings,
            (input.texture_width(), input.texture_height()),
            (output.texture_width(), output.texture_height()),
            meta,
        );
        let context =
            GLBaseFilterExt::context(&*self.obj()).ok_or_else(|| gst::loggable_error!(CAT, "no GL context"))?;
        let uniforms = shader.clone();
        context.thread_add(move |_| {
            let s = &uniforms;
            let f = |v: f64| v as f32;
            s.use_();
            s.set_uniform_2f("u_canvas", f(l.canvas.0), f(l.canvas.1));
            s.set_uniform_1f("u_aa", f(l.aa));
            s.set_uniform_4f("u_rect", f(l.centre.0), f(l.centre.1), f(l.half.0), f(l.half.1));
            s.set_uniform_1f("u_radius", f(l.radius));
            s.set_uniform_1f("u_stroke", f(l.stroke));
            s.set_uniform_1f("u_stroke_in", f(l.stroke_in));
            s.set_uniform_1f("u_stroke_out", f(l.stroke_out));
            s.set_uniform_3f("u_outer", f(l.outer_half.0), f(l.outer_half.1), f(l.outer_radius));
            let c = l.stroke_color;
            s.set_uniform_4f("u_stroke_color", f(c[0]), f(c[1]), f(c[2]), f(c[3]));
            let c = l.crop;
            s.set_uniform_4f("u_crop", f(c[0]), f(c[1]), f(c[2]), f(c[3]));
            let c = l.shadow_color;
            s.set_uniform_4f("u_shadow_color", f(c[0]), f(c[1]), f(c[2]), f(c[3]));
            s.set_uniform_2f("u_shadow_offset", f(l.shadow_offset.0), f(l.shadow_offset.1));
            s.set_uniform_1f("u_sigma", f(l.sigma));
            s.set_uniform_1f("u_sigma_min", f(geometry::SHADOW_SIGMA_MIN_PX * l.aa));
            s.set_uniform_1f("u_opacity", f(l.opacity));
            s.set_uniform_2f("u_footprint", f(l.footprint.0), f(l.footprint.1));
            s.set_uniform_1f("u_taps", f(l.taps));
        });
        self.obj().render_to_target_with_shader(input, output, &shader);
        Ok(())
    }
}
