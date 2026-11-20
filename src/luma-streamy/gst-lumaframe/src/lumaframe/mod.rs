// SPDX-License-Identifier: MPL-2.0
use gst::glib;
use gst::prelude::*;

mod imp;

glib::wrapper! {
    /// `lumaframe`: see the element documentation in `imp.rs` and README.md.
    pub struct LumaFrame(ObjectSubclass<imp::LumaFrame>)
        @extends gst_gl::GLFilter, gst_gl::GLBaseFilter, gst_base::BaseTransform, gst::Element, gst::Object;
}

pub fn register(plugin: &gst::Plugin) -> Result<(), glib::BoolError> {
    gst::Element::register(Some(plugin), "lumaframe", gst::Rank::NONE, LumaFrame::static_type())
}
