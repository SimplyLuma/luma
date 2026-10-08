#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®
# make-bin-tarball.sh — bundle the cross-compiled imsd binary + runtime files
# into the source tarball APKBUILD.binary consumes. Run from the repo root
# after a cross build; output lands in the current directory.
set -eu

VER="${1:-$(sed -n 's/.*char\* Version = "\(.*\)".*/\1/p' implementations/main.cpp)}"
[ -n "$VER" ] || { echo "could not determine version — pass it as \$1" >&2; exit 1; }
BIN=$(ls -t bin/imsd-aarch64-*/imsd 2>/dev/null | head -n1)
MEDIA=$(ls -t bin/imsd-media-aarch64-*/imsd-media 2>/dev/null | head -n1)
DIALERD=$(ls -t bin/imsd-dialerd-aarch64-*/imsd-dialerd 2>/dev/null | head -n1)
[ -n "$BIN" ] || { echo "no aarch64 imsd build found — cross-compile first" >&2; exit 1; }
[ -n "$MEDIA" ] || { echo "no aarch64 imsd-media build — cross-compile with --product=media" >&2; exit 1; }
[ -n "$DIALERD" ] || { echo "no aarch64 imsd-dialerd build — cross-compile with --product=dialerd" >&2; exit 1; }

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
mkdir "$stage/imsd-$VER"
cp "$BIN" "$stage/imsd-$VER/imsd"
cp "$MEDIA" "$stage/imsd-$VER/imsd-media"
cp "$DIALERD" "$stage/imsd-$VER/imsd-dialerd"
cp packaging/ims-pdn-up.sh packaging/imsd.service \
   packaging/imsd-dialerd.desktop \
   packaging/net.catcrafts.IMS1.conf "$stage/imsd-$VER/"
tar -C "$stage" -czf "imsd-$VER.tar.gz" "imsd-$VER"
echo "wrote imsd-$VER.tar.gz ($(du -h "imsd-$VER.tar.gz" | cut -f1))"
