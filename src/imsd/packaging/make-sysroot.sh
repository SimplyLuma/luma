#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®
# make-sysroot.sh — build an Alpine aarch64 sysroot for cross-compiling imsd.
#
# Downloads apk-tools-static + the Alpine signing keys from the CDN and
# populates a minimal rootfs (musl, libc++, glib and their headers, the Linux
# UAPI headers) that
# clang can use via --sysroot. No root, no qemu, no device needed:
#
#   packaging/make-sysroot.sh [dir]        # default: ~/.cache/imsd/sysroot-aarch64-alpine
#   crafter-build -- --target=aarch64-alpine-linux-musl --sysroot=<dir> \
#       --march=… --mtune=…                # the phone's values: AGENTS.md §4.3
set -eu

SYSROOT="${1:-$HOME/.cache/imsd/sysroot-aarch64-alpine}"
MIRROR="${MIRROR:-https://dl-cdn.alpinelinux.org/alpine/edge}"
ARCH=aarch64
# gcc is in the list for its crtbeginS.o/libgcc: clang's driver for
# *-alpine-linux-musl links against the GCC runtime it detects under
# <sysroot>/usr/lib/gcc/<triple>/. llvm-runtimes is in it for the std module
# source of this very libc++ (usr/share/libc++/v1/std.cppm; libc++-dev ships
# none): crafter-build compiles the sysroot's own copy first. --upgrade below
# is for a run over an existing sysroot: added alone beside libc++ 22,
# llvm-runtimes 23's source did not compile against its headers; upgraded
# with it, it did. linux-headers is in it for <linux/qrtr.h>, which
# imsd-pcscf's QRTR socket needs (the sysroot had it only as another
# package's dependency before).
PKGS="musl-dev libc++ libc++-dev llvm-runtimes glib-dev compiler-rt llvm-libunwind-dev gcc linux-headers"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

fetch_pkg() {  # $1 = repo (main/community), $2 = package name -> path on stdout
    file=$(curl -fsSL "$MIRROR/$1/x86_64/" |
        grep -o "href=\"$2-[0-9][^\"]*\.apk\"" | head -n1 | cut -d'"' -f2)
    [ -n "$file" ] || { echo "cannot find $2 in $MIRROR/$1" >&2; exit 1; }
    curl -fsSL -o "$work/$file" "$MIRROR/$1/x86_64/$file"
    echo "$work/$file"
}

echo ">> fetching apk-tools-static + alpine-keys"
tar -xzf "$(fetch_pkg main apk-tools-static)" -C "$work" sbin/apk.static 2>/dev/null
tar -xzf "$(fetch_pkg main alpine-keys)" -C "$work" usr/share/apk/keys 2>/dev/null

echo ">> populating $SYSROOT ($ARCH: $PKGS)"
mkdir -p "$SYSROOT"
"$work/sbin/apk.static" \
    --arch "$ARCH" --root "$SYSROOT" --initdb --no-scripts --no-cache \
    --usermode --keys-dir "$work/usr/share/apk/keys/$ARCH" \
    -X "$MIRROR/main" -X "$MIRROR/community" \
    add --upgrade $PKGS

echo ">> done: $SYSROOT"
