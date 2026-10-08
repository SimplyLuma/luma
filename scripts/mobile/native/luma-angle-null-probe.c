// SPDX-License-Identifier: Apache-2.0
// Prove that an exact Android ANGLE payload supports its platform-independent
// null renderer without loading Android's meta-EGL selector or a GPU driver.

#include <EGL/egl.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

typedef EGLDisplay (*get_display_fn)(EGLNativeDisplayType);
typedef EGLDisplay (*get_platform_display_fn)(EGLenum, void *, const EGLAttrib *);
typedef EGLDisplay (*get_platform_display_ext_fn)(EGLenum, void *, const EGLint *);
typedef EGLBoolean (*initialize_fn)(EGLDisplay, EGLint *, EGLint *);
typedef EGLBoolean (*choose_config_fn)(EGLDisplay, const EGLint *, EGLConfig *,
                                       EGLint, EGLint *);
typedef const char *(*query_string_fn)(EGLDisplay, EGLint);
typedef EGLBoolean (*terminate_fn)(EGLDisplay);
typedef EGLint (*get_error_fn)(void);

static void *required_symbol(void *handle, const char *name) {
    void *symbol = dlsym(handle, name);
    if (symbol == NULL) {
        fprintf(stderr, "missing_symbol=%s error=%s\n", name, dlerror());
        exit(2);
    }
    return symbol;
}

int main(void) {
    if (setenv("ANGLE_DEFAULT_PLATFORM", "null", 1) != 0) {
        perror("setenv");
        return 2;
    }

    void *handle = dlopen("/vendor/lib64/egl/libEGL_angle.so", RTLD_NOW | RTLD_LOCAL);
    if (handle == NULL) {
        fprintf(stderr, "dlopen_error=%s\n", dlerror());
        return 2;
    }

    get_display_fn get_display = (get_display_fn)required_symbol(handle, "eglGetDisplay");
    get_platform_display_fn get_platform_display =
        (get_platform_display_fn)dlsym(handle, "eglGetPlatformDisplay");
    get_platform_display_ext_fn get_platform_display_ext =
        (get_platform_display_ext_fn)dlsym(handle, "eglGetPlatformDisplayEXT");
    initialize_fn initialize = (initialize_fn)required_symbol(handle, "eglInitialize");
    choose_config_fn choose_config = (choose_config_fn)required_symbol(handle, "eglChooseConfig");
    query_string_fn query_string = (query_string_fn)required_symbol(handle, "eglQueryString");
    terminate_fn terminate = (terminate_fn)required_symbol(handle, "eglTerminate");
    get_error_fn get_error = (get_error_fn)required_symbol(handle, "eglGetError");

    // Request ANGLE's standardized null platform explicitly. Android's
    // meta-EGL loader normally supplies an explicit Vulkan attribute, which
    // takes precedence over ANGLE_DEFAULT_PLATFORM.
    static const EGLAttrib platform_attributes[] = {
        0x3203,  // EGL_PLATFORM_ANGLE_TYPE_ANGLE
        0x33AE,  // EGL_PLATFORM_ANGLE_TYPE_NULL_ANGLE
        EGL_NONE,
    };
    static const EGLint platform_attributes_ext[] = {
        0x3203,  // EGL_PLATFORM_ANGLE_TYPE_ANGLE
        0x3450,  // EGL_PLATFORM_ANGLE_TYPE_VULKAN_ANGLE
        0x3209,  // EGL_PLATFORM_ANGLE_DEVICE_TYPE_ANGLE
        0x345E,  // EGL_PLATFORM_ANGLE_DEVICE_TYPE_NULL_ANGLE
        EGL_NONE,
    };
    EGLDisplay display = get_platform_display_ext != NULL
                             ? get_platform_display_ext(0x3202, EGL_DEFAULT_DISPLAY,
                                                        platform_attributes_ext)
                         : get_platform_display != NULL
                             ? get_platform_display(0x3202, EGL_DEFAULT_DISPLAY,
                                                    platform_attributes)
                             : get_display(EGL_DEFAULT_DISPLAY);
    if (display == EGL_NO_DISPLAY) {
        fprintf(stderr, "get_display_error=0x%x\n", get_error());
        return 3;
    }

    EGLint major = 0;
    EGLint minor = 0;
    if (initialize(display, &major, &minor) != EGL_TRUE) {
        fprintf(stderr, "initialize_error=0x%x\n", get_error());
        return 4;
    }

    const EGLint attributes[] = {
        EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
        EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
        EGL_RED_SIZE, 8,
        EGL_GREEN_SIZE, 8,
        EGL_BLUE_SIZE, 8,
        EGL_ALPHA_SIZE, 8,
        EGL_NONE,
    };
    EGLConfig config = NULL;
    EGLint count = 0;
    if (choose_config(display, attributes, &config, 1, &count) != EGL_TRUE || count < 1) {
        fprintf(stderr, "choose_config_error=0x%x count=%d\n", get_error(), count);
        terminate(display);
        return 5;
    }

    printf("ANGLE_NULL_DIRECT_GATE=true\n");
    printf("egl_version=%d.%d\n", major, minor);
    printf("egl_vendor=%s\n", query_string(display, EGL_VENDOR));
    printf("matching_configs=%d\n", count);
    terminate(display);
    dlclose(handle);
    return 0;
}
