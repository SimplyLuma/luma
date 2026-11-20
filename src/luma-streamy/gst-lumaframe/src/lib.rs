// SPDX-License-Identifier: MPL-2.0
//! GStreamer plugin `lumavideofx`: Luma's own video effects.
//!
//! * `lumaframe`: one composited layer in one GL pass (rounded corners,
//!   stroke, drop shadow, crop and opacity), for `glvideomixer` scenes.

pub mod geometry;
mod lumaframe;

pub use lumaframe::LumaFrame;

fn plugin_init(plugin: &gst::Plugin) -> Result<(), gst::glib::BoolError> {
    lumaframe::register(plugin)
}

gst::plugin_define!(
    lumavideofx,
    env!("CARGO_PKG_DESCRIPTION"),
    plugin_init,
    env!("CARGO_PKG_VERSION"),
    "MPL",
    "gst-lumaframe",
    "Luma",
    "https://github.com/SimplyLuma/luma"
);
