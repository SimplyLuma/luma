#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build luma-fp6-carrier-adapter (noarch) from src/luma-fp6-carrier-adapter.
#
#   build-luma-fp6-carrier-adapter.sh [OUTPUT_DIR]

set -euo pipefail
umask 022
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
src=$repo_root/src/luma-fp6-carrier-adapter
output_dir=$(realpath -m "${1:-$repo_root/build/packages/luma-fp6-carrier-adapter}")
export LUMA_RPM_BUILDER_ARCHITECTURE=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$LUMA_RPM_BUILDER_ARCHITECTURE" in
  x86_64) builder_container=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) echo "error: unsupported build architecture" >&2; exit 1 ;;
esac
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
top=$work_dir/rpmbuild
mkdir -p "$top"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
for name in luma-fp6-carrier-pdc luma-fp6-carrier-pdc.service luma-fp6-primary-gw-session \
    luma-fp6-primary-gw-session.service luma-fp6-modem-store luma-fp6-modem-store.service luma-fp6-rmtfs \
    50-luma-fp6-modem-store.conf luma-fp6-modem-restart luma-fp6-modem-restart.service \
    luma-fp6-carrier-select luma-fp6-carrier-select.service luma-fp6-modem-state-notify \
    luma-fp6-modem-state-notify.service fp6-fresh-install-modem-efs.md carriers.toml; do
  install -m 0644 "$src/$name" "$top/SOURCES/$name"
done
for name in test_carrier_kernel.py test_primary_gw_parser.py test_carrier_lifecycle.py test_stock_catalog.py \
    stock-platform-catalog.txt stock-software-catalog.txt test_modem_store.py test_carrier_select.py; do
  install -m 0644 "$src/tests/$name" "$top/SOURCES/$name"
done
install -m 0644 "$repo_root/LICENSE.md" "$top/SOURCES/LICENSE.md"
install -m 0644 "$repo_root/packaging/rpm/luma-fp6-carrier-adapter.spec" "$top/SPECS/"
"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$top" "$builder_container" '
  set -euo pipefail
  dnf5 -q -y install bash python3-devel systemd rpm-build redhat-rpm-config diffutils util-linux python3
  rpmbuild -ba --define "_topdir $PWD" SPECS/luma-fp6-carrier-adapter.spec
'
rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/noarch" "$output_dir/SRPMS"
install -m 0644 "$top"/RPMS/noarch/*.rpm "$output_dir/RPMS/noarch/"
install -m 0644 "$top"/SRPMS/*.rpm "$output_dir/SRPMS/"
printf 'FP6 carrier adapter package: %s\n' "$output_dir"
