#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build GNOME Mobile 48 into a private staged prefix. This script deliberately
# does not install packages, write /opt, or switch the live graphical session.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/gnome-mobile-source.env"

workdir=${LUMA_GNOME_MOBILE_WORKDIR:-$PWD/gnome-mobile-bakeoff}
jobs=${LUMA_GNOME_MOBILE_JOBS:-2}
src=$workdir/src
stage=$workdir/stage
pcdir=$workdir/build-pc

case "$jobs" in
  ''|*[!0-9]*) printf 'jobs must be a positive integer\n' >&2; exit 2 ;;
esac
[ "$jobs" -gt 0 ] || { printf 'jobs must be positive\n' >&2; exit 2; }

fetch_exact() {
  name=$1
  url=$2
  commit=$3
  target=$src/$name

  if [ ! -d "$target/.git" ]; then
    git clone --filter=blob:none --no-checkout "$url" "$target"
  fi
  git -C "$target" fetch --depth=1 origin "$commit"
  git -C "$target" checkout --detach --force "$commit"
  [ "$(git -C "$target" rev-parse HEAD)" = "$commit" ] || {
    printf 'source identity mismatch: %s\n' "$name" >&2
    exit 1
  }
}

setup_build() {
  builddir=$1
  shift
  if [ -f "$builddir/meson-private/coredata.dat" ]; then
    meson setup --wipe "$builddir" "$@"
  else
    meson setup "$builddir" "$@"
  fi
}

mkdir -p "$src" "$stage" "$pcdir"
fetch_exact gnome-settings-daemon-mobile \
  "$GNOME_SETTINGS_DAEMON_MOBILE_URL" "$GNOME_SETTINGS_DAEMON_MOBILE_COMMIT"
fetch_exact mutter-mobile "$MUTTER_MOBILE_URL" "$MUTTER_MOBILE_COMMIT"
fetch_exact gnome-shell-mobile \
  "$GNOME_SHELL_MOBILE_URL" "$GNOME_SHELL_MOBILE_COMMIT"
fetch_exact gjs "$GJS_URL" "$GJS_COMMIT"
fetch_exact gjs-introspection-tests \
  "$GJS_INTROSPECTION_TESTS_URL" "$GJS_INTROSPECTION_TESTS_COMMIT"
fetch_exact gvc-gsd "$GSD_GVC_URL" "$GSD_GVC_COMMIT"
fetch_exact gvc-shell "$SHELL_GVC_URL" "$SHELL_GVC_COMMIT"
fetch_exact gvdb "$MUTTER_GVDB_URL" "$MUTTER_GVDB_COMMIT"

mkdir -p \
  "$src/gjs/subprojects/gobject-introspection-tests" \
  "$src/gnome-settings-daemon-mobile/subprojects/gvc" \
  "$src/mutter-mobile/subprojects/gvdb" \
  "$src/gnome-shell-mobile/subprojects/gvc"
cp -a "$src/gvc-gsd/." \
  "$src/gnome-settings-daemon-mobile/subprojects/gvc/"
cp -a "$src/gjs-introspection-tests/." \
  "$src/gjs/subprojects/gobject-introspection-tests/"
cp -a "$src/gvdb/." "$src/mutter-mobile/subprojects/gvdb/"
cp -a "$src/gvc-shell/." "$src/gnome-shell-mobile/subprojects/gvc/"

for patch_file in "$repo_root"/patches/gnome-mobile/*.patch; do
  git -C "$src/gnome-shell-mobile" apply --check "$patch_file"
  git -C "$src/gnome-shell-mobile" apply "$patch_file"
done

setup_build "$workdir/build-gsd" "$src/gnome-settings-daemon-mobile" \
  --prefix="$GNOME_MOBILE_PREFIX" --libdir=lib64 \
  --sysconfdir="$GNOME_MOBILE_PREFIX/etc" \
  -Dsystemd=false -Delogind=false \
  -Dudev_dir="$GNOME_MOBILE_PREFIX/lib/udev" \
  -Dcups=false -Dsmartcard=false -Dusb-protection=false \
  -Dwwan=false -Dcolord=false
nice -n 10 meson compile -C "$workdir/build-gsd" -j "$jobs"
DESTDIR="$stage" meson install -C "$workdir/build-gsd"

setup_build "$workdir/build-gjs" "$src/gjs" \
  --prefix="$GNOME_MOBILE_PREFIX" --libdir=lib64 \
  --sysconfdir="$GNOME_MOBILE_PREFIX/etc" \
  --buildtype=release \
  -Dinstalled_tests=false -Dprofiler=disabled -Dreadline=disabled \
  -Ddtrace=false -Dsystemtap=false \
  -Dskip_dbus_tests=true -Dskip_gtk_tests=true
nice -n 10 meson compile -C "$workdir/build-gjs" -j "$jobs"
DESTDIR="$stage" meson install -C "$workdir/build-gjs"

private_prefix=$stage$GNOME_MOBILE_PREFIX
cp "$private_prefix/lib64/pkgconfig/gnome-settings-daemon.pc" "$pcdir/"
cp "$private_prefix/lib64/pkgconfig/gjs-1.0.pc" "$pcdir/"
sed -i "s|^prefix=$GNOME_MOBILE_PREFIX|prefix=$private_prefix|" \
  "$pcdir/gnome-settings-daemon.pc"
sed -i "s|^prefix=$GNOME_MOBILE_PREFIX|prefix=$private_prefix|" \
  "$pcdir/gjs-1.0.pc"

PKG_CONFIG_PATH="$pcdir" setup_build \
  "$workdir/build-mutter" "$src/mutter-mobile" \
  --prefix="$GNOME_MOBILE_PREFIX" --libdir=lib64 \
  --sysconfdir="$GNOME_MOBILE_PREFIX/etc" \
  -Dudev_dir="$GNOME_MOBILE_PREFIX/lib/udev" \
  -Dnative_backend=true -Dintrospection=true \
  -Dxwayland_path=/usr/bin/Xwayland -Dremote_desktop=true \
  -Dprofiler=false -Dtests=disabled -Dmutter_tests=false \
  -Dcogl_tests=false -Dclutter_tests=false -Dinstalled_tests=false \
  -Ddocs=false -Dsm=false
nice -n 10 meson compile -C "$workdir/build-mutter" -j "$jobs"
DESTDIR="$stage" meson install -C "$workdir/build-mutter"

cp "$private_prefix/lib64/pkgconfig/libmutter-16.pc" \
  "$private_prefix"/lib64/pkgconfig/mutter-*.pc "$pcdir/"
sed -i "s|^prefix=$GNOME_MOBILE_PREFIX|prefix=$private_prefix|" \
  "$pcdir"/*.pc

PKG_CONFIG_PATH="$pcdir" setup_build \
  "$workdir/build-shell" "$src/gnome-shell-mobile" \
  --prefix="$GNOME_MOBILE_PREFIX" --libdir=lib64 \
  --sysconfdir="$GNOME_MOBILE_PREFIX/etc" \
  -Dsystemd=false -Dtests=false -Dman=false \
  -Dextensions_app=false -Dextensions_tool=false \
  -Dcamera_monitor=true -Dnetworkmanager=true -Dportal_helper=true
nice -n 10 meson compile -C "$workdir/build-shell" -j "$jobs"
DESTDIR="$stage" meson install -C "$workdir/build-shell"

patchelf --set-rpath \
  "$GNOME_MOBILE_PREFIX/lib64/gnome-shell:$GNOME_MOBILE_PREFIX/lib64/mutter-16:$GNOME_MOBILE_PREFIX/lib64" \
  "$private_prefix/bin/gnome-shell"
patchelf --set-rpath "$GNOME_MOBILE_PREFIX/lib64/mutter-16" \
  "$private_prefix/lib64/gnome-shell/libst-16.so"
patchelf --set-rpath "$GNOME_MOBILE_PREFIX/lib64/mutter-16" \
  "$private_prefix/lib64/gnome-shell/libgnome-shell-menu.so"
patchelf --set-rpath \
  "$GNOME_MOBILE_PREFIX/lib64/gnome-shell:$GNOME_MOBILE_PREFIX/lib64/mutter-16:$GNOME_MOBILE_PREFIX/lib64" \
  "$private_prefix/lib64/gnome-shell/libshell-16.so"

for runtime_file in \
  "$private_prefix/bin/gnome-shell" \
  "$private_prefix/lib64/gnome-shell/libst-16.so" \
  "$private_prefix/lib64/gnome-shell/libgnome-shell-menu.so" \
  "$private_prefix/lib64/gnome-shell/libshell-16.so"; do
  if patchelf --print-rpath "$runtime_file" | grep -Fq "$stage"; then
    printf 'staging path remains in runtime RPATH: %s\n' "$runtime_file" >&2
    exit 1
  fi
done

glib-compile-schemas "$private_prefix/share/glib-2.0/schemas"

export LD_LIBRARY_PATH="$private_prefix/lib64:$private_prefix/lib64/mutter-16:$private_prefix/lib64/gnome-shell"
if ldd "$private_prefix/bin/gnome-shell" | grep -F 'not found'; then
  printf 'GNOME Mobile staged runtime has unresolved libraries\n' >&2
  exit 1
fi

printf 'GNOME Mobile staged at %s\n' "$private_prefix"
"$private_prefix/bin/gnome-shell" --version

archive=$workdir/gnome-mobile-48-fp6.tar.gz
(
  cd "$stage"
  tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
    -cf - "${GNOME_MOBILE_PREFIX#/}"
) | gzip -n >"$archive"
sha256sum "$archive" >"$archive.sha256"
printf 'Deterministic archive: %s\n' "$archive"
cat "$archive.sha256"
