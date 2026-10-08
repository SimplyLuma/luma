#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

root=${1:-/}
if [ "$root" = / ]; then
  rpm -q "$LUMA_MODS_NEVRA"
else
  rpm --root "$root" -q "$LUMA_MODS_NEVRA"
fi
test -x "$root/usr/bin/luma-mod"
test -x "$root/usr/bin/luma-mod-recover"
test -L "$root/usr/lib/systemd/user/graphical-session-pre.target.wants/luma-mod-recover.service"
grep -Fq 'class ClosedSystemBackend' \
  "$root"/usr/lib/python3.*/site-packages/luma_mods/privileged.py

printf 'Luma Mods installed-state smoke test: PASS (%s)\n' "$root"
