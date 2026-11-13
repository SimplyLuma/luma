#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build the Fairphone 6 kernel: the SimplyLuma/linux tag in
# devices/fairphone/fp6/device.toml [kernel] with its patches applied, its
# config with the config_fragments merged over it, cross-compiled as
# luma/fp6/alpha1/BUILD.md in that tag describes (clang/LLVM/LLD 22.1.8 and
# pahole 1.30 on Fedora 44).
#
# OUTPUT_DIR receives:
#   Image.gz  the kernel for the boot image (scripts/mobile/build-fp6-boot-image.sh --kernel)
#   dtb       the boot DTB: the built DTB with the tag's overlays
#             (scripts/kernel/compose-fp6-dtb.sh; build-fp6-boot-image.sh --dtb)
#   config    the merged configuration
#   android-gpu-admission.sha256
#             the kernel's Android GPU admission entry, empty unless
#             device.toml [kernel.android_gpu] admits these inputs
#             (scripts/kernel/fp6-android-gpu-admission.py); shipped by
#             kernel-modules-luma-fp6
#   modules/  stripped modules_install tree (scripts/packages/build-kernel-modules-luma-fp6.sh --tree)
#   obj/      the build tree; a second run with the same OUTPUT_DIR is incremental
#
# usage: build-fp6.sh [--source DIR] OUTPUT_DIR
#   --source  an unpacked tree of the tag (default: fetched and exported without
#             .git, so the kernel release carries no -g<commit> suffix); patches
#             it does not carry yet are applied to it
#
# JOBS (default 16), KBUILD_BUILD_VERSION and KBUILD_BUILD_TIMESTAMP override
# the build defaults below.
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
fail() { printf 'build-fp6: %s\n' "$*" >&2; exit 1; }

source_dir=""
while [ "$#" -gt 1 ]; do
  case "$1" in
    --source) source_dir=$(realpath -e "${2:?}"); shift 2 ;;
    *) sed -n 's/^# usage: /usage: /p' "$0" >&2; exit 2 ;;
  esac
done
[ "$#" -eq 1 ] || { sed -n 's/^# usage: /usage: /p' "$0" >&2; exit 2; }
# The admission record must match these inputs before anything is built.
python3 "$repo_root/scripts/kernel/fp6-android-gpu-admission.py" check || fail "restamp [kernel.android_gpu]"
mkdir -p "$1"
out=$(realpath -e "$1")

eval "$(python3 - "$repo_root/devices/fairphone/fp6/device.toml" <<'PY'
import shlex, sys, tomllib
k = tomllib.load(open(sys.argv[1], "rb"))["kernel"]
print(f"tag={shlex.quote(k['tag'])} release={shlex.quote(k['release'])} config={shlex.quote(k['config'])}")
print(f"dtb={shlex.quote(k['dtb'])} fragments=({' '.join(shlex.quote(f) for f in k.get('config_fragments', []))})")
print(f"patches=({' '.join(shlex.quote(f) for f in k.get('patches', []))})")
PY
)"

if [ -z "$source_dir" ]; then
  source_dir="$out/src"
  if [ ! -f "$source_dir/Makefile" ]; then
    rm -rf "$out/linux.git" "$source_dir"
    git clone -q --depth 1 -b "$tag" https://github.com/SimplyLuma/linux.git "$out/linux.git"
    mkdir "$source_dir"
    git -C "$out/linux.git" archive HEAD | tar -x -C "$source_dir"
    rm -rf "$out/linux.git"
  fi
fi
[ ! -e "$source_dir/.git" ] || fail "$source_dir is a git checkout; export it so the release has no -g<commit> suffix"
[ -f "$source_dir/$config" ] || fail "$source_dir has no $config (not the $tag tree)"
for p in "${patches[@]}"; do
  # Already applied when it reverses cleanly.
  patch -d "$source_dir" -p1 -R -f -s --dry-run <"$repo_root/$p" >/dev/null 2>&1 && continue
  patch -d "$source_dir" -p1 -N -s <"$repo_root/$p" || fail "$p does not apply to $source_dir"
done

obj="$out/obj"
mkdir -p "$obj"
cd "$source_dir"
version=$(make -s kernelversion)
localversion=${release#"$version"}
[ "$version$localversion" = "$release" ] || fail "kernel $version cannot have release $release"
K=(make O="$obj" ARCH=arm64 LLVM=1 LOCALVERSION="$localversion"
   KBUILD_BUILD_USER=luma KBUILD_BUILD_HOST=kernel-cross
   KBUILD_BUILD_VERSION="${KBUILD_BUILD_VERSION:-2}"
   KBUILD_BUILD_TIMESTAMP="${KBUILD_BUILD_TIMESTAMP:-2026-11-09 00:00:00 UTC}")

cp "$config" "$obj/.config.base"
cp "$obj/.config.base" "$obj/.config.new"
for fragment in "${fragments[@]}"; do
  scripts/kconfig/merge_config.sh -m -O "$obj" "$obj/.config.new" "$repo_root/$fragment" >/dev/null
  mv "$obj/.config" "$obj/.config.new"
done
mv "$obj/.config.new" "$obj/.config"
"${K[@]}" -s olddefconfig
# Every fragment assignment must survive olddefconfig (no unmet dependencies).
for fragment in "${fragments[@]}"; do
  grep -E '^(CONFIG_[A-Z0-9_]+=|# CONFIG_[A-Z0-9_]+ is not set)' "$repo_root/$fragment" | while IFS= read -r line; do
    grep -qxF -- "$line" "$obj/.config" || fail "$fragment: '$line' did not survive olddefconfig"
  done
done
[ "$("${K[@]}" -s kernelrelease)" = "$release" ] || fail "kernel release is not $release"

"${K[@]}" -j"${JOBS:-16}" vmlinuz.efi modules "$dtb"
rm -rf "$out/modules"
# Debug info stripped, as the Alpha 1 modules were; BTF stays.
"${K[@]}" INSTALL_MOD_STRIP=1 INSTALL_MOD_PATH="$out/modules" modules_install
rm -f "$out/modules/lib/modules/$release/build" "$out/modules/lib/modules/$release/source"
install -m 0644 "$obj/arch/arm64/boot/vmlinuz" "$out/Image.gz"
install -m 0644 "$obj/.config" "$out/config"
"$repo_root/scripts/kernel/compose-fp6-dtb.sh" "$source_dir" "$obj/arch/arm64/boot/dts/$dtb" "$out/dtb" >/dev/null
admission=$(python3 "$repo_root/scripts/kernel/fp6-android-gpu-admission.py" emit \
  --source "$source_dir" --vmlinux "$obj/vmlinux" --output "$out/android-gpu-admission.sha256") ||
  fail "Android GPU admission"
inputs=$(python3 "$repo_root/scripts/kernel/fp6-android-gpu-admission.py" inputs)
{
  printf 'TAG=%s\nRELEASE=%s\n' "$tag" "$release"
  printf 'FRAGMENTS=%s\n' "${fragments[*]}"
  printf 'PATCHES=%s\n' "${patches[*]}"
  printf 'IMAGE_SHA256=%s\n' "$(sha256sum "$out/Image.gz" | cut -d' ' -f1)"
  printf 'CONFIG_SHA256=%s\n' "$(sha256sum "$out/config" | cut -d' ' -f1)"
  printf 'DTB_SHA256=%s\n' "$(sha256sum "$out/dtb" | cut -d' ' -f1)"
  printf 'SIGNING_CERT_SHA256=%s\n' "$(sha256sum "$obj/certs/signing_key.x509" | cut -d' ' -f1)"
  printf 'MODULE_COUNT=%s\n' "$(find "$out/modules/lib/modules/$release" -name '*.ko*' | wc -l)"
  printf '%s\n' "$inputs" "$admission"
} >"$out/manifest.env"
cat "$out/manifest.env"
