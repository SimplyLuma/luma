#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Build Depot Hello as a single-file Flatpak bundle without flatpak-builder:
# it is one Python script, so nothing compiles and any CPU can be targeted.
#   build.sh VERSION ARCH OUT.flatpak
set -euo pipefail
version=$1 arch=$2 out=$3
here=$(cd "$(dirname "$0")" && pwd)
id=io.github.nmcmil.DepotHello
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
d=$work/app
mkdir -p "$d/files/bin" "$d/files/share/applications" "$d/files/share/icons/hicolor/scalable/apps" "$d/files/share/metainfo"
sed "s/@VERSION@/$version/" "$here/depot-hello" >"$d/files/bin/depot-hello"
chmod 0755 "$d/files/bin/depot-hello"
cp "$here/$id.desktop" "$d/files/share/applications/"
cp "$here/$id.svg" "$d/files/share/icons/hicolor/scalable/apps/"
sed "s/@VERSION@/$version/" "$here/$id.metainfo.xml" >"$d/files/share/metainfo/$id.metainfo.xml"
cat >"$d/metadata" <<META
[Application]
name=$id
runtime=org.projectluma.Platform/$arch/44
sdk=org.projectluma.Sdk/$arch/44
command=depot-hello
META
flatpak build-finish --share=ipc --socket=wayland --socket=fallback-x11 --device=dri "$d"
ostree init --mode=archive-z2 --repo="$work/repo"
flatpak build-export --arch="$arch" --disable-sandbox --update-appstream --no-update-summary "$work/repo" "$d" stable
flatpak build-bundle --arch="$arch" --runtime-repo=https://dl.simplyluma.com/luma.flatpakrepo "$work/repo" "$out" "$id" stable
echo "$out"
