#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build the two private Connect dependencies required by the Prairie RPM gate.
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo_root/config/desktop/inputs.env"
printf '%s  %s\n' \
  d5ff536e658e17664f8c1b1ab60dc4aa62aa973fcef1edd33cc44bda45d6f5ea \
  "$repo_root/build/cache/sources/joserfc-1.7.5.tar.gz" \
  939d7d4c1ea10e3747b03cc76c478f5158657a32645a496be38100c927ee6a4c \
  "$repo_root/build/cache/sources/authlib-v1.7.2.tar.gz" | sha256sum --check --status
work_dir="$repo_root/build/packages/prairie-python-deps.work"
rm -rf "$work_dir"
trap 'rm -rf "$work_dir"' EXIT INT TERM
mkdir -p "$work_dir/rpmbuild"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
source_dir="$work_dir/rpmbuild/SOURCES"
install -m 0644 "$repo_root/build/cache/sources/joserfc-1.7.5.tar.gz" "$source_dir/"
install -m 0644 "$repo_root/build/cache/sources/authlib-v1.7.2.tar.gz" \
  "$source_dir/authlib-v1.7.2-upstream.tar.gz"
install -m 0644 "$repo_root/packaging/rpm/python-joserfc.spec" \
  "$repo_root/packaging/rpm/python-authlib.spec" "$work_dir/rpmbuild/SPECS/"

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$work_dir/rpmbuild" "$FEDORA_RPM_BUILD_CONTAINER" '
    set -euo pipefail
    dnf5 -q -y install rpm-build python3-devel pyproject-rpm-macros \
      python3-setuptools python3-wheel python3-pytest python3-cryptography \
      python3-httpx python3-requests python3-werkzeug
    rpmbuild -ba --define "_topdir $PWD" SPECS/python-joserfc.spec
    dnf5 -q -y install RPMS/noarch/python3-joserfc-*.rpm
    rpmbuild -ba --define "_topdir $PWD" SPECS/python-authlib.spec
  '

for package in python-joserfc python-authlib; do
  destination="$repo_root/build/packages/$package/RPMS/noarch"
  install -d "$destination"
  install -m 0644 "$work_dir/rpmbuild/RPMS/noarch/python3-${package#python-}-"*.rpm "$destination/"
done
printf 'Built private Prairie Python dependencies.\n'
