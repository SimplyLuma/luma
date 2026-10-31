/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <algorithm>
#include <cstdint>
#include <vector>

namespace luma {
struct LogicalOutput { int x, y, width, height; };
struct CanvasSize { int width, height; };
// xdg-output supplies logical sizes after rotation and fractional scaling.
// A desktop window may span monitors, including monitors left/above (0, 0).
inline CanvasSize desktop_canvas(const std::vector<LogicalOutput>& outputs) {
    bool found = false;
    int64_t left = 0, top = 0, right = 0, bottom = 0;
    for (const auto& output : outputs) {
        if (output.width <= 0 || output.height <= 0) continue;
        const int64_t x = output.x, y = output.y;
        if (!found) { left = x; top = y; right = x; bottom = y; found = true; }
        left = std::min(left, x); top = std::min(top, y);
        right = std::max(right, x + output.width);
        bottom = std::max(bottom, y + output.height);
    }
    if (!found || right - left > 16384 || bottom - top > 16384) return {0, 0};
    return {static_cast<int>(right - left), static_cast<int>(bottom - top)};
}
}
