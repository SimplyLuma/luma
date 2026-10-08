// SPDX-License-Identifier: Apache-2.0
// Run only inside the emulator Android image. Exercises the actual HAL, including
// rejection of malformed dimensions and handle metadata; no app/user data.
#include <log/log.h>
#include <initializer_list>
#include <climits>
#include <hardware/gralloc.h>
#include "gralloc_priv.h"
#include <dlfcn.h>
#include <cstdio>
#include <cstring>
#include <cerrno>

#define REQUIRE(expr) do { if (!(expr)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #expr); return 1; } } while (0)
int main(int argc, char** argv) {
    REQUIRE(argc <= 2);
    // Optional absolute candidate path permits real-HAL admission before image
    // replacement. No library path is persisted or used by production code.
    const char* path = argc == 2 ? argv[1] : "/vendor/lib64/hw/gralloc.default.so";
    REQUIRE(path[0] == '/');
    void* library = dlopen(path, RTLD_NOW);
    if (!library) fprintf(stderr, "dlopen: %s\n", dlerror());
    REQUIRE(library);
    auto module = reinterpret_cast<gralloc_module_t*>(dlsym(library, HAL_MODULE_INFO_SYM_AS_STR));
    REQUIRE(module && module->lock_ycbcr);
    alloc_device_t* device = nullptr;
    REQUIRE(gralloc_open(&module->common, &device) == 0);
    for (int format : {HAL_PIXEL_FORMAT_RGBA_8888, HAL_PIXEL_FORMAT_YV12, HAL_PIXEL_FORMAT_YCbCr_420_888}) {
        buffer_handle_t buffer = nullptr;
        int stride = 0;
        int usage = GRALLOC_USAGE_SW_READ_OFTEN | GRALLOC_USAGE_SW_WRITE_OFTEN;
        REQUIRE(device->alloc(device, 162, 98, format, usage, &buffer, &stride) == 0);
        if (format == HAL_PIXEL_FORMAT_RGBA_8888) {
            void* pixels = nullptr;
            REQUIRE(module->lock(module, buffer, usage, 0, 0, 162, 98, &pixels) == 0 && pixels);
            memset(pixels, 0x55, stride * 98 * 4);
        } else {
            android_ycbcr yuv{};
            REQUIRE(module->lock_ycbcr(module, buffer, usage, 0, 0, 162, 98, &yuv) == 0);
            REQUIRE(yuv.y && yuv.cb && yuv.cr && yuv.ystride == 176 && yuv.cstride == 96 && yuv.chroma_step == 1);
            REQUIRE((yuv.cr < yuv.cb) == (format == HAL_PIXEL_FORMAT_YV12));
            memset(yuv.y, 128, yuv.ystride * 98);
            memset(yuv.cb, 128, yuv.cstride * 49);
            memset(yuv.cr, 128, yuv.cstride * 49);
            private_handle_t malformed = *reinterpret_cast<const private_handle_t*>(buffer);
            malformed.size = 16;
            REQUIRE(module->lock_ycbcr(module, &malformed, usage, 0, 0, 162, 98, &yuv) == -EINVAL);
            malformed.size = INT_MAX;
            malformed.stride = 16385;
            REQUIRE(module->lock_ycbcr(module, &malformed, usage, 0, 0, 162, 98, &yuv) == -EINVAL);
            REQUIRE(module->lock_ycbcr(module, buffer, usage, 0, 0, 162, 98, nullptr) == -EINVAL);
        }
        REQUIRE(module->unlock(module, buffer) == 0);
        REQUIRE(device->free(device, buffer) == 0);
    }
    for (int size : {-1, 0, 16385}) {
        buffer_handle_t buffer = nullptr; int stride = 0;
        REQUIRE(device->alloc(device, size, 98, HAL_PIXEL_FORMAT_YV12, GRALLOC_USAGE_SW_READ_OFTEN, &buffer, &stride) == -EINVAL);
    }
    buffer_handle_t buffer = nullptr; int stride = 0;
    REQUIRE(device->alloc(device, 161, 98, HAL_PIXEL_FORMAT_YV12, GRALLOC_USAGE_SW_READ_OFTEN, &buffer, &stride) == -EINVAL);
    REQUIRE(gralloc_close(device) == 0);
    puts("PASS: RGB, YV12, flexible YUV allocation/planes and invalid dimensions/handles");
}
