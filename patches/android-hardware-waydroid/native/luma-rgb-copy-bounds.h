// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <cstdint>
#include <cstddef>

namespace luma {
// Validate the CPU copy against the allocation, never against a destination
// display rectangle. Reject mixed/stale metadata rather than clipping it.
inline bool valid_rgb_copy(uint64_t width, uint64_t height, uint64_t stride,
                           uint64_t allocation_height, uint64_t allocation_size,
                           uint64_t offset, uint64_t fd_size,
                           uint64_t destination_size) {
    constexpr uint64_t limit = 16384;
    if (!width || !height || !stride || !allocation_height || width > stride ||
        width > limit || height > limit || stride > limit || allocation_height > limit ||
        height > allocation_height || offset > allocation_size || allocation_size > fd_size)
        return false;
    const uint64_t source_end = ((height - 1) * stride + width) * 4;
    const uint64_t destination_end = width * height * 4;
    return source_end <= allocation_size - offset &&
           source_end <= fd_size - offset && destination_end <= destination_size;
}
inline bool copy_rgb(const uint32_t* source, uint32_t* destination,
                     uint64_t width, uint64_t height, uint64_t stride,
                     uint64_t allocation_height, uint64_t allocation_size,
                     uint64_t offset, uint64_t fd_size, uint64_t destination_size) {
    if (!source || !destination || !valid_rgb_copy(width, height, stride,
            allocation_height, allocation_size, offset, fd_size, destination_size)) return false;
    for (uint64_t i = 0; i < height; ++i) {
        for (uint64_t x = 0; x < width; ++x) {
            const uint32_t c = source[size_t(i * stride + x)];
            destination[size_t(i * width + x)] =
                (c & 0xFF00FF00) | ((c & 0xFF0000) >> 16) | ((c & 0xFF) << 16);
        }
    }
    return true;
}
} // namespace luma
