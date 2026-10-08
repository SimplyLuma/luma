#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

if [ "${LUMA_MOD_RECOVERY_DRILL:-0}" != 1 ]; then
  printf 'error: set LUMA_MOD_RECOVERY_DRILL=1 for this test-only payload\n' >&2
  exit 1
fi

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in git podman sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required recovery-drill build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = x86_64 ] || {
  printf 'error: build the recovery drill on the Linux/x86_64 lab builder\n' >&2
  exit 1
}

output_dir="$repo_root/build/tests/luma-mod-recovery-drill"
work_dir="$output_dir.work"
rpmbuild_dir="$work_dir/rpmbuild"
rm -rf "$work_dir"
install -d -m 0755 "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
trap 'rm -rf "$work_dir"' EXIT

source_date_epoch=$(git -C "$repo_root" log -1 --format=%ct -- \
  tests/fixtures/mods/recovery-drill \
  packaging/rpm/luma-mod-recovery-drill.spec \
  scripts/packages/build-luma-mod-recovery-drill.sh \
  LICENSE.md)
case "$source_date_epoch" in
  ''|*[!0-9]*)
    printf 'error: could not derive a source epoch from repository HEAD\n' >&2
    exit 1
    ;;
esac

install -m 0644 \
  "$repo_root/tests/fixtures/mods/recovery-drill/90-luma-recovery-drill.conf" \
  "$rpmbuild_dir/SOURCES/90-luma-recovery-drill.conf"
install -m 0644 "$repo_root/LICENSE.md" "$rpmbuild_dir/SOURCES/LICENSE.md"
install -m 0644 "$repo_root/packaging/rpm/luma-mod-recovery-drill.spec" \
  "$rpmbuild_dir/SPECS/"
touch -d "@$source_date_epoch" "$rpmbuild_dir"/SOURCES/* "$rpmbuild_dir"/SPECS/*
chmod -R a+rX "$rpmbuild_dir"

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$FEDORA_RPM_BUILD_CONTAINER" '
    set -euo pipefail
    dnf5 -y install rpm-build systemd-rpm-macros
    rpmbuild -ba --define "_topdir $PWD" SPECS/luma-mod-recovery-drill.spec
    rpm_path=$(find RPMS/noarch -name "luma-mod-recovery-drill-*.rpm" -print -quit)
    test -n "$rpm_path"
    rpm -qpl "$rpm_path" | grep -Fxq \
      /usr/lib/systemd/system/luma-mod-boot-promote.service.d/90-luma-recovery-drill.conf
  '

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/noarch" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/noarch/*.rpm "$output_dir/RPMS/noarch/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Luma Mods recovery drill package: %s\n' "$output_dir"
