#!/bin/sh -eu
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®
# CI package build: cross-compile imsd for aarch64 with crafter-build (the
# cross-compiling flow of AGENTS.md §4.3), run the test suite natively, and package
# the result via packaging/APKBUILD.binary. Expects an x86_64 Alpine
# environment with root — the workflow runs it in an alpine:edge job
# container on the privileged `flatpak` runner: crafter-build is the
# Crafter.Build Flatpak, and bubblewrap inside the container needs the
# privilege. Root only installs packages and hands off to a scratch user: the
# sysroot is built with apk.static --usermode (which refuses root) and abuild
# wants a user too.
#
# Built packages land in /home/build/.local/share/abuild/*/aarch64/imsd*.apk;
# the workflow's publish step uploads them to the Forgejo Alpine registry.

set -eu

# crafter-build is the Crafter.Build Flatpak: it carries its own clang, lld
# and libc++, and its SDK has glib, so nothing that compiles is installed
# here. `stable` is the branch Crafter.Build publishes from its release tags
# (`beta` follows every push to its master), so the toolchain of a release
# moves only with a release of the toolchain. The branch is overridable
# for a rehearsal against another one.
CRAFTER_REMOTE=${CRAFTER_REMOTE:-https://flatpak.catcrafts.net/catcrafts.flatpakrepo}
CRAFTER_REF=${CRAFTER_REF:-net.catcrafts.CrafterBuild//stable}

SRC=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)

retry() { # retry <description> <cmd...>
	_desc=$1; shift
	for _i in 1 2 3; do
		"$@" && return 0
		echo "$_desc failed (attempt $_i/3), retrying in 15s..." >&2
		sleep 15
	done
	echo "$_desc failed after 3 attempts" >&2
	return 1
}

# llvm for llvm-strip (abuild in cross mode strips with $CHOST-strip); the
# compiler is the Flatpak's.
if [ "$(id -u)" = 0 ]; then
	apk add -q git curl tar flatpak llvm abuild sudo
	# the Flatpak as a system installation the build user runs; its SDK
	# (org.freedesktop.Sdk) comes from Flathub
	flatpak remote-add --if-not-exists catcrafts "$CRAFTER_REMOTE"
	flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
	retry "install crafter-build" flatpak install -y --noninteractive catcrafts "$CRAFTER_REF"
	id build >/dev/null 2>&1 || adduser -D build
	addgroup build abuild 2>/dev/null || true
	echo 'build ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/build
	# abuild in cross mode strips with $CHOST-strip; llvm-strip handles any
	# ELF arch, so give it that name
	ln -sf "$(command -v llvm-strip)" /usr/local/bin/aarch64-alpine-linux-musl-strip
	# the CI checkout arrives root-owned; crafter-build writes bin/ into it
	chown -R build "$SRC"
	# -l: a login shell, so HOME really is /home/build (abuild keys + output);
	# it scrubs the environment, so carry the one knob that matters across
	exec su -l build -c "CRAFTER_REF='$CRAFTER_REF' sh -eu '$SRC/packaging/build-package.sh'"
fi

# implementations/main.cpp is the version's single source of truth (same
# derivation as make-bin-tarball.sh)
VER=$(sed -n 's/.*char\* Version = "\(.*\)".*/\1/p' "$SRC/implementations/main.cpp")
[ -n "$VER" ] || { echo "cannot read Version from implementations/main.cpp" >&2; exit 1; }
echo ">> packaging imsd $VER"

# --- crafter-build: the Flatpak, under the name the rest of this file uses.
# The build log names the build it ran with.
mkdir -p "$HOME/bin"
printf '#!/bin/sh\nexec flatpak run %s "$@"\n' "$CRAFTER_REF" > "$HOME/bin/crafter-build"
chmod +x "$HOME/bin/crafter-build"
PATH="$HOME/bin:$PATH"
flatpak info "$CRAFTER_REF" | sed -n 's/^ *\(Version\|Commit\|Date\): */crafter-build \1: /p'
crafter-build --version

# --- aarch64 Alpine sysroot (unprivileged: apk.static --usermode)
SYSROOT="$HOME/.cache/imsd/sysroot-aarch64-alpine"
retry "make sysroot" "$SRC/packaging/make-sysroot.sh" "$SYSROOT"

# --- cross-compile the products; run the suites natively, inside the Flatpak
# (same sources, host arch: no suite runs on the aarch64 binaries)
cd "$SRC"
# The phone is one SoC, 4x Cortex-A520 + 4x Cortex-A720, so build for it.
# armv8.6-a is the highest level whose mandatory set it exposes (8.7 would
# assume WFxT, 9.x SVE2 - neither is in its hwcaps); +fp16fml+aes+sha3+sm4 are
# the optional extensions it has. Same string as the fp6-img aports.
XTARGET="--target=aarch64-alpine-linux-musl --sysroot=$SYSROOT --march=armv8.6-a+fp16fml+aes+sha3+sm4 --mtune=cortex-a720"
crafter-build -- $XTARGET
crafter-build -- --product=media $XTARGET
crafter-build -- --product=dialerd $XTARGET
crafter-build -- --product=ofonod $XTARGET
crafter-build -- --product=pcscf $XTARGET
crafter-build test

# --- bundle + package
./packaging/make-bin-tarball.sh "$VER"

PKG="$HOME/pkg"
rm -rf "$PKG"
mkdir -p "$PKG"
cp "$SRC/packaging/APKBUILD.binary" "$PKG/APKBUILD"
cp "$SRC/packaging/org.kde.modem.daemon.desktop" \
   "$SRC/packaging/80-imsd.preset" "$SRC"/packaging/*.post-upgrade "$SRC"/packaging/*.post-install "$PKG/"
mv "imsd-$VER.tar.gz" "$PKG/"
sed -i "s/^pkgver=.*/pkgver=$VER/" "$PKG/APKBUILD"
# a throwaway signing key: phones trust the registry-signed APKINDEX, not
# per-package keys (same situation as fp6-img's pmbootstrap-built packages).
# abuild >= 3.18 keeps keys under ~/.config/abuild and output under
# ~/.local/share/abuild (REPODEST default).
abuild-keygen -a -n >/dev/null 2>&1
sudo cp "$HOME"/.config/abuild/*.rsa.pub /etc/apk/keys/
# CHOST puts abuild in cross mode so arch="aarch64" packages on this x86_64
# host. -d skips dependency handling entirely: nothing compiles under abuild
# (with -r, cross mode would try to install a nonexistent build-base-aarch64
# plus the runtime depends); !tracedeps in the APKBUILD keeps abuild from
# resolving the aarch64 ELF NEEDED entries against this x86_64 host.
cd "$PKG"
abuild checksum
CHOST=aarch64 abuild -d

echo "=== built packages ==="
ls -la "$HOME"/.local/share/abuild/*/aarch64/imsd*.apk
