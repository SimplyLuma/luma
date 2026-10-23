#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build Luma callaudiod (packaging/rpm/callaudiod.spec) in the Fedora builder.
#   LUMA_CALLAUDIOD_SOURCE_ARCHIVE  use this copy of the pinned source archive
set -euo pipefail
repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo_root/config/desktop/inputs.env"
version=0.1.99
archive=callaudiod-$version.tar.gz
archive_sha256=6b8214fb1fa08fa89012d8c85d671a61be37ad33373065daf7ae17bd703bd0ad
output="$repo_root/build/packages/callaudiod"
[ ! -e "$output" ] || { echo 'Existing output must be retained' >&2; exit 1; }
mkdir -p "$(dirname "$output")"
work=$(mktemp -d "$output.work.XXXXXX")
mkdir -p "$work/rpmbuild"/{BUILD,BUILDROOT,RPMS,SRPMS,SOURCES,SPECS}
src=$work/rpmbuild/SOURCES
if [ -n "${LUMA_CALLAUDIOD_SOURCE_ARCHIVE:-}" ]; then
	cp -- "$LUMA_CALLAUDIOD_SOURCE_ARCHIVE" "$src/$archive"
else
	curl -fsSL "https://gitlab.com/mobian1/callaudiod/-/archive/$version/$archive" -o "$src/$archive"
fi
[ "$(sha256sum "$src/$archive" | cut -d' ' -f1)" = "$archive_sha256" ] || { echo "checksum differs: $archive" >&2; exit 1; }
install -m 0644 "$repo_root"/patches/callaudiod/*.patch "$src/"
install -m 0644 "$repo_root/packaging/rpm/callaudiod.spec" "$work/rpmbuild/SPECS/"
export LUMA_RPM_BUILDER_ARCHITECTURE=${LUMA_TARGET_ARCHITECTURE:-aarch64}
"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$work/rpmbuild" "$FEDORA_RPM_BUILD_CONTAINER_AARCH64" '
set -euo pipefail
dnf5 -y install rpm-build "dnf5-command(builddep)"
dnf5 -y builddep SPECS/callaudiod.spec
rpmbuild -ba --define "_topdir $PWD" SPECS/callaudiod.spec
'
mkdir "$work/output"
cp -a "$work/rpmbuild/RPMS" "$work/rpmbuild/SRPMS" "$work/output/"
(cd "$work/output"; find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS)
mv "$work/output" "$output"
rm -rf "$work"
printf 'built %s\n' "$output"
