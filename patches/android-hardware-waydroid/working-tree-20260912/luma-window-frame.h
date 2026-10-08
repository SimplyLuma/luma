/* SPDX-License-Identifier: Apache-2.0
 * Shared App Kit frame geometry for native compatibility renderers.
 * No toolkit, process, input injection or client-content copying is required.
 */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <math.h>
#include "luma-frame-tokens.h"

typedef struct { int x, y, width, height; } LumaFrameRect;
typedef struct {
    LumaFrameRect outer, content;
    int header;
    bool decorated;
} LumaWindowFrame;

static inline LumaWindowFrame
luma_window_frame(int content_width, int content_height, int header, bool decorated)
{
    int gap = decorated ? LUMA_FRAME_GAP : 0;
    LumaWindowFrame f = {{0, 0, 0, 0}, {0, 0, 0, 0}, 0, false};
    f.decorated = decorated;
    f.header = decorated ? header : 0;
    f.content = (LumaFrameRect){gap, f.header, content_width, content_height};
    f.outer = (LumaFrameRect){0, 0, content_width + gap * 2,
                            content_height + f.header + gap};
    return f;
}

/* Fit a composed Android canvas in the content area without changing its
 * display size. Viewporter scales the existing buffer; input uses the inverse.
 */
static inline LumaFrameRect
luma_frame_fit(LumaFrameRect area, int source_width, int source_height)
{
    if (source_width < 1 || source_height < 1 || area.width < 1 || area.height < 1)
        return (LumaFrameRect){area.x, area.y, 0, 0};
    double scale = fmin((double)area.width / source_width,
                        (double)area.height / source_height);
    int width = (int)fmax(1, floor(source_width * scale));
    int height = (int)fmax(1, floor(source_height * scale));
    return (LumaFrameRect){area.x + (area.width - width) / 2,
                           area.y + (area.height - height) / 2, width, height};
}

static inline double
luma_frame_distance(double x, double y, LumaFrameRect r, double radius)
{
    radius = fmin(radius, fmin(r.width, r.height) / 2.0);
    double qx = fabs(x - (r.x + r.width / 2.0)) - (r.width / 2.0 - radius);
    double qy = fabs(y - (r.y + r.height / 2.0)) - (r.height / 2.0 - radius);
    return hypot(fmax(qx, 0), fmax(qy, 0)) + fmin(fmax(qx, qy), 0) - radius;
}

static inline double luma_frame_coverage(double distance)
{
    return fmin(1, fmax(0, 0.5 - distance));
}

/* Two-layer App Kit elevation, from the same tokens as native surfaces.
 * The signed-distance Gaussian is evaluated only in the exposed gutter. */
static inline double luma_frame_shadow(LumaFrameRect r, double radius,
                                       double x, double y, bool dark)
{
    double near_y = dark ? LUMA_FRAME_DARK_SHADOW_NEAR_Y : LUMA_FRAME_LIGHT_SHADOW_NEAR_Y;
    double blur = dark ? LUMA_FRAME_DARK_SHADOW_NEAR_BLUR : LUMA_FRAME_LIGHT_SHADOW_NEAR_BLUR;
    double opacity = dark ? LUMA_FRAME_DARK_SHADOW_NEAR_ALPHA : LUMA_FRAME_LIGHT_SHADOW_NEAR_ALPHA;
    double a = opacity * 0.5 * erfc(luma_frame_distance(x, y - near_y, r, radius) / (blur * 0.70710678118));
    int spread = dark ? LUMA_FRAME_DARK_SHADOW_FAR_SPREAD : LUMA_FRAME_LIGHT_SHADOW_FAR_SPREAD;
    LumaFrameRect far = {r.x - spread, r.y - spread,
                         r.width + 2 * spread, r.height + 2 * spread};
    if (far.width > 0 && far.height > 0) {
        double far_y = dark ? LUMA_FRAME_DARK_SHADOW_FAR_Y : LUMA_FRAME_LIGHT_SHADOW_FAR_Y;
        blur = dark ? LUMA_FRAME_DARK_SHADOW_FAR_BLUR : LUMA_FRAME_LIGHT_SHADOW_FAR_BLUR;
        opacity = dark ? LUMA_FRAME_DARK_SHADOW_FAR_ALPHA : LUMA_FRAME_LIGHT_SHADOW_FAR_ALPHA;
        double b = opacity * 0.5 * erfc(luma_frame_distance(x, y - far_y, far, fmax(0, radius + spread)) / (blur * 0.70710678118));
        a += b * (1 - a);
    }
    return a;
}

/* Premultiplied ARGB overlay. Transparent interior preserves the real app
 * surface; opaque corner masks and the inner stroke stay within its island.
 */
static inline uint32_t
luma_frame_pixel(LumaWindowFrame f, int x, int y, bool dark, bool square_corners)
{
    if (!f.decorated || x < 0 || y < 0 || x >= f.outer.width || y >= f.outer.height)
        return 0;
    /* The large central region never needs raster work, including on hover. */
    if (x >= f.content.x + LUMA_FRAME_ISLAND_RADIUS &&
        x < f.content.x + f.content.width - LUMA_FRAME_ISLAND_RADIUS &&
        y >= f.content.y + LUMA_FRAME_ISLAND_RADIUS &&
        y < f.content.y + f.content.height - LUMA_FRAME_ISLAND_RADIUS)
        return 0;
    double outer = luma_frame_coverage(luma_frame_distance(
        // Maximized is not fullscreen here. The shelf insets the work area, so
        // a maximized window still has desktop around it and keeps its corners
        // — the same rule libadwaita carries for Luma's own windows as
        // `.csd.maximized:not(.fullscreen)`. Only a surface that genuinely
        // fills the screen squares them.
        x + 0.5, y + 0.5, f.outer, square_corners ? 0 : LUMA_FRAME_WINDOW_RADIUS));
    double distance = luma_frame_distance(x + 0.5, y + 0.5, f.content, LUMA_FRAME_ISLAND_RADIUS);
    double hole = luma_frame_coverage(distance);
    double alpha = outer * (1 - hole);
    uint32_t base = dark ? LUMA_FRAME_DARK_WINDOW : LUMA_FRAME_LIGHT_WINDOW;
    double shade = luma_frame_shadow(f.content, LUMA_FRAME_ISLAND_RADIUS, x + 0.5, y + 0.5, dark);
    uint32_t shade_color = dark ? LUMA_FRAME_DARK_SHADOW_COLOR : LUMA_FRAME_LIGHT_SHADOW_COLOR;
    unsigned br = (unsigned)lround(((base >> 16) & 255) * (1 - shade) + ((shade_color >> 16) & 255) * shade);
    unsigned bg = (unsigned)lround(((base >> 8) & 255) * (1 - shade) + ((shade_color >> 8) & 255) * shade);
    unsigned bb = (unsigned)lround((base & 255) * (1 - shade) + (shade_color & 255) * shade);
    base = (br << 16) | (bg << 8) | bb;
    double border = hole * luma_frame_coverage(-distance - 1) * (dark ? 0.08 : 0.095);
    unsigned a = (unsigned)lround(255 * (alpha + border * (1 - alpha)));
    unsigned ink = dark ? 255 : 39;
    unsigned red = (unsigned)lround(((base >> 16) & 255) * alpha + ink * border * (1 - alpha));
    unsigned green = (unsigned)lround(((base >> 8) & 255) * alpha + ink * border * (1 - alpha));
    unsigned blue = (unsigned)lround((base & 255) * alpha + ink * border * (1 - alpha));
    return (a << 24) | (red << 16) | (green << 8) | blue;
}

/* Shared compatibility geometry mirrors the toolkit's connected controls:
 * three 20px targets in a 2px padded pill, at the trailing 8px header inset. */
static inline LumaFrameRect luma_frame_controls(LumaWindowFrame f)
{
    int height = LUMA_FRAME_CONTROL_SIZE + 2 * LUMA_FRAME_CONTROL_PADDING;
    int width = 3 * LUMA_FRAME_CONTROL_SIZE + 2 * LUMA_FRAME_CONTROL_PADDING;
    return (LumaFrameRect){f.outer.width - LUMA_FRAME_CONTROL_INSET - width,
                          (f.header - height) / 2, width, height};
}

static inline LumaFrameRect luma_frame_back(LumaWindowFrame f)
{
    LumaFrameRect controls = luma_frame_controls(f);
    return (LumaFrameRect){controls.x - LUMA_FRAME_BACK_GAP - controls.height,
                          controls.y, controls.height, controls.height};
}

/* Values deliberately match xdg_toplevel.resize_edge, allowing a native
 * compositor resize with the real input serial; no synthetic drag machinery. */
static inline unsigned luma_frame_resize_edge(LumaWindowFrame f, int x, int y,
                                               bool maximized)
{
    if (!f.decorated || maximized || x < 0 || y < 0 ||
        x >= f.outer.width || y >= f.outer.height) return 0;
    int band = LUMA_FRAME_RESIZE_BAND, corner = LUMA_FRAME_RESIZE_CORNER;
    bool left = x < band, right = x >= f.outer.width - band;
    bool top = y < band, bottom = y >= f.outer.height - band;
    if ((left || right) && y < corner) top = true;
    if ((left || right) && y >= f.outer.height - corner) bottom = true;
    if ((top || bottom) && x < corner) left = true;
    if ((top || bottom) && x >= f.outer.width - corner) right = true;
    return (top ? 1u : bottom ? 2u : 0u) | (left ? 4u : right ? 8u : 0u);
}

/* Premultiplied control-pill surface. Glyphs and hover use the same source
 * symbols and semantic colours as GtkWindowControls, rendered by the backend. */
static inline uint32_t luma_frame_control_surface(LumaFrameRect r, int x, int y, bool dark)
{
    double d = luma_frame_distance(x + 0.5, y + 0.5, r, r.height / 2.0);
    double coverage = luma_frame_coverage(d);
    double stroke = coverage * luma_frame_coverage(-d - 1) * 0.055;
    double shadow = luma_frame_shadow(r, r.height / 2.0, x + 0.5, y + 0.5, dark) * (1 - coverage);
    uint32_t fill = dark ? LUMA_FRAME_DARK_CONTENT : LUMA_FRAME_LIGHT_CONTENT;
    uint32_t shade = dark ? LUMA_FRAME_DARK_SHADOW_COLOR : LUMA_FRAME_LIGHT_SHADOW_COLOR;
    unsigned line = dark ? 255 : 33;
    unsigned a = (unsigned)lround(255 * (coverage + shadow));
    unsigned red = (unsigned)lround(((fill >> 16) & 255) * (coverage - stroke) + line * stroke + ((shade >> 16) & 255) * shadow);
    unsigned green = (unsigned)lround(((fill >> 8) & 255) * (coverage - stroke) + line * stroke + ((shade >> 8) & 255) * shadow);
    unsigned blue = (unsigned)lround((fill & 255) * (coverage - stroke) + line * stroke + (shade & 255) * shadow);
    return (a << 24) | (red << 16) | (green << 8) | blue;
}
static inline uint32_t luma_frame_controls_pixel(LumaWindowFrame f, int x, int y, bool dark)
{
    return luma_frame_control_surface(luma_frame_controls(f), x, y, dark);
}

/* Exact native 11px symbolic alpha, rasterized by GdkPixbuf/librsvg from
 * Prairie window controls. Lucide ISC/MIT notices are retained by the theme. */
/* source SHA256 a1c758be8a9110c05d13b6a90a7ca2710c45eb88fd3a035f7eeb21479ae3833e */
static const uint8_t luma_frame_close_glyph[121] = {
    0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,
    0,0,32,52,0,0,0,52,32,0,0,
    0,0,52,223,53,0,53,223,52,0,0,
    0,0,0,53,223,100,223,53,0,0,0,
    0,0,0,0,100,255,100,0,0,0,0,
    0,0,0,53,223,100,223,53,0,0,0,
    0,0,52,223,53,0,53,223,52,0,0,
    0,0,32,52,0,0,0,52,32,0,0,
    0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,
};
/* source SHA256 a5207f4e3cde7bee1bf2a29a4a6f0e15156a0dfa83342b0397fdae3b8c436865 */
static const uint8_t luma_frame_minimize_glyph[121] = {
    0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,31,1,0,1,31,0,0,0,
    0,0,0,140,144,1,144,140,0,0,0,
    0,0,0,1,152,208,152,1,0,0,0,
    0,0,0,0,1,90,1,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,
};
/* source SHA256 6e859341f48aec92fb414688e4c0967d6a82b677e78f5693928a7bdcf92f3d1b */
static const uint8_t luma_frame_maximize_glyph[121] = {
    0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,
    0,0,1,56,68,68,68,56,1,0,0,
    0,0,54,215,153,153,153,215,54,0,0,
    0,0,64,155,0,0,0,155,64,0,0,
    0,0,64,155,0,0,0,155,64,0,0,
    0,0,64,155,0,0,0,155,64,0,0,
    0,0,54,215,153,153,153,215,54,0,0,
    0,0,1,56,68,68,68,56,1,0,0,
    0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,
};
/* source SHA256 ac9f5c1916e12823b2f6143db6da32c4ea12d96114c18d7b06f535e4ac138b92 */
static const uint8_t luma_frame_restore_glyph[121] = {
    0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,134,153,153,153,55,0,0,
    0,0,10,51,155,170,161,179,151,0,0,
    0,0,140,193,170,170,224,156,151,0,0,
    0,0,151,68,0,0,163,167,151,0,0,
    0,0,151,68,0,0,163,152,131,0,0,
    0,0,151,118,68,68,188,55,0,0,0,
    0,0,56,153,153,153,141,10,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,
};
