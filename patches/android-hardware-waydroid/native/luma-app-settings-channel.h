/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <cstdint>
#include <cstring>

namespace luma::app_settings {
constexpr uint32_t magic = 0x4c415053; // LAPS
constexpr uint16_t version = 1;
constexpr char socket_name[] = "luma-android-app-settings-v1";
struct Request {
    uint32_t magic;
    uint16_t version;
    uint16_t reserved;
    char package[256];
};
static_assert(sizeof(Request) == 264);
inline bool valid_package(const char* name, size_t length) {
    if (length < 3 || length > 255) return false;
    bool start = true, dotted = false;
    for (size_t i = 0; i < length; ++i) {
        const unsigned char c = name[i];
        const bool letter = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z');
        if (c == '.') {
            if (start) return false;
            start = true; dotted = true;
        } else {
            if (!letter && (start || !((c >= '0' && c <= '9') || c == '_')))
                return false;
            start = false;
        }
    }
    return dotted && !start;
}
inline bool valid(const Request& request) {
    const size_t length = strnlen(request.package, sizeof(request.package));
    return request.magic == magic && request.version == version &&
        request.reserved == 0 && valid_package(request.package, length);
}
}
