#!/usr/bin/env bash
set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo_root/config/desktop/inputs.env"
architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in
 x86_64) image=$FEDORA_RPM_BUILD_CONTAINER ;;
 aarch64) image=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
 *) exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture
out="$repo_root/build/packages/luma-quick-view"
mkdir -p "$out"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
curl --fail --location --proto '=https' --tlsv1.2 \
 https://download.gnome.org/sources/sushi/46/sushi-46.0.tar.xz -o "$out/SOURCES/sushi-46.0.tar.xz"
printf '%s  %s\n' 96085baaa430ab2142c606aab5c47e2fbb2fd3eb70a352137e65c59a58a0f2c6 "$out/SOURCES/sushi-46.0.tar.xz" | sha256sum -c -
cp "$repo_root/patches/sushi/"*.patch "$out/SOURCES/"
cp "$repo_root/packaging/rpm/luma-quick-view.spec" "$out/SPECS/"
# %check runs the source-level tests against the patched tree.
tar -C "$repo_root/tests" -cf "$out/SOURCES/luma-quick-view-tests.tar" quick-view
"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$out" "$image" '
 set -euo pipefail
 dnf5 -y install rpm-build meson gcc gettext nodejs python3 gjs-devel gtk3-devel gtksourceview4-devel gobject-introspection-devel evince-devel webkit2gtk4.1-devel gstreamer1-plugins-base-devel libepoxy-devel harfbuzz-devel python3-cmarkgfm
 rpmbuild --define "_topdir $PWD" -ba SPECS/luma-quick-view.spec
'
