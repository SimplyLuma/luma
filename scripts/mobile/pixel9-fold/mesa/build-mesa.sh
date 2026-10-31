#!/usr/bin/env bash
# Build Mesa (panfrost gallium, EGL/GBM/GLES, Wayland) for the Pixel 9 Pro Fold's
# Mali-G715 (v11) as a private prefix /var/lib/luma-fold/mesa. Fedora 44 x86_64
# build root + aarch64 sysroot are made with dnf --installroot on the HDD; the
# build runs at native speed in podman --rootfs, cross-compiling with clang.
# Run as root: R=<work dir on a large disk> bash build-mesa.sh
set -euo pipefail
MESA_COMMIT=${MESA_COMMIT:-e282ea8c6deffedc9f18243c8814f06fe78bc23a}   # staging/26.3, 26.3.0-rc1
HERE=$(cd "$(dirname "$0")" && pwd)
: "${R:?work directory (build roots, output)}"
W=${W:-/dev/shm/pixel9-fold-mesa}
OUT=${OUT:-$R/out-${MESA_COMMIT:0:12}}
mkdir -p "$R"; setfacl -b -k "$R" 2>/dev/null || true
DNF="dnf -y -q --use-host-config --releasever=44 --setopt=install_weak_deps=False --nodocs"

if [ ! -e "$R/x86/.done" ]; then
  $DNF --installroot="$R/x86" install bash coreutils findutils grep sed gawk diffutils tar zstd file git-core \
    gcc gcc-c++ clang lld llvm llvm-devel clang-devel libclc libclc-devel spirv-llvm-translator-devel \
    spirv-tools-devel spirv-headers-devel glslang meson ninja-build pkgconf-pkg-config bison flex \
    python3-mako python3-pyyaml python3-packaging python3-ply \
    wayland-devel libdrm-devel zlib-ng-compat-devel expat-devel libzstd-devel elfutils-libelf-devel binutils
  touch "$R/x86/.done"
fi
if [ ! -e "$R/a64/.done" ]; then
  # filesystem first: it owns the /lib64 -> usr/lib64 symlink that libc.so's
  # linker script needs, and fails to install once anything created /lib64
  $DNF --installroot="$R/a64" --forcearch=aarch64 --setopt=tsflags=noscripts install filesystem
  $DNF --installroot="$R/a64" --forcearch=aarch64 --setopt=tsflags=noscripts install \
    glibc-devel gcc libstdc++-devel libdrm-devel wayland-devel wayland-protocols-devel libglvnd-devel expat-devel \
    zlib-ng-compat-devel libzstd-devel elfutils-libelf-devel
  touch "$R/a64/.done"
fi

mkdir -p "$W"
if [ "$(git -C "$W/src" rev-parse HEAD 2>/dev/null)" != "$MESA_COMMIT" ]; then
  rm -rf "$W/src"; git init -q "$W/src"
  git -C "$W/src" fetch -q --depth 1 https://gitlab.freedesktop.org/mesa/mesa.git "$MESA_COMMIT"
  git -C "$W/src" checkout -q FETCH_HEAD
fi
rm -rf "$W/native" "$W/native-inst" "$W/cross" "$W/dest"
cp "$HERE/inside.sh" "$HERE/aarch64.cross" "$W/"
podman run --rm --network none --security-opt label=disable -v "$W:/w" -v "$R/a64:/sysroot:ro" -e JOBS="${JOBS:-16}" \
  --rootfs "$R/x86" /bin/bash /w/inside.sh
rm -rf "$OUT"; mkdir -p "$OUT"
cp -a "$W/dest/var/lib/luma-fold/mesa/." "$OUT/"
echo "MESA_COMMIT=$MESA_COMMIT" > "$OUT/BUILD-INFO"
du -sh "$OUT"; find "$OUT" -name "*.so*" -type f -exec ls -la {} +
