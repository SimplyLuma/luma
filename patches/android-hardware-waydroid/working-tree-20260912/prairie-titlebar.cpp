/*
 * Copyright (C) 2026 Project Luma
 * SPDX-License-Identifier: Apache-2.0
 */

#include "prairie-titlebar.h"

#include "wayland-hwc.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <cctype>
#include <png.h>
#include <fcntl.h>
#include <linux/memfd.h>
#include <log/log.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <syscall.h>
#include <unistd.h>

#include <ft2build.h>
#include FT_FREETYPE_H
#include FT_MULTIPLE_MASTERS_H

namespace prairie {
LumaWindowFrame frame(const window& window) {
    auto geometry = luma_window_frame(window.content_width, window.content_height,
                                     kTitlebarHeight, window.has_prairie_titlebar);
    return geometry;
}
namespace {
enum class SurfaceTreatment { Light, Dark, Frost, Glass };

SurfaceTreatment read_surface_treatment() {
    char value[PROPERTY_VALUE_MAX] {};
    property_get("persist.luma.surface_treatment", value, "light");
    if (std::strcmp(value, "dark") == 0) return SurfaceTreatment::Dark;
    if (std::strcmp(value, "frost") == 0) return SurfaceTreatment::Frost;
    if (std::strcmp(value, "glass") == 0) return SurfaceTreatment::Glass;
    return SurfaceTreatment::Light;
}

const char* treatment_name(SurfaceTreatment treatment) {
    switch (treatment) {
    case SurfaceTreatment::Dark: return "dark";
    case SurfaceTreatment::Frost: return "frost";
    case SurfaceTreatment::Glass: return "glass";
    default: return "light";
    }
}

uint32_t scale_premultiplied(uint32_t pixel, uint32_t opacity) {
    const uint32_t alpha = ((pixel >> 24) & 0xff) * opacity / 255;
    const uint32_t red = ((pixel >> 16) & 0xff) * opacity / 255;
    const uint32_t green = ((pixel >> 8) & 0xff) * opacity / 255;
    const uint32_t blue = (pixel & 0xff) * opacity / 255;
    return (alpha << 24) | (red << 16) | (green << 8) | blue;
}

uint32_t frame_opacity(SurfaceTreatment treatment) {
    if (treatment == SurfaceTreatment::Glass) return 79;   // 31%
    if (treatment == SurfaceTreatment::Frost) return 186;  // 73%
    return 255;
}

uint32_t control_opacity(SurfaceTreatment treatment) {
    if (treatment == SurfaceTreatment::Glass) return 219;  // 86%
    if (treatment == SurfaceTreatment::Frost) return 235;  // 92%
    return 255;
}

int outer_width(const window& window) { return frame(window).outer.width; }
bool dark_frame(const window& window) { return window.surface_treatment == "dark"; }
uint32_t frame_ink(const window& window) { return dark_frame(window) ? LUMA_FRAME_DARK_INK : LUMA_FRAME_LIGHT_INK; }
int control_left(const window& window, int kind) {
    // Match the native order at the right: minimize, maximize, close.
    const int position = kind == 0 ? 0 : kind == 1 ? 2 : 1;
    return luma_frame_controls(frame(window)).x + LUMA_FRAME_CONTROL_PADDING +
        (2 - position) * LUMA_FRAME_CONTROL_SIZE;
}


constexpr uint32_t kCloseHover = 0x29bd5a4e;
constexpr uint32_t kMinimizeHover = 0x29b0782b;
constexpr uint32_t kMaximizeHover = 0x294f8a52;
constexpr uint32_t kHeaderHover = 0xffeceef1;
constexpr uint32_t kCloseGlyphHover = 0xffbd5a4e;
constexpr uint32_t kMinimizeGlyphHover = 0xffb0782b;
constexpr uint32_t kMaximizeGlyphHover = 0xff4f8a52;
constexpr int kIconSize = 11;
// Luma's own windows carry a three-layer elevation rather than one blur: a
// tight contact shadow, a mid lift, and a broad ambient pool. The values are
// the window elevation rule in the patched libadwaita — on dark
//   0 3px 8px          rgba(9,11,14,.38)
//   0 22px 52px -15px  rgba(9,11,14,.72)
//   0 64px 120px -30px rgba(9,11,14,.82)
// and correspondingly lighter on a light desktop.
//
// This was a single Gaussian at four percent, matched against a small island's
// shadow instead of a window's. Halved again by the edge coverage term below,
// its darkest pixel came out at alpha 5 of 255 — which is why Android windows
// looked like they had no shadow: they very nearly did not.
struct ShadowLayer {
    int offset_y;
    float sigma;   // A CSS blur radius is about two sigma.
    float spread;
    float opacity;
};

constexpr ShadowLayer kShadowLayersDark[] = {
    {3, 4.0f, 0.0f, 0.38f},
    {22, 26.0f, -15.0f, 0.72f},
    {64, 60.0f, -30.0f, 0.82f},
};
constexpr ShadowLayer kShadowLayersLight[] = {
    {2, 2.5f, 0.0f, 0.18f},
    {18, 21.0f, -14.0f, 0.42f},
    {54, 55.0f, -28.0f, 0.56f},
};
constexpr size_t kShadowLayerCount =
    sizeof(kShadowLayersDark) / sizeof(kShadowLayersDark[0]);
// Far enough out that the broadest layer has faded before the buffer ends: a
// shadow clipped by its own surface reads as a straight edge.
constexpr int kShadowInset = 148;


uint32_t premultiplied(uint32_t rgb, uint32_t alpha) {
    const uint32_t red = ((rgb >> 16) & 0xff) * alpha / 255;
    const uint32_t green = ((rgb >> 8) & 0xff) * alpha / 255;
    const uint32_t blue = (rgb & 0xff) * alpha / 255;
    return (alpha << 24) | (red << 16) | (green << 8) | blue;
}

void composite_premultiplied(uint32_t& destination, uint32_t source) {
    const uint32_t alpha = source >> 24;
    const uint32_t inverse = 255 - alpha;
    const uint32_t output_alpha = alpha + (destination >> 24) * inverse / 255;
    const uint32_t red = ((source >> 16) & 0xff) +
        ((destination >> 16) & 0xff) * inverse / 255;
    const uint32_t green = ((source >> 8) & 0xff) +
        ((destination >> 8) & 0xff) * inverse / 255;
    const uint32_t blue = (source & 0xff) +
        (destination & 0xff) * inverse / 255;
    destination = (output_alpha << 24) |
        (std::min(255u, red) << 16) |
        (std::min(255u, green) << 8) | std::min(255u, blue);
}

void composite(uint32_t& destination, uint32_t source) {
    composite_premultiplied(destination,
        premultiplied(source & 0x00ffffff, source >> 24));
}

void pixel(window& window, int x, int y, uint32_t color) {
    if (x < 0 || y < 0 || x >= outer_width(window) || y >= kTitlebarHeight)
        return;
    auto* pixels = static_cast<uint32_t*>(window.titlebar_data);
    composite(pixels[y * outer_width(window) + x], color);
}

void premultiplied_pixel(window& window, int x, int y, uint32_t color) {
    if (x < 0 || y < 0 || x >= outer_width(window) || y >= kTitlebarHeight)
        return;
    auto* pixels = static_cast<uint32_t*>(window.titlebar_data);
    composite_premultiplied(pixels[y * outer_width(window) + x], color);
}

void disc(window& window, int left, int top, int diameter, uint32_t color) {
    constexpr int samples = 4;
    const float radius = static_cast<float>(diameter) / 2.0f;
    const float center_x = static_cast<float>(left) + radius;
    const float center_y = static_cast<float>(top) + radius;
    for (int y = top; y < top + diameter; ++y) {
        for (int x = left; x < left + diameter; ++x) {
            int inside = 0;
            for (int sample_y = 0; sample_y < samples; ++sample_y) {
                for (int sample_x = 0; sample_x < samples; ++sample_x) {
                    const float px = x + (sample_x + 0.5f) / samples;
                    const float py = y + (sample_y + 0.5f) / samples;
                    const float dx = px - center_x;
                    const float dy = py - center_y;
                    if (dx * dx + dy * dy <= radius * radius)
                        ++inside;
                }
            }
            const uint32_t alpha = (color >> 24) * inside /
                (samples * samples);
            if (alpha != 0)
                pixel(window, x, y, (alpha << 24) | (color & 0x00ffffff));
        }
    }
}

void draw_mask(window& window, int left, int top, const uint8_t* mask,
               uint32_t color) {
    for (int y = 0; y < kIconSize; ++y) {
        for (int x = 0; x < kIconSize; ++x) {
            const uint32_t alpha = (color >> 24) *
                mask[y * kIconSize + x] / 255;
            if (alpha != 0)
                pixel(window, left + x, top + y,
                      (alpha << 24) | (color & 0x00ffffff));
        }
    }
}

void draw_control(window& window, int button_left, int kind) {
    constexpr int circle_top = (kTitlebarHeight - kControlDiameter) / 2;
    const int circle_left = button_left +
        (kControlAllocation - kControlDiameter) / 2;
    const bool hovered = window.hovered_chrome_target == kind + 2;
    if (hovered) {
        const uint32_t hover = kind == 0 ? kCloseHover :
            (kind == 1 ? kMinimizeHover : kMaximizeHover);
        disc(window, circle_left + 1, circle_top + 1,
             kControlDiameter - 2, hover);
    }

    const uint32_t glyph = hovered ?
        (kind == 0 ? kCloseGlyphHover :
         (kind == 1 ? kMinimizeGlyphHover : kMaximizeGlyphHover)) : frame_ink(window);
    const uint8_t* mask = kind == 0 ? luma_frame_close_glyph :
        (kind == 1 ? luma_frame_minimize_glyph :
         (window.maximized ? luma_frame_restore_glyph : luma_frame_maximize_glyph));
    const int icon_left = button_left + (kControlAllocation - kIconSize) / 2;
    constexpr int icon_top = (kTitlebarHeight - kIconSize) / 2;
    draw_mask(window, icon_left, icon_top, mask, glyph);
}

void draw_back(window& window) {
    const int left = luma_frame_back(frame(window)).x;
    constexpr int center_y = kTitlebarHeight / 2;
    if (window.hovered_chrome_target ==
            static_cast<int>(TitlebarTarget::Back)) {
        constexpr int radius = 8;
        for (int y = center_y - kBackButtonSize / 2;
                y < center_y + kBackButtonSize / 2; ++y) {
            for (int x = left; x < left + kBackButtonSize; ++x) {
                const int nearest_x = std::clamp(x, left + radius,
                                                 left + kBackButtonSize - radius - 1);
                const int nearest_y = std::clamp(y, center_y - kBackButtonSize / 2 + radius,
                                                 center_y + kBackButtonSize / 2 - radius - 1);
                const int dx = x - nearest_x;
                const int dy = y - nearest_y;
                if (dx * dx + dy * dy <= radius * radius)
                    pixel(window, x, y, dark_frame(window) ? LUMA_FRAME_DARK_CONTENT : kHeaderHover);
            }
        }
    }
    // Rotate the exact App Kit minimize chevron, preserving its optical size
    // and antialiasing instead of drawing an oversized ten-pixel back arrow.
    uint8_t back_mask[kIconSize * kIconSize];
    for (int y = 0; y < kIconSize; ++y)
        for (int x = 0; x < kIconSize; ++x)
            back_mask[y * kIconSize + x] = luma_frame_minimize_glyph[(kIconSize - 1 - x) * kIconSize + y];
    draw_mask(window, left + (kBackButtonSize - kIconSize) / 2,
              (kTitlebarHeight - kIconSize) / 2, back_mask, frame_ink(window));
}

uint32_t rounded_top_coverage(int width, int x, int y) {
    if (y >= kWindowRadius ||
            (x >= kWindowRadius && x < width - kWindowRadius))
        return 255;
    constexpr int samples = 4;
    constexpr float radius = static_cast<float>(kWindowRadius);
    const int local_x = x < kWindowRadius ? x : width - 1 - x;
    int inside = 0;
    for (int sample_y = 0; sample_y < samples; ++sample_y) {
        for (int sample_x = 0; sample_x < samples; ++sample_x) {
            const float px = local_x + (sample_x + 0.5f) / samples;
            const float py = y + (sample_y + 0.5f) / samples;
            const float dx = px - radius;
            const float dy = py - radius;
            if (dx * dx + dy * dy <= radius * radius)
                ++inside;
        }
    }
    return 255 * inside / (samples * samples);
}


float rounded_rect_distance(float x, float y, float left, float top,
                            float right, float bottom, float radius) {
    const float half_width = (right - left) / 2.0f;
    const float half_height = (bottom - top) / 2.0f;
    const float center_x = (left + right) / 2.0f;
    const float center_y = (top + bottom) / 2.0f;
    const float qx = std::abs(x - center_x) - half_width + radius;
    const float qy = std::abs(y - center_y) - half_height + radius;
    return std::hypot(std::max(qx, 0.0f), std::max(qy, 0.0f)) +
        std::min(std::max(qx, qy), 0.0f) - radius;
}

std::string ellipsize(FT_Face face, const std::string& title, int max_width) {
    auto width_of = [&](const std::string& value) {
        int width = 0;
        for (unsigned char character : value) {
            if (FT_Load_Char(face, character, FT_LOAD_DEFAULT) == 0)
                width += face->glyph->advance.x >> 6;
        }
        return width;
    };
    if (width_of(title) <= max_width)
        return title;
    std::string result = title;
    constexpr const char* suffix = "...";
    while (!result.empty() && width_of(result + suffix) > max_width)
        result.pop_back();
    return result + suffix;
}

void draw_app_icon(window& window) {
    constexpr int size = LUMA_FRAME_IDENTITY_ICON_SIZE;
    if (!window.prairie_icon_loaded && !window.appID.empty()) {
        window.prairie_icon_loaded = true;
        // Reuse WaydroidService's exported icons. Treat the file as untrusted:
        // no path components, symlinks, oversized files or allocation bombs.
        const bool valid = window.appID.size() <= 255 && std::all_of(
            window.appID.begin(), window.appID.end(), [](unsigned char c) {
                return std::isalnum(c) || c == '.' || c == '_';
            });
        if (valid) {
            const std::string path = "/data/icons/" + window.appID + ".png";
            const int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
            struct stat st {};
            if (fd >= 0 && fstat(fd, &st) == 0 && S_ISREG(st.st_mode) && st.st_size > 0 && st.st_size <= 2 * 1024 * 1024) {
                FILE* file = fdopen(fd, "rb");
                if (file) {
                    png_image image {}; image.version = PNG_IMAGE_VERSION;
                    if (png_image_begin_read_from_stdio(&image, file) && image.width > 0 && image.height > 0 && image.width <= 1024 && image.height <= 1024) {
                        image.format = PNG_FORMAT_RGBA;
                        std::vector<uint8_t> bytes(PNG_IMAGE_SIZE(image));
                        if (png_image_finish_read(&image, nullptr, bytes.data(), 0, nullptr)) {
                            window.prairie_icon.resize(size * size);
                            for (int y = 0; y < size; ++y) for (int x = 0; x < size; ++x) {
                                // Area sample the exported icon once, then retain the small tile.
                                unsigned long a=0, red=0, green=0, blue=0, count=0;
                                unsigned y0=y*image.height/size, y1=std::max(y0+1,(y+1)*image.height/size);
                                unsigned x0=x*image.width/size, x1=std::max(x0+1,(x+1)*image.width/size);
                                for (unsigned sy=y0; sy<y1; ++sy) for (unsigned sx=x0; sx<x1; ++sx) {
                                    const uint8_t* c=&bytes[(sy*image.width+sx)*4];
                                    a+=c[3]; red+=c[0]*c[3]; green+=c[1]*c[3]; blue+=c[2]*c[3]; ++count;
                                }
                                window.prairie_icon[y*size+x] = a ? ((a/count)<<24)|((red/a)<<16)|((green/a)<<8)|(blue/a) : 0;
                            }
                        }
                    }
                    png_image_free(&image); fclose(file);
                } else close(fd);
            } else if (fd >= 0) close(fd);
        }
    }
    if (window.prairie_icon.size() != size*size) return;
    LumaFrameRect rect = {0,0,size,size};
    for (int y=0; y<size; ++y) for(int x=0; x<size; ++x) {
        uint32_t c=window.prairie_icon[y*size+x];
        double coverage=luma_frame_coverage(luma_frame_distance(x+0.5,y+0.5,rect,size*0.25));
        premultiplied_pixel(window, LUMA_FRAME_IDENTITY_INSET+x,
            (kTitlebarHeight-size)/2+y,
            scale_premultiplied(c, (uint32_t)lround(255 * coverage)));
    }
}

void draw_title(window& window) {
    FT_Library library = nullptr;
    FT_Face face = nullptr;
    if (FT_Init_FreeType(&library) != 0)
        return;
    if (FT_New_Face(library, "/vendor/etc/fonts/Figtree-VF.ttf", 0, &face) != 0) {
        FT_Done_FreeType(library);
        return;
    }
    // Match the shared App Kit Figtree title size and semibold weight.
    FT_Set_Char_Size(face, 0, LUMA_FRAME_TITLE_SIZE * 64, 72, 72);
    FT_Fixed weight = LUMA_FRAME_TITLE_WEIGHT << 16;
    FT_Set_Var_Design_Coordinates(face, 1, &weight);
    const int title_left = LUMA_FRAME_IDENTITY_INSET + LUMA_FRAME_IDENTITY_ICON_SIZE + LUMA_FRAME_IDENTITY_GAP;
    const int max_width = std::max(0, luma_frame_back(frame(window)).x - LUMA_FRAME_IDENTITY_GAP - title_left);
    const std::string text = ellipsize(face, window.title, max_width);
    int pen_x = title_left;
    const uint32_t ink = frame_ink(window);
    const int ascender = face->size->metrics.ascender >> 6;
    const int descender = -(face->size->metrics.descender >> 6);
    const int baseline = (kTitlebarHeight - ascender - descender) / 2 + ascender;
    for (unsigned char character : text) {
        if (FT_Load_Char(face, character, FT_LOAD_RENDER) != 0)
            continue;
        const FT_Bitmap& bitmap = face->glyph->bitmap;
        const int glyph_x = pen_x + face->glyph->bitmap_left;
        const int glyph_y = baseline - face->glyph->bitmap_top;
        for (unsigned int y = 0; y < bitmap.rows; ++y) {
            for (unsigned int x = 0; x < bitmap.width; ++x) {
                const uint32_t alpha = bitmap.buffer[y * bitmap.pitch + x];
                pixel(window, glyph_x + x, glyph_y + y,
                      (alpha << 24) | (ink & 0x00ffffff));
            }
        }
        pen_x += face->glyph->advance.x >> 6;
    }
    FT_Done_Face(face);
    FT_Done_FreeType(library);
}

}  // namespace

bool desktop_chrome_enabled() {
    char value[PROPERTY_VALUE_MAX] {};
    property_get("persist.luma.device_class", value, "desktop");
    return std::strcmp(value, "handheld") != 0;
}

void redraw_shadow(window& window) {
    if (!window.shadow_surface)
        return;
    if (window.shadow_buffer)
        wl_buffer_destroy(window.shadow_buffer);
    window.shadow_buffer = nullptr;
    if (window.shadow_data && window.shadow_data_size)
        munmap(window.shadow_data, window.shadow_data_size);
    window.shadow_data = nullptr;
    window.shadow_data_size = 0;

    // Same rule as the corners: a maximized window is inset by the shelf and
    // still casts onto desktop, so it keeps its shadow. Only a fullscreen
    // surface has nothing to cast onto.
    if (window.fullscreen || outer_width(window) < 1 ||
            outer_width(window) > 4096 || window.content_height > 4096 ||
            window.content_height < 1) {
        wl_surface_attach(window.shadow_surface, nullptr, 0, 0);
        wl_surface_commit(window.shadow_surface);
        return;
    }

    const int frame_width = outer_width(window);
    const int frame_height = frame(window).outer.height;
    const bool shadow_dark = dark_frame(window);
    const ShadowLayer* layers =
        shadow_dark ? kShadowLayersDark : kShadowLayersLight;
    // The pool a window casts is the desktop's own near-black, not the slate
    // the frame itself is drawn in.
    const uint32_t shadow_rgb = shadow_dark ? 0x00090b0e : 0x00191b1f;
    const int width = frame_width + 2 * kShadowInset;
    const int height = frame_height + 2 * kShadowInset;
    const size_t size = static_cast<size_t>(width) * height * sizeof(uint32_t);
    const int fd = syscall(SYS_memfd_create, "prairie-window-shadow", MFD_CLOEXEC);
    if (fd < 0 || ftruncate(fd, size) != 0) {
        if (fd >= 0)
            close(fd);
        return;
    }
    window.shadow_data = mmap(nullptr, size, PROT_READ | PROT_WRITE,
                              MAP_SHARED, fd, 0);
    if (window.shadow_data == MAP_FAILED) {
        window.shadow_data = nullptr;
        close(fd);
        return;
    }
    window.shadow_data_size = size;
    auto* pixels = static_cast<uint32_t*>(window.shadow_data);
    std::fill_n(pixels, static_cast<size_t>(width) * height, 0u);

    for (int y = 0; y < height; ++y) {
        for (int x = 0; x < width; ++x) {
            const float local_x = x - kShadowInset + 0.5f;
            const float local_y = y - kShadowInset + 0.5f;
            const bool deep_inside = local_x >= 1.0f &&
                local_x < frame_width - 1.0f && local_y >= 1.0f &&
                local_y < frame_height - 1.0f &&
                !((local_x < kWindowRadius ||
                   local_x >= frame_width - kWindowRadius) &&
                  (local_y < kWindowRadius ||
                   local_y >= frame_height - kWindowRadius));
            if (deep_inside)
                continue;

            // GTK's box-shadow is a Gaussian convolution of the whole window
            // silhouette. At the geometric edge only half the kernel covers
            // the shape, which is what the erfc term expresses; a
            // point-sampled Gaussian sits at full opacity there and draws a
            // dark seam along the frame instead.
            constexpr float sqrt_two = 1.41421356237f;
            float shadow_alpha_f = 0.0f;
            for (size_t index = 0; index < kShadowLayerCount; ++index) {
                const ShadowLayer& layer = layers[index];
                const float distance = rounded_rect_distance(
                    local_x, local_y, 0.0f, static_cast<float>(layer.offset_y),
                    static_cast<float>(frame_width),
                    static_cast<float>(frame_height + layer.offset_y),
                    static_cast<float>(kWindowRadius));
                const float outside = std::max(0.0f, distance - layer.spread);
                const float coverage = 0.5f * std::erfc(
                    outside / (sqrt_two * layer.sigma));
                // Layers stack the way overlapping translucency does, not by
                // addition: three eighty-percent layers are darker than one,
                // never two hundred and forty percent.
                shadow_alpha_f += (1.0f - shadow_alpha_f) * layer.opacity * coverage;
            }
            const uint32_t shadow_alpha = static_cast<uint32_t>(std::round(
                255.0f * std::min(1.0f, shadow_alpha_f)));
            if (shadow_alpha != 0)
                pixels[y * width + x] =
                    premultiplied(shadow_rgb, shadow_alpha);

            const float frame_distance = rounded_rect_distance(
                local_x, local_y, 0.0f, 0.0f,
                static_cast<float>(frame_width),
                static_cast<float>(frame_height),
                static_cast<float>(kWindowRadius));
            if (std::abs(frame_distance) <= 0.75f)
                composite_premultiplied(pixels[y * width + x],
                                        premultiplied(0x00111827, 26));
        }
    }

    wl_shm_pool* pool = wl_shm_create_pool(window.display->shm, fd, size);
    close(fd);
    window.shadow_buffer = wl_shm_pool_create_buffer(
        pool, 0, width, height, width * sizeof(uint32_t),
        WL_SHM_FORMAT_ARGB8888);
    wl_shm_pool_destroy(pool);
    wl_surface_attach(window.shadow_surface, window.shadow_buffer, 0, 0);
    wl_surface_damage(window.shadow_surface, 0, 0, width, height);
    wl_surface_commit(window.shadow_surface);
}

TitlebarTarget hit_test(const window& window, int x, int y) {
    if (!window.has_prairie_titlebar)
        return TitlebarTarget::Content;
    const auto content = frame(window).content;
    if (y >= kTitlebarHeight) {
        if (x >= content.x && x < content.x + content.width &&
            y >= content.y && y < content.y + content.height)
            return TitlebarTarget::Content;
        return TitlebarTarget::Drag;
    }
    if (y < 0)
        return TitlebarTarget::Content;
    const int half_hit = kControlHitSize / 2;
    for (int index = 0; index < 3; ++index) {
        const int button_left = control_left(window, index);
        const int center = button_left + kControlAllocation / 2;
        if (x >= center - half_hit && x < center + half_hit) {
            if (index == 0)
                return TitlebarTarget::Close;
            if (index == 1)
                return TitlebarTarget::Minimize;
            return TitlebarTarget::Maximize;
        }
    }
    const int back_left = luma_frame_back(frame(window)).x;
    if (x >= back_left && x < back_left + kBackButtonSize)
        return TitlebarTarget::Back;
    return TitlebarTarget::Drag;
}

bool create_titlebar(window& window) {
    if (!desktop_chrome_enabled() || !window.display->subcompositor ||
            !window.display->viewporter || !window.display->wm_base ||
            property_get_bool("persist.waydroid.no_background_subsurface", false))
        return false;
    window.shadow_surface = wl_compositor_create_surface(window.display->compositor);
    window.shadow_subsurface = wl_subcompositor_get_subsurface(
        window.display->subcompositor, window.shadow_surface, window.surface);
    wl_subsurface_set_position(window.shadow_subsurface,
                               -kShadowInset, -kShadowInset);
    wl_subsurface_place_below(window.shadow_subsurface, window.surface);
    wl_subsurface_set_desync(window.shadow_subsurface);
    wl_region* shadow_input =
        wl_compositor_create_region(window.display->compositor);
    wl_surface_set_input_region(window.shadow_surface, shadow_input);
    wl_region_destroy(shadow_input);
    window.titlebar_surface = wl_compositor_create_surface(window.display->compositor);
    window.titlebar_subsurface = wl_subcompositor_get_subsurface(
        window.display->subcompositor, window.titlebar_surface, window.surface);
    wl_surface_set_user_data(window.titlebar_surface, &window);
    wl_subsurface_set_position(window.titlebar_subsurface, 0, 0);
    wl_subsurface_set_desync(window.titlebar_subsurface);
    wl_region* input_region = wl_compositor_create_region(window.display->compositor);
    for (int y = 0; y < kTitlebarHeight; ++y) {
        int left = 0;
        int right = outer_width(window);
        while (left < right && rounded_top_coverage(outer_width(window), left, y) < 128)
            ++left;
        while (right > left &&
                rounded_top_coverage(outer_width(window), right - 1, y) < 128)
            --right;
        if (right > left)
            wl_region_add(input_region, left, y, right - left, 1);
    }
    wl_surface_set_input_region(window.titlebar_surface, input_region);
    wl_region_destroy(input_region);
    window.has_prairie_titlebar = true;
    return true;
}

void destroy_titlebar(window& window) {
    if (window.shadow_buffer)
        wl_buffer_destroy(window.shadow_buffer);
    window.shadow_buffer = nullptr;
    if (window.shadow_data && window.shadow_data_size)
        munmap(window.shadow_data, window.shadow_data_size);
    window.shadow_data = nullptr;
    window.shadow_data_size = 0;
    if (window.shadow_subsurface)
        wl_subsurface_destroy(window.shadow_subsurface);
    window.shadow_subsurface = nullptr;
    if (window.shadow_surface)
        wl_surface_destroy(window.shadow_surface);
    window.shadow_surface = nullptr;
    if (window.titlebar_buffer)
        wl_buffer_destroy(window.titlebar_buffer);
    window.titlebar_buffer = nullptr;
    if (window.titlebar_data && window.titlebar_data_size)
        munmap(window.titlebar_data, window.titlebar_data_size);
    window.titlebar_data = nullptr;
    window.titlebar_data_size = 0;
    if (window.titlebar_subsurface)
        wl_subsurface_destroy(window.titlebar_subsurface);
    window.titlebar_subsurface = nullptr;
    if (window.titlebar_surface) {
        wl_surface_set_user_data(window.titlebar_surface, nullptr);
        wl_surface_destroy(window.titlebar_surface);
    }
    window.titlebar_surface = nullptr;
}

void refresh_appearance(window& window) {
    const std::string treatment = treatment_name(read_surface_treatment());
    if (window.has_prairie_titlebar && window.surface_treatment != treatment)
        redraw_titlebar(window);
}

void redraw_titlebar(window& window) {
    if (!window.has_prairie_titlebar || window.content_width < 1 || window.content_height < 1)
        return;
    const auto geometry = frame(window);
    const int width = geometry.outer.width;
    const int height = geometry.outer.height;
    // Avoid unbounded allocations from invalid compositor configurations.
    if (width > 4096 || height > 4096) return;
    const size_t size = static_cast<size_t>(width) * height * sizeof(uint32_t);
    const int fd = syscall(SYS_memfd_create, "luma-window-frame", MFD_CLOEXEC);
    if (fd < 0) return;
    if (ftruncate(fd, size) != 0) { close(fd); return; }
    void *data = mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (data == MAP_FAILED) { close(fd); return; }
    if (window.titlebar_buffer) wl_buffer_destroy(window.titlebar_buffer);
    if (window.titlebar_data && window.titlebar_data_size)
        munmap(window.titlebar_data, window.titlebar_data_size);
    window.titlebar_data = data;
    window.titlebar_data_size = size;
    const SurfaceTreatment treatment = read_surface_treatment();
    window.surface_treatment = treatment_name(treatment);
    const bool dark = treatment == SurfaceTreatment::Dark;
    window.dark_appearance = dark;
    auto* pixels = static_cast<uint32_t*>(data);
    for (int y = 0; y < height; ++y) {
        const bool interior = y >= geometry.content.y + LUMA_FRAME_ISLAND_RADIUS &&
            y < geometry.content.y + geometry.content.height - LUMA_FRAME_ISLAND_RADIUS;
        const int left = interior ? geometry.content.x + LUMA_FRAME_ISLAND_RADIUS : width;
        const int right = interior ? geometry.content.x + geometry.content.width - LUMA_FRAME_ISLAND_RADIUS : width;
        for (int x = 0; x < left; ++x)
            pixels[y * width + x] = scale_premultiplied(
                luma_frame_pixel(geometry, x, y, dark, window.fullscreen),
                frame_opacity(treatment));
        for (int x = right; x < width; ++x)
            pixels[y * width + x] = scale_premultiplied(
                luma_frame_pixel(geometry, x, y, dark, window.fullscreen),
                frame_opacity(treatment));
    }
    const auto back = luma_frame_back(geometry);
    for (int y = 0; y < kTitlebarHeight; ++y)
        for (int x = std::max(0, back.x - 16); x < width; ++x) {
            composite_premultiplied(pixels[y * width + x], scale_premultiplied(
                luma_frame_controls_pixel(geometry, x, y, dark), control_opacity(treatment)));
            composite_premultiplied(pixels[y * width + x], scale_premultiplied(
                luma_frame_control_surface(back, x, y, dark), control_opacity(treatment)));
        }
    for (int index = 0; index < 3; ++index)
        draw_control(window, control_left(window, index), index);
    draw_back(window);
    draw_app_icon(window);
    draw_title(window);
    wl_shm_pool* pool = wl_shm_create_pool(window.display->shm, fd, size);
    close(fd);
    window.titlebar_buffer = wl_shm_pool_create_buffer(pool, 0, width, height,
        width * sizeof(uint32_t), WL_SHM_FORMAT_ARGB8888);
    wl_shm_pool_destroy(pool);
    // Only the frame takes input. The client remains the real input surface.
    wl_region* region = wl_compositor_create_region(window.display->compositor);
    wl_region_add(region, 0, 0, width, height);
    wl_region_subtract(region, geometry.content.x, geometry.content.y,
                        geometry.content.width, geometry.content.height);
    wl_surface_set_input_region(window.titlebar_surface, region);
    wl_region_destroy(region);
    wl_surface_attach(window.titlebar_surface, window.titlebar_buffer, 0, 0);
    wl_surface_damage(window.titlebar_surface, 0, 0, width, height);
    wl_surface_commit(window.titlebar_surface);
}

}  // namespace prairie
