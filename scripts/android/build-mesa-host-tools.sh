#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build the Mesa host compilers that external/mesa runs during an Android
# build (-Dmesa-clc=system -Dprecomp-compiler=system). Mesa requires them to
# come from the same source as the target libraries, so a Mesa revision change
# also replaces Waydroid's prebuilts/mesa-tools. The result has the same layout
# as that project: root/bin, root/lib64 with its own loader, and wrappers.
#
# usage: build-mesa-host-tools.sh FEDORA_BUILD_ROOT MESA_SOURCE OUTPUT
# FEDORA_BUILD_ROOT is an x86_64 tree with clang, LLVM, libclc, SPIRV-LLVM-
# Translator, SPIRV-Tools, meson, ninja, Mako and PyYAML, run with podman.

set -euo pipefail

[ "$#" -eq 3 ] || { printf 'usage: %s FEDORA_BUILD_ROOT MESA_SOURCE OUTPUT\n' "$0" >&2; exit 2; }
build_root=$(realpath "$1")
source_dir=$(realpath "$2")
output=$3
jobs=${LUMA_BUILD_JOBS:-8}
tools=(mesa_clc vtn_bindgen2 panfrost_compile asahi_clc)

test -x "$build_root/usr/bin/meson" || { printf 'error: build root lacks meson\n' >&2; exit 1; }
test -f "$source_dir/VERSION" || { printf 'error: not a Mesa source tree\n' >&2; exit 1; }
version=$(cat "$source_dir/VERSION")
work=$(mktemp -d "${TMPDIR:-/var/tmp}/mesa-host-tools.XXXXXX")
trap 'rm -rf "$work"' EXIT

podman run --rm --network none --security-opt label=disable \
  -v "$source_dir:/src:ro" -v "$work:/w" -e JOBS="$jobs" \
  --rootfs "$build_root" /bin/bash -euo pipefail -c '
    export PKG_CONFIG=/usr/bin/pkgconf
    meson setup /w/build /src --buildtype=release -Db_ndebug=true \
      -Dplatforms= -Dglx=disabled -Degl=disabled -Dgbm=disabled -Dopengl=false \
      -Dgles1=disabled -Dgles2=disabled -Dgallium-drivers=panfrost,asahi \
      -Dvulkan-drivers= -Dllvm=enabled -Dmesa-clc=enabled -Dinstall-mesa-clc=true \
      -Dprecomp-compiler=enabled -Dinstall-precomp-compiler=true -Dvalgrind=disabled \
      -Dlibunwind=disabled -Dlmsensors=disabled -Dgallium-va=disabled -Dvideo-codecs= \
      -Dteflon=false -Dstatic-libclc=all >/w/setup.log
    ninja -C /w/build -j"$JOBS" src/compiler/clc/mesa_clc src/compiler/spirv/vtn_bindgen2 \
      src/panfrost/clc/panfrost_compile src/asahi/clc/asahi_clc >/w/build.log
    mkdir -p /w/root/bin /w/root/lib64
    for tool in mesa_clc vtn_bindgen2 panfrost_compile asahi_clc; do
      install -m 0755 "$(find /w/build/src -type f -name "$tool" -perm -u+x | head -n 1)" /w/root/bin/
    done
    # Bundle every shared library, including glibc and its loader, so the
    # tools run on the Ubuntu builder root exactly as Waydroid prebuilts do.
    for tool in /w/root/bin/*; do ldd "$tool"; done |
      awk "/=> \\// {print \$3} /^\\t\\/.*ld-linux/ {print \$1}" | sort -u |
      while read -r library; do install -m 0644 "$library" /w/root/lib64/; done
    chmod 0755 /w/root/lib64/ld-linux-x86-64.so.2
    # Clang finds its resource headers relative to the executable.
    resource=$(clang -print-resource-dir)
    mkdir -p "/w/root/lib/clang/$(basename "$resource")"
    cp -a "$resource/include" "/w/root/lib/clang/$(basename "$resource")/"
    for tool in /w/root/bin/*; do "$tool" --help >/dev/null 2>&1 || true; done
  '

rm -rf "$output"
install -d -m 0755 "$output"
cp -a "$work/root" "$output/root"
for tool in "${tools[@]}"; do
  cat > "$output/$tool" <<'EOF'
#!/bin/bash
DIR=$(dirname "$(realpath "$0")")
LIBS="$DIR/root/lib64"
BIN="$DIR/root/bin/$(basename "$0")"

exec env LD_LIBRARY_PATH="$LIBS:$LD_LIBRARY_PATH" "$LIBS/ld-linux-x86-64.so.2" "$BIN" "$@"
EOF
  chmod 0755 "$output/$tool"
done
printf 'MESA_VERSION=%s\nMESA_COMMIT=%s\n' "$version" \
  "$(git -C "$source_dir" rev-parse HEAD 2>/dev/null || printf unknown)" > "$output/BUILD-INFO"
printf 'Mesa %s host tools: %s\n' "$version" "$output"
