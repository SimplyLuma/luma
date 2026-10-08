#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Generate rpm-md metadata over the pinned Luma desktop package set.
#
# The composing guest installs Luma packages from this repository instead of
# from local file paths, so the composed base commit records where they came
# from. Without that origin rpm-ostree cannot resolve the base side of a
# replacement, and `rpm-ostree override replace` of any Luma package reports
# success and is silently dropped. See docs/design/luma-package-repository.md.
#
# The package set is exactly config/desktop/packages.txt. That file is the
# single pin list for the desktop composition; this script never invents a
# second one and refuses to run if any pinned RPM is missing from build/.

set -euo pipefail

if [ "$#" -gt 1 ]; then
  printf 'usage: %s [/absolute/path/to/repository]\n' "$0" >&2
  exit 2
fi

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

build_root="$repo_root/build"
package_list="$repo_root/config/desktop/packages.txt"
package_root="$build_root/packages"
output_dir=${1:-$build_root/luma-package-repository}
case "$output_dir" in
  /*) : ;;
  *) output_dir="$PWD/$output_dir" ;;
esac

for tool in find install mktemp sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required Luma repository tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

# The createrepo_c fallback runs in the shared RPM builder, which mounts only
# build/. Keep the repository beneath it so both paths behave identically.
case "$output_dir/" in
  "$build_root"/*) : ;;
  *)
    printf 'error: Luma repository must be beneath %s\n' "$build_root" >&2
    exit 1
    ;;
esac

[ -f "$package_list" ] || {
  printf 'error: desktop package pin list is missing: %s\n' "$package_list" >&2
  exit 1
}
[ -d "$package_root" ] || {
  printf 'error: no built packages to publish: %s\n' "$package_root" >&2
  exit 1
}

mapfile -t pinned_nevras < <(
  sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$package_list"
)
if [ "${#pinned_nevras[@]}" -eq 0 ]; then
  printf 'error: desktop package pin list is empty: %s\n' "$package_list" >&2
  exit 1
fi

rm -rf -- "$output_dir"
install -d -m 0755 "$output_dir/Packages"

missing=0
for nevra in "${pinned_nevras[@]}"; do
  # A package builder leaves its scratch rpmbuild tree beside the published
  # output. Publish only the stable artifact, never a `.work.` leftover.
  mapfile -t candidates < <(
    find "$package_root" -type f -name "$nevra.rpm" ! -path '*.work*' ! -path '*/work.*' |
      LC_ALL=C sort
  )
  if [ "${#candidates[@]}" -eq 0 ]; then
    printf 'error: pinned package has not been built: %s\n' "$nevra" >&2
    missing=1
    continue
  fi
  # Several builders publish the same NEVRA into more than one output tree.
  # Accept that only while the bytes agree, so the repository cannot silently
  # pick one of two different packages with the same name.
  mapfile -t digests < <(
    sha256sum "${candidates[@]}" | awk '{print $1}' | LC_ALL=C sort -u
  )
  if [ "${#digests[@]}" -ne 1 ]; then
    printf 'error: pinned package %s has divergent copies:\n' "$nevra" >&2
    printf '  %s\n' "${candidates[@]}" >&2
    missing=1
    continue
  fi
  install -m 0644 "${candidates[0]}" "$output_dir/Packages/$nevra.rpm"
done
if [ "$missing" -ne 0 ]; then
  printf 'error: refusing to publish an incomplete Luma repository\n' >&2
  exit 1
fi

if command -v createrepo_c >/dev/null 2>&1; then
  createrepo_c --no-database "$output_dir"
else
  # createrepo_c is not part of the build host's contract. Generate the
  # metadata in the same pinned Fedora container the package builders use.
  "$repo_root/scripts/packages/run-in-rpm-builder.sh" \
    "$output_dir" "$FEDORA_RPM_BUILD_CONTAINER" '
    set -euo pipefail
    dnf5 -y install createrepo_c
    createrepo_c --no-database "$PWD"
    '
fi

# A directory that has been through a podman `:z` mount carries
# container_file_t. A confined rpm-ostreed cannot read that label, and the
# failure is a depsolve error that names the package rather than the
# repository, so relabel the repository back to its policy default here.
# -F is required: container_file_t is a customizable type, and a plain
# restorecon leaves customizable types exactly as it found them.
if command -v restorecon >/dev/null 2>&1; then
  restorecon -RF "$output_dir" >/dev/null 2>&1 || true
fi

[ -f "$output_dir/repodata/repomd.xml" ] || {
  printf 'error: repository metadata was not generated: %s\n' "$output_dir" >&2
  exit 1
}

printf 'Luma package repository: %s (%d packages)\n' \
  "$output_dir" "${#pinned_nevras[@]}"
