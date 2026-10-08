#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in mktemp podman sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required luma-update package build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  printf 'error: build luma-update on the canonical Linux/x86_64 builder\n' >&2
  exit 1
fi

version=$(sed -n 's/^Version:[[:space:]]*//p' "$repo_root/packaging/rpm/luma-update.spec")
output_dir="$repo_root/build/packages/luma-update"
mkdir -p "$(dirname -- "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

# A reproducible source archive: fixed order, owner and mtime; no caches.
COPYFILE_DISABLE=1 tar --sort=name --owner=0 --group=0 --numeric-owner \
  --mtime='2026-09-15 00:00:00Z' --exclude='__pycache__' --exclude='*.pyc' --exclude='._*' \
  --transform "s,^src/luma-update,luma-update-$version," \
  -C "$repo_root" -czf "$rpmbuild_dir/SOURCES/luma-update-$version.tar.gz" src/luma-update
install -m 0644 "$repo_root/LICENSE.md" "$rpmbuild_dir/SOURCES/LICENSE.md"
# luma-os-remote: the image's own remote definition and the public keys, from their one source.
install -m 0644 "$repo_root/image/luma-desktop/rootfs/etc/ostree/remotes.d/luma.conf" "$rpmbuild_dir/SOURCES/luma.conf"
install -m 0644 "$repo_root/image/luma-desktop/rootfs/etc/luma/update-mirrorlist" "$rpmbuild_dir/SOURCES/update-mirrorlist"
install -m 0644 "$repo_root/config/os/keys/luma-os-release.asc" "$rpmbuild_dir/SOURCES/luma-os-release.asc"
install -m 0644 "$repo_root/config/os/keys/luma-update-graph.pub" "$rpmbuild_dir/SOURCES/luma-update-graph.pub"
install -m 0644 "$repo_root/packaging/rpm/luma-update.spec" "$rpmbuild_dir/SPECS/"

# The single-quoted payload expands only inside the Fedora builder.
# shellcheck disable=SC2016
"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$FEDORA_RPM_BUILD_CONTAINER" '
    set -euo pipefail
    dnf5 -y install rpm-build systemd-rpm-macros python3-devel python3-gobject-base desktop-file-utils libxml2 gnupg2
    rpmbuild -ba --define "_topdir $PWD" SPECS/luma-update.spec
    rpm=$(find RPMS/noarch -name "luma-update-1*.rpm" -print -quit)
    remote_rpm=$(find RPMS/noarch -name "luma-os-remote-*.rpm" -print -quit)
    rpm -qp --requires "$rpm" | grep -Fq "luma-os-remote"
    rpm -qplv "$remote_rpm" | grep -E "^-rw------- .* root +root .* /etc/luma/update-mirrorlist$"
    rpm -qpl "$remote_rpm" | grep -Fxq /etc/ostree/remotes.d/luma.conf
    rpm -qpl "$remote_rpm" | grep -Fxq /etc/pki/ostree/luma-release.gpg
    rpm -qpl "$remote_rpm" | grep -Fxq /usr/lib/luma-update/graph-keys.d/luma-update-graph.pub
    verify=$(mktemp -d)
    cd "$verify"
    rpm2cpio "$OLDPWD/$rpm" >payload.cpio
    cpio -idm --quiet <payload.cpio
    rm payload.cpio
    test -x usr/libexec/luma-updated
    test -x usr/bin/luma-update
    find usr/lib/python3*/site-packages/luma_update -name bootmenu.py | grep -q .
    test -f usr/lib/systemd/system/luma-updated.timer
    test -f usr/share/dbus-1/system.d/org.projectluma.Update1.conf
    test -x usr/lib/greenboot/check/required.d/43-luma-updated.sh
    desktop-file-validate usr/share/applications/org.projectluma.Update.desktop
    xmllint --noout usr/share/dbus-1/interfaces/org.projectluma.Update1.xml \
      usr/share/polkit-1/actions/org.projectluma.update.policy \
      usr/share/dbus-1/system.d/org.projectluma.Update1.conf
    for script in usr/lib/greenboot/check/required.d/*.sh usr/lib/greenboot/*/*.sh \
                  usr/lib/NetworkManager/dispatcher.d/90-luma-update usr/lib/luma-update/luma-greenboot-common.sh; do
      bash -n "$script"
    done
    PYTHONPATH=$(echo usr/lib/python3*/site-packages) python3 -c "import luma_update.daemon, luma_update.cli, luma_update.notifier, luma_update.boot, luma_update.redact, luma_update.names"
    # Review fixes that must be in the payload: Cancel on the bus, no implied logind action,
    # greenboot errors at error priority, logind inhibitor checks for Apply.
    grep -Fq "<method name=\"Cancel\"/>" usr/share/dbus-1/interfaces/org.projectluma.Update1.xml
    ! grep -Fq "policykit.imply" usr/share/polkit-1/actions/org.projectluma.update.policy
    grep -Fq "logger --priority user.err" usr/lib/luma-update/luma-greenboot-common.sh
    grep -Fq "RebootWithFlags" usr/lib/python3*/site-packages/luma_update/system.py
    grep -Fq "KernelArgs" usr/lib/python3*/site-packages/luma_update/rpmostree.py
    test -f "$(echo usr/lib/python3*/site-packages)/luma_update/kargs.py"
    grep -Fq "def revocable" usr/lib/python3*/site-packages/luma_update/preview.py
    grep -Fq "staff-media" usr/lib/python3*/site-packages/luma_update/preview.py
    # Release names (1.0.0-1.luma.12): the naming module and its published properties.
    grep -Fq "def booted_name" usr/lib/python3*/site-packages/luma_update/names.py
    grep -Fq "\"booted_name\": \"s\"" usr/lib/python3*/site-packages/luma_update/status.py
    # 1.0.0-1.luma.15: added packages the new base ships, stale credentials, errors from another system.
    grep -Fq "def added_packages_the_base_provides" usr/lib/python3*/site-packages/luma_update/engine.py
    grep -Fq "uninstall-packages" usr/lib/python3*/site-packages/luma_update/rpmostree.py
    # 1.0.0-1.luma.17: an added copy identical to the base goes, a newer one is kept.
    grep -Fq "def added_packages_in_conflict" usr/lib/python3*/site-packages/luma_update/engine.py
    grep -Fq "def rpmvercmp" usr/lib/python3*/site-packages/luma_update/rpmver.py
    grep -Fq '"added-package-newer"' usr/lib/python3*/site-packages/luma_update/engine.py
    grep -Fq "\"kept_packages\": \"as\"" usr/lib/python3*/site-packages/luma_update/status.py
    grep -Fq "def set_aside" usr/lib/python3*/site-packages/luma_update/preview.py
    grep -Fq "def _forget_another_systems_error" usr/lib/python3*/site-packages/luma_update/engine.py
    grep -Fq "\"removed_packages\": \"as\"" usr/lib/python3*/site-packages/luma_update/status.py
    # 1.0.0-1.luma.16: refusals are recognised inside the wrapped rpm-ostree transaction error.
    grep -Fq "wrapped = not error_class or error_class == \"transaction\"" usr/lib/python3*/site-packages/luma_update/engine.py
    rpm -qp --obsoletes "$OLDPWD/$rpm" | grep -Fq "luma-update-client"
    ! find usr/lib/systemd -type l -print -quit | grep -q .
  '

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/noarch" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/noarch/*.rpm "$output_dir/RPMS/noarch/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Luma update agent package: %s\n' "$output_dir"
