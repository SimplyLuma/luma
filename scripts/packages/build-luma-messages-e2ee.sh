#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Build the existing ADR-051 helper from locked, vendored source.
set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo_root/config/desktop/inputs.env"
architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in
  x86_64) builder_container=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) printf 'error: unsupported Messages helper architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture
output_dir="$repo_root/build/packages/luma-messages-e2ee"
mkdir -p "$output_dir"
work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
mkdir -p "$rpmbuild_dir/source-repo/src" "$rpmbuild_dir/source-repo/scripts/packages"
cp -R "$repo_root/src/luma-messages-e2ee" "$rpmbuild_dir/source-repo/src/"
install -m 0755 "$repo_root/scripts/packages/build-luma-messages-e2ee-source.sh" \
  "$rpmbuild_dir/source-repo/scripts/packages/"
install -m 0644 "$repo_root/packaging/rpm/luma-messages-e2ee.spec" "$rpmbuild_dir/SPECS/"
"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$rpmbuild_dir" "$builder_container" '
  set -euo pipefail
  dnf5 -q -y install cargo rust gcc openssl-devel python3 rpm-build
  source-repo/scripts/packages/build-luma-messages-e2ee-source.sh "$PWD/SOURCES/luma-messages-e2ee.tar.gz"
  rpmbuild -ba --define "_topdir $PWD" SPECS/luma-messages-e2ee.spec
'
expected="$LUMA_MESSAGES_E2EE_VERSION-$LUMA_MESSAGES_E2EE_RELEASE.fc44.$architecture"
rpm="$rpmbuild_dir/RPMS/$architecture/luma-messages-e2ee-$expected.rpm"
srpm="$rpmbuild_dir/SRPMS/luma-messages-e2ee-$LUMA_MESSAGES_E2EE_VERSION-$LUMA_MESSAGES_E2EE_RELEASE.fc44.src.rpm"
test -f "$rpm" && test -f "$srpm" || { printf 'error: expected Messages helper artifacts are missing\n' >&2; exit 1; }
test "$(rpm -qp --qf '%{VERSION}-%{RELEASE}.%{ARCH}' "$rpm")" = "$expected" || {
  printf 'error: Messages helper RPM header differs from its admitted pin\n' >&2; exit 1;
}
install -d "$output_dir/RPMS/$architecture" "$output_dir/SRPMS"
for artifact in "$rpm" "$srpm"; do
  case "$artifact" in
    *.src.rpm) destination="$output_dir/SRPMS/$(basename "$artifact")" ;;
    *) destination="$output_dir/RPMS/$architecture/$(basename "$artifact")" ;;
  esac
  if test -e "$destination"; then
    cmp -s "$artifact" "$destination" || { printf 'error: refusing to replace existing artifact identity\n' >&2; exit 1; }
  else
    install -m 0644 "$artifact" "$destination"
  fi
done
(cd "$output_dir" && find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS)
printf 'Messages helper packages: %s\n' "$output_dir"
