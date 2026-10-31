#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build both network helpers from the same verified vendored source on either CPU.
set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo_root/config/desktop/inputs.env"
architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in
  x86_64) builder=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) printf 'error: unsupported Messages bridge architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture
output_dir="$repo_root/build/packages/luma-messages-bridges"
mkdir -p "$output_dir"
work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
mkdir -p "$rpmbuild_dir/source-repo/src" "$rpmbuild_dir/source-repo/scripts/packages"
cp -R "$repo_root/src/luma-messages-bridges" "$rpmbuild_dir/source-repo/src/"
install -m 0755 "$repo_root/scripts/packages/build-luma-messages-bridges-source.sh" \
  "$rpmbuild_dir/source-repo/scripts/packages/"
install -m 0644 "$repo_root/packaging/rpm/luma-messages-bridges.spec" "$rpmbuild_dir/SPECS/"
"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$rpmbuild_dir" "$builder" '
  set -euo pipefail
  dnf5 -q -y install golang python3 rpm-build
  source-repo/scripts/packages/build-luma-messages-bridges-source.sh "$PWD/SOURCES/luma-messages-bridges.tar.gz"
  rpmbuild -ba --define "_topdir $PWD" SPECS/luma-messages-bridges.spec
'
expected="luma-messages-bridges-0.1.0-0.11.experiment.fc44"
rpm="$rpmbuild_dir/RPMS/$architecture/$expected.$architecture.rpm"
srpm="$rpmbuild_dir/SRPMS/$expected.src.rpm"
test -f "$rpm" && test -f "$srpm"
test "$(rpm -qp --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}' "$rpm")" = "$expected.$architecture"
install -d "$output_dir/RPMS/$architecture" "$output_dir/SRPMS"
for artifact in "$rpm" "$srpm"; do
  case "$artifact" in
    *.src.rpm) destination="$output_dir/SRPMS/$(basename "$artifact")" ;;
    *) destination="$output_dir/RPMS/$architecture/$(basename "$artifact")" ;;
  esac
  if test -e "$destination"; then cmp "$artifact" "$destination"; else install -m 0644 "$artifact" "$destination"; fi
done
(cd "$output_dir" && find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS)
printf 'Messages bridge packages: %s\n' "$output_dir"
