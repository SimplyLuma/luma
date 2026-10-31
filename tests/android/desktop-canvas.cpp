// SPDX-License-Identifier: Apache-2.0
#include "luma-desktop-canvas.h"
#include "luma-app-settings-channel.h"
#include <cassert>
#include <climits>
#include <iostream>

int main() {
    using luma::desktop_canvas;
    auto size = desktop_canvas({{0, 0, 1440, 2560}, {1440, 1054, 3440, 1440},
                               {4880, 1398, 1920, 1200}});
    assert(size.width == 6800 && size.height == 2598);
    // Already-logical fractional-scale and rotated output sizes, with an
    // output above/left of the primary. Do not rotate or scale them twice.
    size = desktop_canvas({{-1920, -200, 1920, 1080}, {0, 0, 1536, 864},
                           {1536, -640, 1080, 1920}});
    assert(size.width == 4536 && size.height == 1920);
    size = desktop_canvas({{0, 0, 0, 100}, {0, 0, 1920, 1080}});
    assert(size.width == 1920 && size.height == 1080);
    assert(desktop_canvas({}).width == 0);
    assert(desktop_canvas({{INT_MIN, 0, 1920, 1080}, {INT_MAX, 0, 1920, 1080}}).width == 0);
    using namespace luma::app_settings;
    Request request{magic, version, 0, "org.example.Game"};
    assert(valid(request));
    request.reserved = 1; assert(!valid(request));
    request.reserved = 0; request.version = 2; assert(!valid(request));
    request.version = version;
    memset(request.package, 'a', sizeof(request.package)); assert(!valid(request));
    assert(!valid_package("org.example.Game;command", 24));
    assert(!valid_package("../org.example.Game", 19));
    std::cout << "PASS logical monitor envelope, negative origins, overflow and settings packet validation\n";
}
