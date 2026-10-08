#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build Ari's model runtime (llama.cpp llama-server with Vulkan and CPU
# backends) from the pinned upstream release.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in curl mktemp podman sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required Ari runtime build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in
  x86_64) builder_container=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) printf 'error: unsupported Ari runtime architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture

version=$LUMA_ARI_RUNTIME_LLAMA_CPP_VERSION
tarball="llama.cpp-$version.tar.gz"
cache_dir="$repo_root/build/sources"
mkdir -p "$cache_dir"
if ! printf '%s  %s\n' "$LUMA_ARI_RUNTIME_SOURCE_SHA256" "$cache_dir/$tarball" | sha256sum -c --status 2>/dev/null; then
  curl -fL --retry 3 -o "$cache_dir/$tarball.part" \
    "https://codeload.github.com/ggml-org/llama.cpp/tar.gz/refs/tags/v$version"
  printf '%s  %s\n' "$LUMA_ARI_RUNTIME_SOURCE_SHA256" "$cache_dir/$tarball.part" | sha256sum -c --status || {
    printf 'error: llama.cpp %s does not match the pinned checksum\n' "$version" >&2
    rm -f "$cache_dir/$tarball.part"
    exit 1
  }
  mv "$cache_dir/$tarball.part" "$cache_dir/$tarball"
fi

output_dir="$repo_root/build/packages/luma-ari-runtime/$architecture"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
install -m 0644 "$cache_dir/$tarball" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/packaging/rpm/luma-ari-runtime.spec" "$rpmbuild_dir/SPECS/"

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$builder_container" "
    set -euo pipefail
    dnf5 -q -y install rpm-build cmake gcc-c++ ninja-build vulkan-headers vulkan-loader-devel \
      glslc glslang spirv-headers-devel openssl-devel chrpath
    rpmbuild -ba --define \"_topdir \$PWD\" SPECS/luma-ari-runtime.spec
  "

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/*/*.rpm "$output_dir/RPMS/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Ari runtime packages: %s\n' "$output_dir"
