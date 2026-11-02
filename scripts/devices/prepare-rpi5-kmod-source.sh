#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Prepare the luma-rpi5-kmod source tree from a Fedora kernel SRPM: unpack
# that kernel's upstream tarball, apply Fedora's own patch, then the Pi patch
# series (patches/linux-rpi5/SERIES), and copy the files the kmods build from
# (src/luma-rpi5-kmod/sources.list) next to their Kbuild files.
#
# usage: prepare-rpi5-kmod-source.sh KERNEL.src.rpm SERIES OUTPUT_DIR

set -euo pipefail

[ "$#" -eq 3 ] || { printf 'usage: %s KERNEL.src.rpm SERIES OUTPUT_DIR\n' "$0" >&2; exit 2; }
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
srpm=$(realpath "$1")
series=$2
output=$3
patch_dir="$repo_root/patches/linux-rpi5/$series"
kmod_src="$repo_root/src/luma-rpi5-kmod"

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
for tool in cpio patch rpm2cpio tar xz; do
  command -v "$tool" >/dev/null 2>&1 || fail "missing tool: $tool"
done
[ -d "$patch_dir" ] || fail "no patch series: $patch_dir"
[ ! -e "$output" ] || fail "output exists: $output"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT INT TERM
(cd "$work" && rpm2cpio "$srpm" | cpio -idm --quiet './linux-*.tar.xz' './patch-*-redhat.patch')
tarball=$(find "$work" -maxdepth 1 -name 'linux-*.tar.xz' | head -n1)
redhat=$(find "$work" -maxdepth 1 -name 'patch-*-redhat.patch' | head -n1)
[ -n "$tarball" ] || fail "SRPM has no linux tarball"
tar -C "$work" -xf "$tarball"
tree=$(find "$work" -maxdepth 1 -type d -name 'linux-*' | head -n1)
[ -n "$tree" ] || fail "tarball has no linux-* tree"
# Fedora's patch first (it may be empty), then the Pi series; no fuzz.
if [ -n "$redhat" ] && [ -s "$redhat" ]; then
  patch -d "$tree" -p1 -s -F0 --no-backup-if-mismatch <"$redhat"
fi
for p in "$patch_dir"/*.patch; do
  patch -d "$tree" -p1 -s -F0 --no-backup-if-mismatch <"$p" || fail "patch does not apply: ${p##*/}"
done

mkdir -p "$output"
cp -R "$kmod_src/." "$output/"
while read -r path; do
  case "$path" in ''|'#'*) continue ;; esac
  [ -e "$tree/$path" ] || fail "kernel tree lacks $path"
  install -d "$output/$(dirname "$path")"
  if [ -d "$tree/$path" ]; then
    cp -R "$tree/$path." "$output/$path"
  else
    install -m 0644 "$tree/$path" "$output/$path"
  fi
done <"$kmod_src/sources.list"
# Out-of-tree builds read Kbuild before Makefile; vc4's Kbuild includes it.
install -d "$output/include/uapi"
{
  printf 'kernel_srpm %s %s\n' "$(basename "$srpm")" "$(sha256sum "$srpm" | cut -d' ' -f1)"
  for p in "$patch_dir"/*.patch; do
    printf 'patch %s %s\n' "${p##*/}" "$(sha256sum "$p" | cut -d' ' -f1)"
  done
} >"$output/SOURCES.manifest"
printf 'kmod source: %s\n' "$output"
