#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
source_file=$repo_root/src/fp6-android-netbpfload/luma_netbpfload.cpp
ndk_root=${ANDROID_NDK_ROOT:-/Users/nick/Library/Android/sdk/ndk/28.2.13676358}
toolchain=$ndk_root/toolchains/llvm/prebuilt/darwin-x86_64
compiler=$toolchain/bin/aarch64-linux-android35-clang++
stock_lib=${LUMA_STOCK_LIBBPF_ANDROID:-/tmp/luma-c1-netbpfload-inputs/libbpf_android.so}
output=${LUMA_NETBPFLOAD_OUTPUT:-$repo_root/artifacts/fp6-android-netbpfload/luma-netbpfload}

[[ -f $source_file && ! -L $source_file ]] || {
  echo "missing source: $source_file" >&2
  exit 1
}
[[ -x $compiler ]] || {
  echo "missing Android NDK compiler: $compiler" >&2
  exit 1
}
[[ -f $stock_lib && ! -L $stock_lib ]] || {
  echo "missing exact stock libbpf_android.so: $stock_lib" >&2
  exit 1
}

mkdir -p "$(dirname "$output")"
"$compiler" \
  --target=aarch64-linux-android35 \
  -std=c++20 -fPIE -pie -O2 -Wall -Wextra -Werror -static-libstdc++ \
  "$source_file" "$stock_lib" \
  -Wl,--allow-shlib-undefined \
  -Wl,--no-undefined-version \
  -o "$output"

file "$output"
shasum -a 256 "$output"
