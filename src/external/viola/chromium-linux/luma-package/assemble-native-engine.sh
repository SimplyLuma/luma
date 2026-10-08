#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
#
# Assemble the engine of a Luma native-integration release as a tarball the
# RPM spec consumes. The engine is the signed Viola Linux release RPM's
# /opt/viola/browser payload with the files the native build changed (the
# DevTools-window platform patch: chrome, paks, snapshot, locales) taken from
# that build's output directory. Every file must match the release manifest
# (native-<N>-engine.sha256, recorded from the approved release) or the
# assembly fails; nothing is taken on trust.
#
# assemble-native-engine.sh --release-rpm RPM --build-out DIR \
#   --manifest native-21-engine.sha256 --out viola-native-engine.tar.zst
set -euo pipefail

release_rpm= build_out= manifest= out=
while [ $# -gt 0 ]; do
  case $1 in
    --release-rpm) release_rpm=$2; shift 2 ;;
    --build-out) build_out=$2; shift 2 ;;
    --manifest) manifest=$2; shift 2 ;;
    --out) out=$2; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -f "$release_rpm" ] && [ -d "$build_out" ] && [ -f "$manifest" ] && [ -n "$out" ] || {
  echo 'usage: assemble-native-engine.sh --release-rpm RPM --build-out DIR --manifest FILE --out TAR.ZST' >&2
  exit 2
}
manifest=$(realpath -- "$manifest")
out=$(realpath -m -- "$out")
# The release RPM must be the one its signed SHA256SUMS names.
sums="$(dirname -- "$release_rpm")/SHA256SUMS"
[ -f "$sums" ] && [ -f "$sums.asc" ] || { echo "no signed SHA256SUMS beside $release_rpm" >&2; exit 1; }
(cd "$(dirname -- "$release_rpm")" && grep -F " $(basename -- "$release_rpm")" SHA256SUMS | sed 's/ \*/  /' | sha256sum -c --quiet -) ||
  { echo "release RPM digest does not match SHA256SUMS" >&2; exit 1; }

stage=$(mktemp -d "$(dirname -- "$out")/.engine-assembly.XXXXXX")
trap 'rm -rf "$stage"' EXIT
gpg --dearmor <"$(dirname -- "$release_rpm")/Viola-Linux-Release-Key-public.asc" >"$stage/release-key.gpg"
gpgv --keyring "$stage/release-key.gpg" "$sums.asc" "$sums" 2>/dev/null ||
  { echo "SHA256SUMS signature does not verify" >&2; exit 1; }
release="$stage/release"
engine="$stage/engine"
mkdir -p "$release" "$engine/engine" "$engine/system"
engine_root="$engine"
engine="$engine/engine"
rpm2cpio "$release_rpm" | (cd "$release" && cpio -idm --quiet './opt/viola/browser/*' './usr/share/appdata/*' \
  './usr/share/gnome-control-center/*' './usr/share/licenses/*' './usr/share/man/*')

from_build=0 from_release=0
while read -r digest path; do
  [ -n "$path" ] || continue
  mkdir -p "$engine/$(dirname -- "$path")"
  # The approved release keeps the SUID helper as chrome-sandbox.packaged.
  release_path=${path%.packaged}
  if [ -f "$build_out/$path" ] && [ "$(sha256sum <"$build_out/$path" | cut -c1-64)" = "$digest" ]; then
    cp -p "$build_out/$path" "$engine/$path"
    from_build=$((from_build + 1))
  elif [ -f "$release/opt/viola/browser/$release_path" ] &&
       [ "$(sha256sum <"$release/opt/viola/browser/$release_path" | cut -c1-64)" = "$digest" ]; then
    cp -p "$release/opt/viola/browser/$release_path" "$engine/$path"
    from_release=$((from_release + 1))
  else
    echo "no source matches the approved engine file: $path ($digest)" >&2
    exit 1
  fi
done <"$manifest"

(cd "$engine" && sha256sum -c --quiet "$manifest") || { echo 'assembled engine does not verify' >&2; exit 1; }
# Release metadata files (AppStream, default-apps, licenses, manuals) as signed.
cp -a "$release/usr" "$engine_root/system/"
tar --sort=name --owner=0 --group=0 --numeric-owner --mtime=@0 -C "$engine_root" -cf - engine system |
  zstd -q -T0 -10 -o "$out.partial"
mv -f "$out.partial" "$out"
printf 'assembled %s: %d files from the native build, %d from the signed release\n' \
  "$out" "$from_build" "$from_release"
