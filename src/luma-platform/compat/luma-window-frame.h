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

/* The same LumaUI frame palette used by the native toolkit sheets. */
typedef enum { LUMA_FRAME_LIGHT, LUMA_FRAME_DARK, LUMA_FRAME_FROST, LUMA_FRAME_GLASS } LumaFrameMaterial;
typedef struct {
    uint32_t top, bottom, ink, muted, hover, close_hover, close_ink, edge;
} LumaFrameStyle;
#define LUMA_FRAME_STYLE(M) ((LumaFrameStyle){LUMA_FRAME_##M##_WINDOW_TOP, \
    LUMA_FRAME_##M##_WINDOW_BOTTOM, LUMA_FRAME_##M##_FRAME_INK, \
    LUMA_FRAME_##M##_FRAME_MUTED, LUMA_FRAME_##M##_WINDOW_CONTROL_HOVER, \
    LUMA_FRAME_##M##_WINDOW_CLOSE_HOVER, LUMA_FRAME_##M##_WINDOW_CLOSE_HOVER_INK, \
    LUMA_FRAME_##M##_ISLAND_EDGE})
static inline LumaFrameStyle luma_frame_style(LumaFrameMaterial material)
{
    switch (material) {
    case LUMA_FRAME_DARK: return LUMA_FRAME_STYLE(DARK);
    case LUMA_FRAME_FROST: return LUMA_FRAME_STYLE(FROST);
    case LUMA_FRAME_GLASS: return LUMA_FRAME_STYLE(GLASS);
    default: return LUMA_FRAME_STYLE(LIGHT);
    }
}
#undef LUMA_FRAME_STYLE

static inline uint32_t luma_frame_premultiply(uint32_t color, double coverage)
{
    unsigned a = (unsigned)lround((color >> 24) * coverage);
    return (a << 24) | ((((color >> 16) & 255) * a / 255) << 16) |
        ((((color >> 8) & 255) * a / 255) << 8) | ((color & 255) * a / 255);
}
static inline uint32_t luma_frame_over(uint32_t below, uint32_t above)
{
    unsigned inverse = 255 - (above >> 24);
    uint32_t out = 0;
    for (unsigned shift = 0; shift <= 24; shift += 8) {
        unsigned channel = ((above >> shift) & 255) + ((below >> shift) & 255) * inverse / 255;
        out |= (channel > 255 ? 255 : channel) << shift;
    }
    return out;
}

/* Transparent island interior keeps Android's real surface and input owner.
 * Maximized windows keep LumaUI's current corner treatment; fullscreen and
 * handheld bypass decoration at the caller. */
static inline uint32_t luma_frame_pixel_material(LumaWindowFrame f, int x, int y,
                                                LumaFrameMaterial material)
{
    if (!f.decorated || x < 0 || y < 0 || x >= f.outer.width || y >= f.outer.height)
        return 0;
    /* Most of an application window is untouched Android content. Avoid
     * distance/gradient work in that area during interactive resizing. */
    if (x >= f.content.x + LUMA_FRAME_ISLAND_RADIUS + 2 &&
            x < f.content.x + f.content.width - LUMA_FRAME_ISLAND_RADIUS - 2 &&
            y >= f.content.y + LUMA_FRAME_ISLAND_RADIUS + 2 &&
            y < f.content.y + f.content.height - LUMA_FRAME_ISLAND_RADIUS - 2)
        return 0;
    double outer = luma_frame_coverage(luma_frame_distance(
        x + 0.5, y + 0.5, f.outer, LUMA_FRAME_WINDOW_RADIUS));
    double distance = luma_frame_distance(x + 0.5, y + 0.5, f.content, LUMA_FRAME_ISLAND_RADIUS);
    double hole = luma_frame_coverage(distance);
    if (hole == 1 && distance < -1.5) return 0;
    LumaFrameStyle style = luma_frame_style(material);
    uint32_t top = luma_frame_premultiply(style.top, outer * (1 - hole));
    uint32_t bottom = luma_frame_premultiply(style.bottom, outer * (1 - hole));
    double progress = (double)y / (f.outer.height > 1 ? f.outer.height - 1 : 1);
    uint32_t gradient = 0;
    for (unsigned shift = 0; shift <= 24; shift += 8)
        gradient |= (uint32_t)lround(((top >> shift) & 255) * (1 - progress) +
                                    ((bottom >> shift) & 255) * progress) << shift;
    double edge = outer * hole * luma_frame_coverage(-distance - 1);
    return luma_frame_over(gradient, luma_frame_premultiply(style.edge, edge));
}
static inline uint32_t luma_frame_pixel(LumaWindowFrame f, int x, int y, bool dark, bool maximized)
{
    (void)maximized;
    return luma_frame_pixel_material(f, x, y, dark ? LUMA_FRAME_DARK : LUMA_FRAME_LIGHT);
}

/* Individual 30px LumaUI controls, two pixels apart. Back sits immediately
 * before Minimize with the title row's shared slot spacing. */
static inline LumaFrameRect luma_frame_controls(LumaWindowFrame f)
{
    int width = 3 * LUMA_FRAME_CONTROL_SIZE + 2 * LUMA_FRAME_CONTROL_GAP;
    return (LumaFrameRect){f.outer.width - LUMA_FRAME_CONTROL_INSET - width,
                          (f.header - LUMA_FRAME_CONTROL_SIZE) / 2,
                          width, LUMA_FRAME_CONTROL_SIZE};
}
static inline LumaFrameRect luma_frame_back(LumaWindowFrame f)
{
    LumaFrameRect controls = luma_frame_controls(f);
    return (LumaFrameRect){controls.x - LUMA_FRAME_BACK_GAP - LUMA_FRAME_CONTROL_SIZE,
                          controls.y, LUMA_FRAME_CONTROL_SIZE, LUMA_FRAME_CONTROL_SIZE};
}
/* 0 Minimize, 1 Maximize, 2 Close, 3 Android Back. */
static inline LumaFrameRect luma_frame_control(LumaWindowFrame f, unsigned index)
{
    if (index == 3) return luma_frame_back(f);
    LumaFrameRect r = luma_frame_controls(f);
    r.x += (int)index * (LUMA_FRAME_CONTROL_SIZE + LUMA_FRAME_CONTROL_GAP);
    r.width = LUMA_FRAME_CONTROL_SIZE;
    return r;
}
static inline int luma_frame_control_at(LumaWindowFrame f, int x, int y)
{
    if (!f.decorated) return -1;
    for (unsigned index = 0; index < 4; ++index) {
        LumaFrameRect r = luma_frame_control(f, index);
        if (x >= r.x && x < r.x + r.width && y >= r.y && y < r.y + r.height)
            return (int)index;
    }
    return -1;
}
static inline uint32_t luma_frame_button_pixel(LumaFrameRect r, int x, int y,
                                               LumaFrameMaterial material, bool close)
{
    LumaFrameStyle style = luma_frame_style(material);
    double coverage = luma_frame_coverage(luma_frame_distance(
        x + 0.5, y + 0.5, r, LUMA_FRAME_CONTROL_RADIUS));
    return luma_frame_premultiply(close ? style.close_hover : style.hover, coverage);
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

#include "luma-frame-glyphs.h"
