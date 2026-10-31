#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build flatpak-runtime-config, the one package a Flatpak runtime needs that
# Fedora publishes only in its Koji Flatpak tags, not in the Everything
# repository. The spec is Fedora's (packaging/flatpak/runtime/flatpak-runtime-config/)
# with Luma's component data. Output: build/packages/flatpak-runtime-config/RPMS/<arch>.
set -euo pipefail
. "$(dirname -- "$0")/lib.sh"
. "$depot_repo_root/config/desktop/inputs.env"

case "$DEPOT_ARCH" in
  x86_64) builder=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) depot_die "unsupported runtime config architecture: $DEPOT_ARCH" ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$DEPOT_ARCH

source_dir="$depot_repo_root/packaging/flatpak/runtime/flatpak-runtime-config"
output_dir="$depot_repo_root/build/packages/flatpak-runtime-config"
rm -rf "$output_dir"
rpmbuild_dir="$output_dir/rpmbuild"
install -d -m 0755 "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
install -m 0644 "$source_dir/flatpak-runtime-config.spec" "$rpmbuild_dir/SPECS/"
find "$source_dir" -maxdepth 1 -type f ! -name '*.spec' -exec install -m 0644 -t "$rpmbuild_dir/SOURCES" {} +

"$depot_repo_root/scripts/packages/run-in-rpm-builder.sh" "$rpmbuild_dir" \
  "$builder" '
    set -euo pipefail
    dnf5 -q -y install rpm-build python3 python3-rpm-macros >/dev/null
    rpmbuild -bb --define "_topdir $PWD" SPECS/flatpak-runtime-config.spec
  '
mv "$rpmbuild_dir/RPMS" "$output_dir/RPMS"
rm -rf "$rpmbuild_dir"
find "$output_dir/RPMS" -name "*.rpm"
