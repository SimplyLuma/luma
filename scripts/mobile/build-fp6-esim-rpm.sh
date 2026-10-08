#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build lpac in an isolated native Fedora 44 AArch64 environment. This script
# refuses to build on the phone and never accesses or mutates an eUICC.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
output_dir=${1:-$repo_root/build/mobile/fp6-esim/lpac}
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-esim.env"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "$(uname -s)" = Linux ] || die 'build requires Linux'
[ "$(uname -m)" = aarch64 ] || die 'build requires native AArch64'
# shellcheck disable=SC1091
. /etc/os-release
[ "${ID:-}:${VERSION_ID:-}" = fedora:44 ] || die 'build requires Fedora 44'
if [ -r /sys/firmware/devicetree/base/model ]; then
  model=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$model" in *Fairphone*) die 'refuse to compile on the FP6 target' ;; esac
fi

for command in cmake git gzip rpm rpmbuild sha256sum; do
  command -v "$command" >/dev/null || die "missing build command: $command"
done
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

spec_file=$repo_root/packaging/rpm/lpac-fp6.spec
patch_files=(
  "$repo_root/patches/lpac/0001-profile-download-read-activation-code-from-stdin.patch"
  "$repo_root/patches/lpac/0002-profile-enable-read-identifier-from-stdin.patch"
  "$repo_root/patches/lpac/0003-profile-enable-accept-19-digit-iccid.patch"
)
[ -f "$spec_file" ] || die "missing spec: $spec_file"
for patch_file in "${patch_files[@]}"; do
  [ -f "$patch_file" ] || die "missing patch: $patch_file"
done

install -d -m 0700 "$output_dir"
topdir=$output_dir/rpmbuild
source_tree=$output_dir/source
install -d -m 0700 "$topdir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

git clone --filter=blob:none --no-checkout "$FP6_ESIM_LPAC_REPO" "$source_tree"
git -C "$source_tree" checkout --detach "$FP6_ESIM_LPAC_COMMIT"
[ "$(git -C "$source_tree" rev-parse HEAD)" = "$FP6_ESIM_LPAC_COMMIT" ] ||
  die 'source checkout differs from the pin'

archive=lpac-$FP6_ESIM_LPAC_COMMIT.tar.gz
git -C "$source_tree" archive --format=tar \
  --prefix="lpac-$FP6_ESIM_LPAC_COMMIT/" "$FP6_ESIM_LPAC_COMMIT" |
  gzip -n >"$topdir/SOURCES/$archive"
install -m 0644 "$spec_file" "$topdir/SPECS/"
for patch_file in "${patch_files[@]}"; do
  install -m 0644 "$patch_file" "$topdir/SOURCES/"
done

rpmbuild -ba --define "_topdir $topdir" "$topdir/SPECS/$(basename "$spec_file")"

mapfile -t binary_rpms < <(find "$topdir/RPMS" -type f -name '*.rpm' | sort)
mapfile -t source_rpms < <(find "$topdir/SRPMS" -type f -name '*.src.rpm' | sort)
[ "${#binary_rpms[@]}" -eq 1 ] || die 'expected exactly one binary RPM'
[ "${#source_rpms[@]}" -eq 1 ] || die 'expected exactly one source RPM'

rpm -qp --qf '%{NAME} %{VERSION} %{RELEASE} %{ARCH}\n' "${binary_rpms[0]}"
rpm -qpl "${binary_rpms[0]}" | grep -qx '/usr/lib64/lpac/driver/driver_apdu_qmi_qrtr.so' ||
  die 'binary RPM lacks the QRTR APDU driver'

{
  printf 'LUMA_FP6_ESIM_BUILD_VERSION=1\n'
  printf 'SOURCE_REPOSITORY=%s\n' "$FP6_ESIM_LPAC_REPO"
  printf 'SOURCE_COMMIT=%s\n' "$FP6_ESIM_LPAC_COMMIT"
  printf 'SOURCE_ARCHIVE_SHA256=%s\n' "$(sha256sum "$topdir/SOURCES/$archive" | awk '{print $1}')"
  for patch_file in "${patch_files[@]}"; do
    printf 'PATCH_%s_SHA256=%s\n' \
      "$(basename "$patch_file" | tr '[:lower:].-' '[:upper:]__')" \
      "$(sha256sum "$patch_file" | awk '{print $1}')"
  done
  printf 'SPEC_SHA256=%s\n' "$(sha256sum "$spec_file" | awk '{print $1}')"
  printf 'BINARY_RPM_SHA256=%s\n' "$(sha256sum "${binary_rpms[0]}" | awk '{print $1}')"
  printf 'SOURCE_RPM_SHA256=%s\n' "$(sha256sum "${source_rpms[0]}" | awk '{print $1}')"
  printf 'PHONE_ACCESSED=false\n'
  printf 'EUICC_ACCESSED=false\n'
  printf 'PACKAGE_INSTALLED=false\n'
} >"$output_dir/manifest.env"
chmod 0600 "$output_dir/manifest.env"

printf 'Offline FP6 eSIM RPM build complete: %s\n' "$output_dir"
printf 'No phone or eUICC was accessed.\n'
