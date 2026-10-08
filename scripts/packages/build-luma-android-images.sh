#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
if [ "${1:-}" = --reference ]; then
  [ "$#" -eq 1 ] || { printf 'error: unexpected reference arguments\n' >&2; exit 2; }
  exec "$repo_root/scripts/packages/build-luma-android-images-reference.sh"
fi
[ "$#" -eq 0 ] || { printf 'usage: %s [--reference]\n' "$0" >&2; exit 2; }
source "$repo_root/config/desktop/inputs.env"
arch=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
[ "$arch" = x86_64 ] || { printf 'error: current native Android frame pair is not qualified for %s\n' "$arch" >&2; exit 1; }
: "${LUMA_ANDROID_TERMINAL_PAIR_DIR:?actual terminal native image pair required}"
: "${LUMA_ANDROID_IMAGE_MEMBERS:?actual read-only inside-image membership proof required}"
export LUMA_RPM_BUILDER_ARCHITECTURE=$arch
output="$repo_root/build/packages/luma-android-images/$arch"
mkdir -p "$output"
work="$output/work.native.$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
python3 -B -I "$repo_root/scripts/android/stage-native-image-package.py" \
  "$LUMA_ANDROID_TERMINAL_PAIR_DIR" "$LUMA_ANDROID_IMAGE_MEMBERS" "$work" "$repo_root" "$arch"
# Recount only allocations already made by staging, then preserve the original
# full scratch forecast and 10 GiB physical floor through normal RPM stages.
python3 -B -I - "$work" <<'PY'
import json, os, pathlib, shutil, sys
root=pathlib.Path(sys.argv[1]); receipt=json.loads((root/'STAGED-INPUTS.json').read_text())
allocated=sum(p.stat().st_blocks*512 for p in root.rglob('*') if p.is_file() and not p.is_symlink())
remaining=max(0, receipt['actual_full_scratch_budget']-allocated)
if shutil.disk_usage(root).free < remaining+receipt['physical_reserve_bytes']:
    raise SystemExit('Insufficient measured remaining scratch plus 10 GiB reserve')
PY
"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$work" "$FEDORA_RPM_BUILD_CONTAINER" '
  set -euo pipefail
  dnf5 -y install rpm-build python3
  rpmbuild -ba --define "_topdir $PWD" SPECS/luma-android-images.spec
  test "$(find RPMS -name "luma-android-images-*.rpm" | wc -l)" -eq 1
  test "$(find SRPMS -name "luma-android-images-*.src.rpm" | wc -l)" -eq 1
'
mkdir -p "$output/RPMS" "$output/SRPMS"
find "$work/RPMS" -name 'luma-android-images-*.rpm' -exec cp --reflink=auto -t "$output/RPMS" {} +
find "$work/SRPMS" -name 'luma-android-images-*.src.rpm' -exec cp --reflink=auto -t "$output/SRPMS" {} +
find "$output/RPMS" "$output/SRPMS" -type f -name '*.rpm' -print0 |
  sort -z | xargs -0 sha256sum > "$output/SHA256SUMS"
test -s "$output/SHA256SUMS"
