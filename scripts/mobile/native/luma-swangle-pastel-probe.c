// SPDX-License-Identifier: Apache-2.0
// Prove that QREL's exact ANGLE payload can initialize through QREL's exact
// CPU-only Vulkan Pastel (SwiftShader) driver without exposing a physical GPU.

#include <EGL/egl.h>
#include <GLES2/gl2.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef EGLDisplay (*get_display_fn)(EGLNativeDisplayType);
typedef EGLDisplay (*get_platform_display_ext_fn)(EGLenum, void *, const EGLint *);
typedef EGLBoolean (*initialize_fn)(EGLDisplay, EGLint *, EGLint *);
typedef EGLBoolean (*choose_config_fn)(EGLDisplay, const EGLint *, EGLConfig *,
                                       EGLint, EGLint *);
typedef EGLSurface (*create_pbuffer_surface_fn)(EGLDisplay, EGLConfig, const EGLint *);
typedef EGLContext (*create_context_fn)(EGLDisplay, EGLConfig, EGLContext, const EGLint *);
typedef EGLBoolean (*make_current_fn)(EGLDisplay, EGLSurface, EGLSurface, EGLContext);
typedef EGLBoolean (*destroy_surface_fn)(EGLDisplay, EGLSurface);
typedef EGLBoolean (*destroy_context_fn)(EGLDisplay, EGLContext);
typedef const char *(*query_string_fn)(EGLDisplay, EGLint);
typedef EGLBoolean (*terminate_fn)(EGLDisplay);
typedef EGLint (*get_error_fn)(void);
typedef const GLubyte *(*gl_get_string_fn)(GLenum);
typedef void (*gl_clear_color_fn)(GLfloat, GLfloat, GLfloat, GLfloat);
typedef void (*gl_clear_fn)(GLbitfield);
typedef void (*gl_read_pixels_fn)(GLint, GLint, GLsizei, GLsizei, GLenum, GLenum, void *);

static void *required_symbol(void *handle, const char *name) {
    void *symbol = dlsym(handle, name);
    if (symbol == NULL) {
        fprintf(stderr, "missing_symbol=%s error=%s\n", name, dlerror());
        exit(2);
    }
    return symbol;
}

int main(void) {
    if (setenv("ANGLE_DEFAULT_PLATFORM", "vulkan", 1) != 0) {
        perror("setenv");
        return 2;
    }

    void *handle = dlopen("/vendor/lib64/egl/libEGL_angle.so", RTLD_NOW | RTLD_LOCAL);
    if (handle == NULL) {
        fprintf(stderr, "dlopen_error=%s\n", dlerror());
        return 2;
    }

    get_display_fn get_display = (get_display_fn)required_symbol(handle, "eglGetDisplay");
    get_platform_display_ext_fn get_platform_display_ext =
        (get_platform_display_ext_fn)dlsym(handle, "eglGetPlatformDisplayEXT");
    initialize_fn initialize = (initialize_fn)required_symbol(handle, "eglInitialize");
    choose_config_fn choose_config = (choose_config_fn)required_symbol(handle, "eglChooseConfig");
    create_pbuffer_surface_fn create_pbuffer_surface =
        (create_pbuffer_surface_fn)required_symbol(handle, "eglCreatePbufferSurface");
    create_context_fn create_context =
        (create_context_fn)required_symbol(handle, "eglCreateContext");
    make_current_fn make_current =
        (make_current_fn)required_symbol(handle, "eglMakeCurrent");
    destroy_surface_fn destroy_surface =
        (destroy_surface_fn)required_symbol(handle, "eglDestroySurface");
    destroy_context_fn destroy_context =
        (destroy_context_fn)required_symbol(handle, "eglDestroyContext");
    query_string_fn query_string = (query_string_fn)required_symbol(handle, "eglQueryString");
    terminate_fn terminate = (terminate_fn)required_symbol(handle, "eglTerminate");
    get_error_fn get_error = (get_error_fn)required_symbol(handle, "eglGetError");

    // Ask ANGLE for its Vulkan backend. Android's Vulkan loader then resolves
    // ro.hardware.vulkan=pastel to the verified CPU-only QREL driver.
    static const EGLint platform_attributes[] = {
        0x3203,  // EGL_PLATFORM_ANGLE_TYPE_ANGLE
        0x3450,  // EGL_PLATFORM_ANGLE_TYPE_VULKAN_ANGLE
        EGL_NONE,
    };
    EGLDisplay display = get_platform_display_ext != NULL
                             ? get_platform_display_ext(0x3202, EGL_DEFAULT_DISPLAY,
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

    const EGLint pbuffer_attributes[] = {
        EGL_WIDTH, 4,
        EGL_HEIGHT, 4,
        EGL_NONE,
    };
    EGLSurface surface = create_pbuffer_surface(display, config, pbuffer_attributes);
    if (surface == EGL_NO_SURFACE) {
        fprintf(stderr, "create_surface_error=0x%x\n", get_error());
        terminate(display);
        return 6;
    }
    const EGLint context_attributes[] = {
        EGL_CONTEXT_CLIENT_VERSION, 2,
        EGL_NONE,
    };
    EGLContext context = create_context(display, config, EGL_NO_CONTEXT, context_attributes);
    if (context == EGL_NO_CONTEXT) {
        fprintf(stderr, "create_context_error=0x%x\n", get_error());
        destroy_surface(display, surface);
        terminate(display);
        return 7;
    }
    if (make_current(display, surface, surface, context) != EGL_TRUE) {
        fprintf(stderr, "make_current_error=0x%x\n", get_error());
        destroy_context(display, context);
        destroy_surface(display, surface);
        terminate(display);
        return 8;
    }

    void *gles = dlopen("/vendor/lib64/egl/libGLESv2_angle.so", RTLD_NOW | RTLD_LOCAL);
    if (gles == NULL) {
        fprintf(stderr, "gles_dlopen_error=%s\n", dlerror());
        return 9;
    }
    gl_get_string_fn gl_get_string = (gl_get_string_fn)required_symbol(gles, "glGetString");
    gl_clear_color_fn gl_clear_color = (gl_clear_color_fn)required_symbol(gles, "glClearColor");
    gl_clear_fn gl_clear = (gl_clear_fn)required_symbol(gles, "glClear");
    gl_read_pixels_fn gl_read_pixels = (gl_read_pixels_fn)required_symbol(gles, "glReadPixels");
    const char *renderer = (const char *)gl_get_string(GL_RENDERER);
    const char *gl_vendor = (const char *)gl_get_string(GL_VENDOR);
    const char *gl_version = (const char *)gl_get_string(GL_VERSION);
    if (renderer == NULL || strstr(renderer, "SwiftShader") == NULL) {
        fprintf(stderr, "unexpected_renderer=%s\n", renderer == NULL ? "null" : renderer);
        return 10;
    }
    gl_clear_color(0.25f, 0.5f, 0.75f, 1.0f);
    gl_clear(GL_COLOR_BUFFER_BIT);
    unsigned char pixel[4] = {0, 0, 0, 0};
    gl_read_pixels(0, 0, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, pixel);

    printf("SWANGLE_PASTEL_DIRECT_GATE=true\n");
    printf("egl_version=%d.%d\n", major, minor);
    printf("egl_vendor=%s\n", query_string(display, EGL_VENDOR));
    printf("egl_version_string=%s\n", query_string(display, EGL_VERSION));
    printf("matching_configs=%d\n", count);
    printf("gl_vendor=%s\n", gl_vendor);
    printf("gl_renderer=%s\n", renderer);
    printf("gl_version=%s\n", gl_version);
    printf("readback_rgba=%u,%u,%u,%u\n", pixel[0], pixel[1], pixel[2], pixel[3]);
    make_current(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    dlclose(gles);
    destroy_context(display, context);
    destroy_surface(display, surface);
    terminate(display);
    dlclose(handle);
    return 0;
}
