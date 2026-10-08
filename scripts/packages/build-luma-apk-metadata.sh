#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
source "$repo_root/config/desktop/inputs.env"
architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in
  x86_64) image=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) image=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) echo 'unsupported metadata builder architecture' >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture
work="$repo_root/build/packages/luma-apk-metadata.$architecture.work"
mkdir -p "$work"/{SOURCES,SPECS,BUILD,BUILDROOT,RPMS,SRPMS}
mkdir -p "$work/luma-apk-metadata"
cp -R "$repo_root/src/luma-installer/apk-metadata/." "$work/luma-apk-metadata/"
cp "$repo_root/packaging/rpm/luma-apk-metadata.spec" "$work/SPECS/"
cp "$repo_root/LICENSE.md" "$work/SOURCES/"
"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$work" "$image" '
 set -euo pipefail
 dnf5 -y install cargo rust gcc rpm-build python3
 cd luma-apk-metadata
 mkdir -p .cargo
 cargo vendor --locked vendor > .cargo/config.toml
 python3 collect-licenses.py
 cd ..
 tar --exclude=target -czf SOURCES/luma-apk-metadata.tar.gz luma-apk-metadata
 rpmbuild -ba --define "_topdir $PWD" SPECS/luma-apk-metadata.spec
'
output="$repo_root/build/packages/luma-apk-metadata/$architecture"
mkdir -p "$output"
cp "$work/RPMS/$architecture/"*.rpm "$output/"
cp "$work/SRPMS/"*.rpm "$output/"
sha256sum "$output/"*.rpm
