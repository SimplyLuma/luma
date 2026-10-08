#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/greetd-source.env"
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] || {
  echo 'error: build Luma greetd session on the Fedora AArch64 builder' >&2; exit 1;
}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT INT TERM
# A builder with no route to crates.io can supply the pinned tarball locally.
# The hash is still checked, so the source is identical either way.
if [ -n "${LUMA_GREETD_TARBALL:-}" ]; then
  cp -- "$LUMA_GREETD_TARBALL" "$work/greetd.tar.gz"
else
  curl -fsSL "$LUMA_GREETD_URL" -o "$work/greetd.tar.gz"
fi
test "$(sha256sum "$work/greetd.tar.gz" | cut -d' ' -f1)" = "$LUMA_GREETD_SHA256"
tar -xzf "$work/greetd.tar.gz" -C "$work"
src="$work/greetd-$LUMA_GREETD_VERSION"
patch -d "$src" -p1 < "$repo_root/patches/greetd/0002-luma-persistent-compositor-session.patch"
patch -d "$src" -p1 < "$repo_root/patches/greetd/0003-luma-seatless-authenticated-session.patch"
# Likewise for the dependency tree: point at a `cargo vendor` directory when
# the crate registry is unreachable. Cargo verifies each crate against the
# checksums in Cargo.lock, so a vendored build is not a weaker one.
if [ -n "${LUMA_GREETD_VENDOR_DIR:-}" ]; then
  mkdir -p "$src/.cargo"
  cat >"$src/.cargo/config.toml" <<VENDOR
[source.crates-io]
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "$LUMA_GREETD_VENDOR_DIR"
VENDOR
  (cd "$src" && cargo build --release --offline -p greetd)
else
  (cd "$src" && cargo build --release -p greetd)
fi
top="$work/rpmbuild"
mkdir -p "$top"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
install -m 0755 "$src/target/release/greetd" "$top/SOURCES/luma-greetd"
install -m 0644 "$repo_root/packaging/systemd/40-luma-persistent-greetd.conf" "$top/SOURCES/"
install -m 0644 "$repo_root/packaging/rpm/luma-greetd-session.spec" "$top/SPECS/"
rpmbuild -bb --define "_topdir $top" "$top/SPECS/luma-greetd-session.spec"
out="$repo_root/build/packages/greetd-session/aarch64"
mkdir -p "$out"
rpm_path=$(find "$top/RPMS" -name "$LUMA_GREETD_SESSION_NEVRA.rpm" -print -quit)
test -n "$rpm_path"
install -m 0644 "$rpm_path" "$out/"
sha256sum "$out/$LUMA_GREETD_SESSION_NEVRA.rpm"
