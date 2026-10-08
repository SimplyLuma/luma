#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

desktop_descriptor="$repo_root/assets/boot/luma-loading.plymouth"
desktop_script="$repo_root/assets/boot/luma-loading.script"
handheld_descriptor="$repo_root/assets/boot/luma-loading-handheld.plymouth"
handheld_script="$repo_root/assets/boot/luma-loading-handheld.script"
mobile_config="$repo_root/config/mobile/plymouthd.conf"
spec="$repo_root/packaging/rpm/luma-boot-theme.spec"
composer="$repo_root/scripts/mobile/compose-fp6-rootfs.sh"

grep -Fxq 'Theme=luma-loading-handheld' "$mobile_config"
grep -Fxq 'Theme=luma-loading' "$repo_root/config/boot/plymouthd.conf"
grep -Fq 'ScriptFile=/usr/share/plymouth/themes/luma-loading/luma-loading.script' \
  "$desktop_descriptor"
grep -Fq 'global.logo_source = Image("luma-wordmark.png");' "$desktop_script"
grep -Fq 'ScriptFile=/usr/share/plymouth/themes/luma-loading-handheld/luma-loading-handheld.script' \
  "$handheld_descriptor"

python3 - "$repo_root" <<'PYTHON'
from pathlib import Path
import importlib.util,json,sys
root=Path(sys.argv[1])
spec=importlib.util.spec_from_file_location('boot_compiler',root/'scripts/boot/compile-theme.py')
compiler=importlib.util.module_from_spec(spec);spec.loader.exec_module(compiler)
for name,path,mobile in [('luma-loading','config/boot/desktop-theme.json',False),
                         ('luma-loading-handheld','config/mobile/boot-theme.json',True)]:
    expected=compiler.script(json.loads((root/path).read_text()),(root/'assets/boot/luma-theme.script.in').read_text(),mobile)
    assert (root/f'assets/boot/{name}.script').read_text()==expected
PYTHON

grep -Fq 'Source8:        luma-wordmark.svg' "$spec"
grep -Fq '%{_datadir}/luma/boot/brand/luma-wordmark.svg' "$spec"
grep -Fq 'build/packages/boot-theme/RPMS/noarch/$LUMA_BOOT_THEME_NEVRA.rpm' "$composer"
grep -Fq 'config/mobile/plymouthd.conf' "$composer"
test "$LUMA_BOOT_THEME_NEVRA" = luma-boot-theme-0.1.0-1.luma.15.fc44.noarch

printf 'Mobile boot-theme source contract: PASS\n'
