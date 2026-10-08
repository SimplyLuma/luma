#!/usr/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Image build step 3: identity and boot artifacts.
#
# os-release names the release the way people see it (ADR-040): "Luma
# (Prairie, Beta 0, Nightly 20260916)", rendered from config/os/release.env and
# this build's channel and nightly date. The ordered channel version
# (1.0.0-nightly.YYYYMMDD.N, 1.0.0-beta.N, 1.0.0) stays the OSTree commit's
# `version` metadata, which rpm-ostree and luma-update compare.

set -euo pipefail

src=/run/luma/source

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

: "${LUMA_BUILD_ID:?}" "${LUMA_OS_VERSION:?}" "${LUMA_BUILD_DATE:?}" "${LUMA_OS_CHANNEL:?}"
[[ "$LUMA_BUILD_ID" =~ ^[0-9]{8}\.[0-9]+$ ]] || fail "invalid build id: $LUMA_BUILD_ID"
[ "$LUMA_OS_CHANNEL" != nightly ] || [[ "${LUMA_NIGHTLY_DATE:-}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] ||
  fail "a nightly needs its nightly date (LUMA_NIGHTLY_DATE=YYYY-MM-DD), not '${LUMA_NIGHTLY_DATE:-}'"

contract() {
  sed -n "s/^$1=//p" "$src/config/os/release.env"
}
fedora_release=$(contract LUMA_OS_FEDORA_RELEASE)
build_day=${LUMA_BUILD_ID%%.*}
build_day_iso="${build_day:0:4}-${build_day:4:2}-${build_day:6:2}"

python3 "$src/scripts/os/lib/os_release.py" \
  --channel "$LUMA_OS_CHANNEL" \
  --build-id "$LUMA_BUILD_ID" \
  --nightly-date "${LUMA_NIGHTLY_DATE:-}" \
  --build-date "$build_day_iso" >/usr/lib/os-release.luma
mv /usr/lib/os-release.luma /usr/lib/os-release
chmod 0644 /usr/lib/os-release
[ "$(readlink /etc/os-release)" = ../usr/lib/os-release ] ||
  ln -sfn ../usr/lib/os-release /etc/os-release
# /etc/system-release is what tools that predate os-release print as the
# system's name. It names Luma (ADR-040) in the "<name> release <version>"
# form those tools and bootupd parse: "Luma release 1 (Prairie, Beta 0,
# Nightly 20260916)". /etc/fedora-release and /etc/redhat-release keep
# Fedora's text for tooling that asks for them by name.
. /usr/lib/os-release
printf '%s release %s (%s)\n' "$NAME" "$VERSION_ID" "$VERSION" >/usr/lib/luma-release
chmod 0644 /usr/lib/luma-release
ln -sfn ../usr/lib/luma-release /etc/system-release
# bootupd uses system-release for a normal firmware entry, but shim's recovery
# path gets its label from its UTF-16 CSV. Brand that metadata before bootupd
# copies it to the ESP; retain Fedora's signed executables and vendor paths.
/usr/libexec/luma-prepare-efi-identity /
# Packages the image removes can leave their directories behind, empty and
# under Fedora's names (/usr/share/backgrounds/fedora-workstation). Nothing
# shows an empty directory, but an image carries no Fedora-named artwork
# locations (ADR-040).
find /usr/share/backgrounds /usr/share/gnome-background-properties /usr/share/pixmaps \
  /usr/share/icons /usr/share/plymouth -depth -type d -empty \
  \( -iname '*fedora*' -o -iname '*silverblue*' \) -delete 2>/dev/null || :
# Package layering resolves $releasever from VERSION_ID unless a variable
# file sets it; with Luma's VERSION_ID every Fedora repository URL and key
# path would name release 1 and `rpm-ostree install` would fail. The base
# release is pinned for libdnf (rpm-ostree) and dnf5 alike.
install -d -m 0755 /etc/dnf/vars
printf '%s\n' "$fedora_release" >/etc/dnf/vars/releasever
chmod 0644 /etc/dnf/vars/releasever
# rpm-ostree and PackageKit (libdnf) ignore that file and take $releasever
# from the rpmdb's system-release(releasever) provide, then os-release
# VERSION_ID; luma-release carries the provide (dnf5 honours it as well).
provided=$(rpm -q --whatprovides 'system-release(releasever)' --provides 2>/dev/null |
  sed -n 's/^system-release(releasever) = //p' | sort -u)
[ "$provided" = "$fedora_release" ] ||
  fail "package tooling would resolve \$releasever to '${provided:-VERSION_ID}', not $fedora_release (luma-release missing?)"

# The initramfs carries the Plymouth theme and plugins, which the package step
# replaced. Regenerate it for the image's one kernel, the way Fedora's bootc
# images build theirs: no host-only content, reproducible, with OSTree support.
mapfile -t kernels < <(find /usr/lib/modules -mindepth 1 -maxdepth 1 -type d -printf '%f\n')
[ "${#kernels[@]}" -eq 1 ] || fail "expected one kernel in the image, found ${#kernels[@]}"
kver=${kernels[0]}
# /root is a link to /var/roothome, which exists only on a booted system.
created_roothome=0
if [ -L /root ] && [ ! -e /root ]; then
  install -d -m 0700 /var/roothome
  created_roothome=1
fi
DRACUT_NO_XATTR=1 dracut --no-hostonly --reproducible --zstd --force \
  --add ostree --kver "$kver" "/usr/lib/modules/$kver/initramfs.img"
chmod 0644 "/usr/lib/modules/$kver/initramfs.img"
[ "$created_roothome" -eq 0 ] || rm -rf /var/roothome
# List to a file first: grep -q closing a pipe under pipefail would fail the
# check with SIGPIPE even when the theme is there.
lsinitrd "/usr/lib/modules/$kver/initramfs.img" >/tmp/initramfs-listing.txt 2>&1 || :
# The theme Plymouth will use: /etc/plymouth/plymouthd.conf if it sets one,
# otherwise Luma's plymouthd.defaults in /usr (ADR-045).
theme=$(plymouth-set-default-theme)
[ -n "$theme" ] || fail 'no Plymouth theme is selected'
[ "$theme" = luma-loading ] ||
  fail "the image selects the Plymouth theme '$theme', not luma-loading"
grep -q 'usr/share/plymouth/plymouthd.defaults' /tmp/initramfs-listing.txt ||
  fail 'the regenerated initramfs does not carry plymouthd.defaults'
grep -q 'usr/lib64/plymouth/script.so' /tmp/initramfs-listing.txt ||
  fail 'the regenerated initramfs does not carry the script plugin the Luma splash needs'
grep -q "usr/share/plymouth/themes/$theme/$theme.plymouth" /tmp/initramfs-listing.txt ||
  fail "the regenerated initramfs does not carry the selected Luma boot theme ($theme)"

# rpm-ostree treats /usr/lib/sysimage/rpm-ostree-base-db as the list of base
# packages (what `rpm-ostree override remove/replace` can act on). The base
# image's copy still describes Fedora's package set, so every package this
# image installed or replaced was "not in the base" and overrides of Luma
# packages were recorded as inactive and did nothing. It is refreshed from
# the image's final database, as rpm-ostree's own compose does.
base_db=/usr/lib/sysimage/rpm-ostree-base-db
rpmdb=$(rpm --eval '%{_dbpath}')
[ -s "$rpmdb/rpmdb.sqlite" ] || fail "no rpm database at $rpmdb"
rm -rf "$base_db"
install -d -m 0755 "$base_db"
cp -a "$rpmdb"/rpmdb.sqlite* "$base_db/"
[ "$(rpm -qa --dbpath "$base_db" | wc -l)" -eq "$(rpm -qa | wc -l)" ] ||
  fail 'the rpm-ostree base database does not list the image packages'
rm -f "$rpmdb/.rpm.lock"

# A bootable container carries no state in /var beyond what tmpfiles creates:
# drop caches and logs, then declare everything packages put in /var the way
# rpm-ostree does, so a deployed system gets it. /run is runtime-only.
rm -rf /var/cache/libdnf5 /var/lib/dnf /var/log/dnf5.log* /var/log/hawkey.log 2>/dev/null || :
python3 "$src/image/luma-desktop/scripts/var-to-tmpfiles.py"
find /var/cache /var/log /var/tmp -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + 2>/dev/null || :
rm -rf /run/dnf /run/selinux-policy 2>/dev/null || :
printf 'Finalize step complete: %s (build %s, kernel %s)\n' \
  "$LUMA_OS_VERSION" "$LUMA_BUILD_ID" "$kver"
