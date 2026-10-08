#!/usr/bin/env bash

set -Eeuo pipefail

release_dir="${1:-.}"

if [[ ! -d "${release_dir}" ]]; then
  echo "recovery release directory not found: ${release_dir}" >&2
  exit 1
fi

cd "${release_dir}"

if [[ ! -f SHA256SUMS ]]; then
  echo "SHA256SUMS is missing" >&2
  exit 1
fi

if command -v sha256sum >/dev/null; then
  sha256sum --check SHA256SUMS
elif command -v shasum >/dev/null; then
  shasum -a 256 --check SHA256SUMS
else
  echo "sha256sum or shasum is required" >&2
  exit 1
fi

if command -v zstd >/dev/null; then
  found_archive=false
  while IFS= read -r first_part; do
    found_archive=true
    archive="${first_part%.part-00}"
    echo "testing compressed stream: ${archive}"
    cat "${archive}".part-* | zstd --test --quiet
  done < <(find . -maxdepth 1 -type f -name '*.tar.zst.part-00' -print | sort)

  if [[ "${found_archive}" == false ]]; then
    echo "no recovery archive parts found" >&2
    exit 1
  fi
else
  echo "chunk hashes pass; install zstd to test the reconstructed archive streams" >&2
fi

echo "Project Luma recovery release verification: OK"

