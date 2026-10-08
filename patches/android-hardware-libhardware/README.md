# Emulator software allocator candidate

Owner: Android vendor graphics HAL, scoped to the unaccelerated emulator profile.
This is a Class C/D candidate, **not release accepted**. No physical-device claim.

The combined patch applies to LineageOS `android_hardware_libhardware`
`0eb202d7ebd7d2410eb2f62c908c0341964a4829` (Apache-2.0, AOSP attribution retained).
It incorporates the existing Waydroid gralloc baseline from
`android_vendor_waydroid` `1b95b85221f4faaa357932fa5e93eacb7430f636`:

- `0003`: same-process buffer registration guard, Paulo Sergio Travaglia;
- `0004`: ashmem allocation instead of the absent framebuffer, Erfan Abdi;
- `0005`: additional pixel formats, Alessandro Astone.

Exact upstream patch files, author metadata, and their original descriptions
are retained under `upstream/`. They are already incorporated in the combined
patch and must not be applied twice. Upstream source:
https://github.com/waydroid/android_vendor_waydroid/tree/1b95b85221f4faaa357932fa5e93eacb7430f636/waydroid-patches/base-patches-33/hardware/libhardware

Luma adds bounded YV12/flexible 4:2:0 planar allocation and `lock_ycbcr`, the
required handle metadata, and a variable-page-size-compatible page helper.
The initial vanilla-Lineage candidate omitted Waydroid's framebuffer behavior:
byte-buffer decoding passed, but visual presentation failed. That candidate is
rejected. Full display checks are required in addition to decoder checks.

Build with `scripts/android/build-software-gralloc.py`. It accepts local Git
repositories, exports exact commits (ignoring dirty files), uses NDK
28.2.13676358/API33, and links only the pinned image's libcutils plus Android
liblog/libc. It records source/patch/module hashes and carries AOSP, NDK/LLVM,
and Waydroid notices with the artifact. Source repositories and NDK are build
inputs, never downloaded at app launch or committed as binaries.

`prepare-software-graphics-images.py` admits only the exact coherent Android13
arm64_only raw pair and source-built module hash. It grows a staged vendor image
by 16 MiB, replaces only the allocator, preserves its exact SELinux extended
attribute, and checks the extracted module and ext filesystem. System remains
unchanged. The existing image-pair deployer retains a rollback pair. The explicitly opted-in experimental
configuration refuses to enable Codec2 unless this allocator is present; the
static VINTF fragment accurately disables the declared but absent OMX service.
No security enforcement, app permissions, native audio, or userdata is changed.

Required before release: independent review of cross-process handle validation,
full clean image composition, cold boot/rollback, RGB and YUV surface rendering,
malformed handle tests in an executable test image, long playback/seek/audio sync,
and ordinary Android/Linux application regressions. The test-only native probe
must run from an executable test image, not by weakening userdata mount policy.

The successor module 2106130c… initially failed TikTok presentation with Waydroid's
RGB-only default multiwindow SHM copier. With the same allocator and the upstream
SurfaceFlinger composition mode (`persist.waydroid.multi_windows=false` and
`persist.waydroid.use_subsurface=false`), TikTok video plays on the customized
native VM. The user confirmed flawless playback, estimating 30–60 fps; this is
subjective feedback, not an instrumented frame-rate measurement.

The profile uses RGB-composited app windows and snapshots inactive Android
windows instead of continuously composing their individual subsurfaces. Physical
graphics paths are unchanged. This is a tested emulator profile, with the release
review/negative-test/clean-image gates above still open.

Reproduce through the normal emulator exporter with `--with-compatibility`, the
pinned `--android-image-cache`, and `--software-video-artifact` pointing to the
output of `build-software-gralloc.py`. The exporter verifies the module/source
patch pins and includes its license notices. The factory installer derives the
vendor image with the existing offline preparation script, deploys the coherent
pair transactionally, and applies the complete composition/Codec2 profile. The
profile remains explicit until the remaining release gates are closed; launching
an app never downloads, patches, or rebuilds components.
