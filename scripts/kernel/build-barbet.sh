#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build the barbet kernel from the pinned source lock: Linux v7.3-rc7 plus the
# avicii SM7250 overlay and patches/linux-barbet/7.3, with the committed
# config. Publishes Image, DTB, config and receipt.json only after every check
# passes. Never flashes or deploys anything.
#
# usage: build-barbet.sh [--cache DIR] [--source DIR] [--offline] OUTPUT_DIR
#   --cache   download/extract the pinned archives here (default: required
#             unless --source is given)
#   --source  an already prepared tree (prepare-source.py output or the
#             extracted linux tree); skips prepare
#   --offline fail instead of downloading a missing archive
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
lock="$repo_root/config/kernel/barbet-7.3.json"
config="$repo_root/config/kernel/barbet.config"
fail() { printf 'build-barbet: %s\n' "$*" >&2; exit 1; }
usage() { sed -n 's/^# usage: /usage: /p' "$0" >&2; exit 2; }

cache="" source_dir="" offline=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --cache) cache=$(realpath -e "${2:?}"); shift 2 ;;
    --source) source_dir=$(realpath -e "${2:?}"); shift 2 ;;
    --offline) offline="--offline"; shift ;;
    *) break ;;
  esac
done
[ "$#" -eq 1 ] || usage
out=$1

eval "$(python3 - "$lock" "$config" <<'PY'
import hashlib, json, shlex, sys
lock_path = sys.argv[1]
lock = json.load(open(lock_path))
config = open(sys.argv[2], 'rb').read()
print(f"lock_sha={shlex.quote(hashlib.sha256(open(lock_path, 'rb').read()).hexdigest())}")
print(f"config_sha={shlex.quote(hashlib.sha256(config).hexdigest())}")
print(f"release={shlex.quote(lock['release'])}")
print(f"patch_count={len(lock['patches'])}")
print(f"lock_config={shlex.quote(lock['base_config']['sha256'])}")
PY
)"
[ "$config_sha" = "$lock_config" ] || fail "config hash does not match the lock"

if [ -e "$out" ]; then
  [ -f "$out/receipt.json" ] || fail "output exists without a receipt: $out"
  grep -q "\"lock_sha256\": \"$lock_sha\"" "$out/receipt.json" ||
    fail "stale receipt in $out (lock changed)"
fi

work="${out}.work"
rm -rf "$work"
mkdir -p "$work"

if [ -n "$source_dir" ]; then
  src=$source_dir
else
  [ -n "$cache" ] || fail "--cache or --source is required"
  mkdir -p "$work/prepared"
  python3 "$repo_root/scripts/kernel/prepare-source.py" \
    --lock "$lock" --cache "$cache" --output "$work/prepared" $offline >/dev/null
  src=$(python3 -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["linux"])' \
    "$work/prepared/PREPARED.json")
fi
[ -f "$src/Makefile" ] || fail "$src is not a kernel tree"

obj="$work/obj"
mkdir -p "$obj"
cp "$config" "$obj/.config"
make="${MAKE:-make}"
jobs="${JOBS:-$(nproc)}"
kbuild=(O="$obj" ARCH=arm64 LLVM=1 LOCALVERSION=
        KBUILD_BUILD_USER=luma KBUILD_BUILD_HOST=kernel-cross
        KBUILD_BUILD_VERSION="${KBUILD_BUILD_VERSION:-1}"
        KBUILD_BUILD_TIMESTAMP="${KBUILD_BUILD_TIMESTAMP:-2026-11-14 00:00:00 UTC}")

kernel_release=$("$make" -s -C "$src" "${kbuild[@]}" kernelrelease)
[ "$kernel_release" = "$release" ] ||
  fail "kernel release $kernel_release does not match the lock ($release)"

if ! "$make" -s -C "$src" "${kbuild[@]}" olddefconfig; then
  fail "olddefconfig failed"
fi
if ! cmp -s "$obj/.config" "$config"; then
  diff -u "$config" "$obj/.config" >&2 || true
  fail "the committed config does not survive olddefconfig"
fi

if ! "$make" -s -C "$src" "${kbuild[@]}" -j"$jobs" \
      Image qcom/sm7250-google-barbet.dtb; then
  fail "kernel build failed"
fi

image="$obj/arch/arm64/boot/Image"
dtb="$obj/arch/arm64/boot/dts/qcom/sm7250-google-barbet.dtb"
[ -s "$image" ] || fail "build produced no Image"
[ -s "$dtb" ] || fail "build produced no sm7250-google-barbet.dtb"

mkdir -p "$out/qcom"
cp "$image" "$out/Image"
cp "$dtb" "$out/qcom/sm7250-google-barbet.dtb"
cp "$config" "$out/config"
python3 - "$out" "$lock_sha" "$config_sha" "$release" "$patch_count" <<'PY'
import hashlib, json, sys
from pathlib import Path
out = Path(sys.argv[1])
digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
receipt = {
    "schema": "org.projectluma.barbet-kernel/v1",
    "lock_sha256": sys.argv[2],
    "config_sha256": sys.argv[3],
    "release": sys.argv[4],
    "patch_count": int(sys.argv[5]),
    "image_sha256": digest(out / "Image"),
    "dtb_sha256": digest(out / "qcom/sm7250-google-barbet.dtb"),
}
(out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt, indent=2))
PY
rm -rf "$work"
