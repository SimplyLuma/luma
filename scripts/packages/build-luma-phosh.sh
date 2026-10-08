#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/phosh-source.env"

for tool in curl patch rpmbuild sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required Luma Phosh build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] || {
  printf 'error: build Luma Phosh on the Fedora AArch64 builder\n' >&2
  exit 1
}

output_dir="$repo_root/build/packages/luma-phosh/aarch64"
install -d -m 0755 "$repo_root/build/packages"
# Keep compiler probes and build products on the builder's executable
# temporary filesystem.  The repository can live on a capacity-oriented
# noexec mount; only the verified RPM artifacts need to be copied back there.
work_dir=$(mktemp -d /tmp/luma-phosh.work.XXXXXX)
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
install -d -m 0755 "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

fetch_verified() {
  local url=$1 expected=$2 destination=$3 actual local_copy
  # A builder with no route to the upstream forges can supply the pinned
  # tarballs locally. The hash below is still checked, so the source is
  # identical either way.
  local_copy="${LUMA_PHOSH_SOURCE_DIR:-}/$(basename "$url")"
  if [ -n "${LUMA_PHOSH_SOURCE_DIR:-}" ] && [ -f "$local_copy" ]; then
    cp -- "$local_copy" "$destination"
  else
    curl -fsSL "$url" -o "$destination"
  fi
  actual=$(sha256sum "$destination" | cut -d ' ' -f 1)
  [ "$actual" = "$expected" ] || {
    printf 'error: source hash mismatch for %s: %s\n' "$destination" "$actual" >&2
    exit 1
  }
}

fetch_verified "$LUMA_PHOSH_URL" "$LUMA_PHOSH_SHA256" \
  "$rpmbuild_dir/SOURCES/phosh-$LUMA_PHOSH_COMMIT.tar.gz"
fetch_verified "$LUMA_PHOSH_GVC_URL" "$LUMA_PHOSH_GVC_SHA256" \
  "$rpmbuild_dir/SOURCES/libgnome-volume-control-$LUMA_PHOSH_GVC_COMMIT.tar.gz"
fetch_verified "$LUMA_PHOSH_CALL_UI_URL" "$LUMA_PHOSH_CALL_UI_SHA256" \
  "$rpmbuild_dir/SOURCES/libcall-ui-$LUMA_PHOSH_CALL_UI_VERSION.tar.gz"

install -m 0644 \
  "$repo_root/patches/phosh/0001-luma-handheld-home-dock-search.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/phosh/0002-luma-status-order-search-purpose.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/phosh/0003-luma-quiet-session-handoff.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/phosh/0004-luma-fold-quick-options-before-first-frame.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/phosh/0005-luma-publish-first-complete-home-frame.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/phosh/0006-luma-commit-initial-folded-drag-state.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/phosh/0007-luma-presence-session-lock.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/phosh/0008-luma-theme-native-lockscreen.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/phosh/0009-luma-foreground-app-status-surface.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/phosh/0010-luma-semantic-status-surfaces.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/phosh/0011-luma-wallpaper-status-continuity.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/phosh/0012-luma-notification-system.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/phosh/0013-luma-responsive-quick-options-dismiss.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/phosh/0014-luma-notification-quick-options-fixups.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/phosh/0015-luma-notification-lifecycle.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/phosh/0016-luma-shared-search-client.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/phosh/0017-luma-physical-lock-carousel.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/phosh/0018-luma-single-owner-touch-drawers.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/packaging/systemd/90-luma-phosh.conf" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/packaging/rpm/luma-phosh.spec" \
  "$rpmbuild_dir/SPECS/"

rpmbuild -ba --define "_topdir $rpmbuild_dir" \
  --define "use_source_date_epoch_as_buildtime 1" \
  "$rpmbuild_dir/SPECS/luma-phosh.spec"

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS" "$output_dir/SRPMS"
find "$rpmbuild_dir/RPMS" -type f -name '*.rpm' \
  -exec install -m 0644 {} "$output_dir/RPMS/" \;
find "$rpmbuild_dir/SRPMS" -type f -name '*.rpm' \
  -exec install -m 0644 {} "$output_dir/SRPMS/" \;
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Luma Phosh packages: %s\n' "$output_dir"
