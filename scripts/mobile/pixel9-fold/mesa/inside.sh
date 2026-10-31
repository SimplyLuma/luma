#!/bin/bash
# Runs inside the x86_64 Fedora 44 build root: host tools first, then aarch64.
set -euo pipefail
cd /w
export PKG_CONFIG=/usr/bin/pkgconf  # the pkg-config wrapper needs rpm
meson setup native src --prefix=/w/native-inst --buildtype=release \
  -Dplatforms= -Dglx=disabled -Degl=disabled -Dgbm=disabled -Dopengl=false -Dgles1=disabled -Dgles2=disabled \
  -Dgallium-drivers=panfrost -Dvulkan-drivers= -Dllvm=enabled -Dmesa-clc=enabled -Dinstall-mesa-clc=true \
  -Dprecomp-compiler=enabled -Dinstall-precomp-compiler=true -Dvalgrind=disabled -Dlibunwind=disabled \
  -Dlmsensors=disabled -Dgallium-va=disabled -Dvideo-codecs= -Dteflon=false >/w/native-setup.log
ninja -C native -j"$JOBS" install >/w/native-build.log
export PATH=/w/native-inst/bin:$PATH
command -v mesa_clc panfrost_compile
meson setup cross src --cross-file /w/aarch64.cross --prefix=/var/lib/luma-fold/mesa --libdir=lib64 \
  --buildtype=release -Db_ndebug=true -Dstrip=true \
  -Dplatforms=wayland -Dglx=disabled -Degl=enabled -Dgbm=enabled -Dopengl=true -Dgles1=disabled -Dgles2=enabled \
  -Dglvnd=enabled -Dgallium-drivers=panfrost -Dvulkan-drivers=panfrost -Dllvm=disabled \
  -Dmesa-clc=system -Dprecomp-compiler=system -Dvalgrind=disabled -Dlibunwind=disabled -Dlmsensors=disabled \
  -Dgallium-va=disabled -Dvideo-codecs= -Dteflon=false -Dzstd=enabled -Dexpat=enabled >/w/cross-setup.log
ninja -C cross -j"$JOBS" >/w/cross-build.log
DESTDIR=/w/dest ninja -C cross install >/w/cross-install.log
