# macOS native VirGL runtime

The Apple Silicon runtime uses HVF for the CPU and VirGL backed by Apple's
accelerated desktop OpenGL context. Its headless display feeds the existing
private VNC viewer. The GLES/ANGLE backend remains available for diagnostics;
it is not the selected desktop backend because its desktop GL feature level
causes missing shell textures. No global Homebrew installation is replaced.

## Corresponding sources

QEMU source: `fb806cdfca39542e59b2eae58c6fd6662afd3787` from
<https://gitlab.com/milesbuckton/qemu>, archive SHA256
`8264c222f6a6a861e92b0944bbf4367e1083f7071559fc60f5d038985554da8a`.
Apply `qemu-macos-headless-cgl.patch` with `patch -p1` in that source directory.
The patch adds shared native CGL contexts and desktop shader adaptation while
retaining the existing EGL path on other hosts. It does not modify guest images.

Runtime dependencies used for this build:

- libepoxy `d1f952c4565c389d82fd1657153e84e050f35b27`,
  <https://github.com/anholt/libepoxy>. Build with EGL dispatch enabled.
- VirGL `451505ad0aa449290f2c0672da6877d57ee770e5`,
  <https://github.com/utmapp/virglrenderer>.
- ANGLE `ed78ab6e1a37f4f11583a0bd038f22ec91f3ff10`,
  <https://github.com/utmapp/WebKit/tree/ed78ab6e1a37f4f11583a0bd038f22ec91f3ff10/Source/ThirdParty/ANGLE>.
- Vulkan Loader `6df86907be582927507637f3f6003ea58bc34517`,
  <https://github.com/osy/Vulkan-Loader>.

The last three dependency binaries came from the official UTM 5.0.8 arm64
bundle, whose DMG SHA256 is
`7b2d7b2e2a02b3de1757ef19a8f36573e6d9cceae8c8cca0abcbcaa4a852019a`.
EGL/GLES remain bundled for libepoxy dispatch initialization; native desktop
rendering uses CGL. Keep their upstream licenses and exact source references in
`ThirdPartyNotices` and `runtime.json`.

## Build recipe

Use an isolated directory on macOS with Xcode command-line tools and Homebrew
GLib, Pixman, libslirp, zstd, libpng, jpeg-turbo, dtc, pkg-config and Ninja.
Use no-space dependency aliases because QEMU's configure splits extra flags.
The qualified build used Python 3.14 and an isolated venv with setuptools
84.0.0, wheel 0.48.0, packaging 26.3 and Meson 1.12.0. Provision the pinned
`subprojects/keycodemapdb` revision from QEMU's wrap before disabling downloads.

Set `PKG_CONFIG_PATH` to the isolated epoxy/virgl pkg-config directory followed
by the Homebrew pkg-config directory. Ensure the virgl header include directory
is present in `C_INCLUDE_PATH`. Rewrite dependency install names and `.pc`
prefixes to the isolated dependency directory before linking. With `SRC`,
`PREFIX`, `DEPS`, `ANGLE` and `BREW` denoting those local directories:

```sh
"$SRC/configure" --python="$VENV/bin/python3" --prefix="$PREFIX" \
  --target-list=aarch64-softmmu --disable-download --disable-tests \
  --disable-docs --disable-werror --disable-gtk --disable-sdl \
  --enable-cocoa --enable-opengl --enable-virglrenderer --enable-slirp \
  --enable-fdt=system --disable-pvg \
  --extra-ldflags="-framework OpenGL -L$BREW/lib -L$BREW/opt/dtc/lib -Wl,-headerpad_max_install_names -Wl,-rpath,$DEPS/lib -Wl,-rpath,$ANGLE" \
  --extra-cflags="-Wno-deprecated-declarations -I$BREW/include -I$BREW/opt/dtc/include"
ninja -j3
ninja install
```

The qualified deployment target was macOS 27.0, matching the application's
existing minimum. Run `stage-macos-gpu.py --backend apple-opengl` with the built
QEMU, EGL/GLES binaries, notices and pinned provenance to relocate every
non-system dependency into a new private prefix. Pass that prefix to
`build-macos.py --gpu-runtime`. Sign the helper with Hypervisor and JIT
entitlements, then the nested libraries/frameworks and application. Notarize
and staple the final distributable through the authorized release credentials.
Ship the corresponding QEMU archive, this patch and this recipe with the release.

Qualification requires an actual prepared ARM guest boot, accelerated VirGL
renderer evidence, a correctly rendered dock and normal GTK application, native
viewer input, and a clean shutdown. A successful diskless QMP probe alone is not
sufficient. Use a private writable overlay, never the user's workspace.
