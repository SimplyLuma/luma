#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build lpac-titan2e (packaging/rpm/lpac-titan2e.spec) in the Fedora builder.
#   LUMA_LPAC_SOURCE_ARCHIVE  use this copy of the pinned source archive
set -euo pipefail
repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo_root/config/desktop/inputs.env"
. "$repo_root/config/mobile/fp6-esim.env"
commit=$FP6_ESIM_LPAC_COMMIT
archive=lpac-$commit.tar.gz
archive_sha256=ea7d56fd95c74c5c2a98bb2240f147f5bc58a87b29b8f771d3b5df88cd74187d
output="$repo_root/build/packages/lpac-titan2e"
[ ! -e "$output" ] || { echo 'Existing output must be retained' >&2; exit 1; }
mkdir -p "$(dirname "$output")"
work=$(mktemp -d "$output.work.XXXXXX")
mkdir -p "$work/rpmbuild"/{BUILD,BUILDROOT,RPMS,SRPMS,SOURCES,SPECS}
src=$work/rpmbuild/SOURCES
if [ -n "${LUMA_LPAC_SOURCE_ARCHIVE:-}" ]; then
	cp -- "$LUMA_LPAC_SOURCE_ARCHIVE" "$src/$archive"
else
	curl -fsSL "https://github.com/estkme-group/lpac/archive/$commit.tar.gz" -o "$src/$archive"
fi
[ "$(sha256sum "$src/$archive" | cut -d' ' -f1)" = "$archive_sha256" ] || { echo "checksum differs: $archive" >&2; exit 1; }
for p in 0001-profile-download-read-activation-code-from-stdin 0002-profile-enable-read-identifier-from-stdin \
	0003-profile-enable-accept-19-digit-iccid 0004-profile-management-protected-selectors \
	0005-luma-owned-fd-driver; do
	install -m 0644 "$repo_root/patches/lpac/$p.patch" "$src/"
done
for source in luma_mm_fd.c luma-owned-apdu-wire.h; do
 install -m 0644 "$repo_root/patches/lpac/$source" "$src/"
done
install -m 0644 "$repo_root/packaging/rpm/lpac-titan2e.spec" "$work/rpmbuild/SPECS/"
export LUMA_RPM_BUILDER_ARCHITECTURE=${LUMA_TARGET_ARCHITECTURE:-aarch64}
"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$work/rpmbuild" "$FEDORA_RPM_BUILD_CONTAINER_AARCH64" '
set -euo pipefail
dnf5 -y install rpm-build "dnf5-command(builddep)"
dnf5 -y builddep SPECS/lpac-titan2e.spec
rpmbuild -ba --define "_topdir $PWD" SPECS/lpac-titan2e.spec
'
mkdir "$work/output"
cp -a "$work/rpmbuild/RPMS" "$work/rpmbuild/SRPMS" "$work/output/"
(cd "$work/output"; find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS)
mv "$work/output" "$output"
rm -rf "$work"
printf 'built %s\n' "$output"
