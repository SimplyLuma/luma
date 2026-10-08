/*
 * Copyright (C) 2026 Project Luma
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include <cstdint>
#include <string>
#include "luma-window-frame.h"

struct window;

namespace prairie {

constexpr int kTitlebarHeight = LUMA_FRAME_HEADER_HEIGHT;
constexpr int kControlDiameter = LUMA_FRAME_CONTROL_SIZE;
constexpr int kControlAllocation = LUMA_FRAME_CONTROL_SIZE;
constexpr int kControlSpacing = 0;
constexpr int kControlInset = 12;
constexpr int kControlHitSize = LUMA_FRAME_CONTROL_SIZE;
constexpr int kWindowRadius = LUMA_FRAME_WINDOW_RADIUS;
constexpr int kBackButtonSize = LUMA_FRAME_CONTROL_SIZE + 2 * LUMA_FRAME_CONTROL_PADDING;
constexpr int kBackButtonInset = 7;

enum class TitlebarTarget {
    Content,
    Drag,
    Close,
    Minimize,
    Maximize,
    Back,
};

LumaWindowFrame frame(const window& window);
bool desktop_chrome_enabled();
TitlebarTarget hit_test(const window& window, int x, int y);
bool create_titlebar(window& window);
void destroy_titlebar(window& window);
void redraw_titlebar(window& window);
void refresh_appearance(window& window);
void redraw_shadow(window& window);

}  // namespace prairie
