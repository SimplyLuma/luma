#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
#
# Source, artwork and migration contract for the desktop boot splash: the
# luma-loading selection, the luma-firmware presentation that stays packaged,
# the hidden GRUB menu and the one-shot setting migration.
# Runs in the Fedora rasterization builder (rsvg-convert, python3, GNU
# coreutils). It proves what can be proven without booting: the selection, a
# reproducible and correctly sized render, and that the one-shot moves only
# settings Luma shipped. Boot, shutdown and prompt rendering are VM evidence.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

desktop_config=$repo_root/config/boot/plymouthd.conf
previous=$repo_root/config/boot/plymouthd.conf.previous
theme=$repo_root/assets/boot/luma-firmware.plymouth
migrate=$repo_root/config/boot/luma-boot-splash-migrate

# Selection: desktop luma-loading, which takes over from the firmware logo;
# handheld unchanged; luma-firmware still complete for anyone who picks it.
# Luma's plymouth selects the desktop splash in /usr; the /etc file Luma ships
# is a template with no settings, so nothing in /etc can take the splash away.
if grep -Evq '^[[:space:]]*(#|$)' "$desktop_config"; then
  printf 'error: %s has settings; the splash is selected in plymouthd.defaults\n' "$desktop_config" >&2
  exit 1
fi
grep -Fq "sed -i -e 's/^Theme=.*/Theme=luma-loading/' src/plymouthd.defaults" \
  "$repo_root/patches/plymouth/0000-luma-fedora-spec.patch"
grep -Fq 'Plymouth.GetTime' "$repo_root/assets/boot/luma-loading.script"
grep -Fq 'Window.SetFirmwareBackgroundOpacity' "$repo_root/assets/boot/luma-loading.script"
grep -Fxq 'global.handheld = 1;' "$repo_root/assets/boot/luma-loading-handheld.script"
grep -Fxq 'Theme=luma-loading-handheld' "$repo_root/config/mobile/plymouthd.conf"
grep -Fxq 'ModuleName=two-step' "$theme"
grep -Fxq 'ImageDir=/usr/share/plymouth/themes/luma-firmware' "$theme"
for mode in boot-up shutdown reboot; do
  awk -v mode="[$mode]" '$0 == mode { section = 1; next } /^\[/ { section = 0 }
    section && $0 == "UseFirmwareBackground=true" { found = 1 } END { exit !found }' "$theme"
done

# GRUB stays out of sight on bootupd installs: the hidden-menu piece sorts
# after the preamble's timeout and before 10_blscfg (so before luma.cfg) and
# 14_menu_show_once, keeps a one-second window for Esc/F8/Shift, and luma.cfg
# only clears the screen for a menu that is actually drawn.
hidden_menu=$repo_root/config/boot/09_luma_hidden_menu.cfg
[ "$(grep -Ev '^(#|$)' "$hidden_menu")" = "$(printf 'set timeout_style=hidden\nset timeout=1')" ]
[ "$(printf '%s\n' 08_greenboot.cfg 10_blscfg.cfg 09_luma_hidden_menu.cfg 14_menu_show_once.cfg | LC_ALL=C sort)" = \
  "$(printf '%s\n' 08_greenboot.cfg 09_luma_hidden_menu.cfg 10_blscfg.cfg 14_menu_show_once.cfg)" ]
# The helper that brings updated computers' grub.cfg up to date.
python3 -m unittest -q "$repo_root/tests/unit/test_luma_boot_hidden_menu.py"
awk '/timeout_style.*!=.*hidden/ { guard = NR } /terminal_output gfxterm/ { exit !(guard && guard < NR) }' \
  "$repo_root/config/boot/luma.cfg"
if command -v grub2-script-check >/dev/null 2>&1; then
  grub2-script-check "$hidden_menu"
  grub2-script-check "$repo_root/config/boot/luma.cfg"
fi

# The shipped digest list: well formed, unique, and never the current file.
current=$(sha256sum "$desktop_config" | cut -d ' ' -f 1)
digests=$(grep -Ev '^(#|$)' "$previous" | cut -d ' ' -f 1)
[ -n "$digests" ]
! grep -Evq '^[0-9a-f]{64}$' <<<"$digests"
[ "$(sort <<<"$digests" | uniq -d)" = '' ]
! grep -Fxq "$current" <<<"$digests"
# Every desktop plymouthd.conf in this repository's history is either current
# or listed, so no machine carrying one is stranded.
if git -C "$repo_root" rev-parse --git-dir >/dev/null 2>&1; then
  git -C "$repo_root" log --all --format=%H -- config/boot/plymouthd.conf |
    while read -r commit; do
      blob=$(git -C "$repo_root" show "$commit:config/boot/plymouthd.conf" 2>/dev/null) || continue
      digest=$(printf '%s\n' "$blob" | sha256sum | cut -d ' ' -f 1)
      [ "$digest" = "$current" ] || grep -Fxq "$digest" <<<"$digests" || {
        printf 'error: plymouthd.conf from %s (%s) is neither current nor listed\n' \
          "$commit" "$digest" >&2
        exit 1
      }
    done
fi

# Artwork: two renders are byte-identical and every image has its drawn size.
render() {
  python3 "$repo_root/scripts/boot/render-firmware-theme.py" \
    --wordmark "$repo_root/website/public/brand/luma-wordmark.svg" \
    --dot "$repo_root/assets/boot/luma-loading-dot.svg" --output "$1" >/dev/null
}
render "$work/first"
render "$work/second"
(cd "$work/first" && sha256sum -- *.png) >"$work/first.sums"
(cd "$work/second" && sha256sum -- *.png) >"$work/second.sums"
cmp "$work/first.sums" "$work/second.sums"
python3 - "$work/first" <<'PYTHON'
import struct, sys
from pathlib import Path
directory = Path(sys.argv[1])
def size(name):
    data = (directory / name).read_bytes()
    assert data[:8] == b'\x89PNG\r\n\x1a\n', name
    return struct.unpack('>II', data[16:24])
expected = {'watermark.png': (99, 32), 'lock.png': (60, 36), 'entry.png': (300, 36),
            'bullet.png': (16, 16), 'keyboard.png': (40, 40), 'capslock.png': (24, 28)}
for name, dimensions in expected.items():
    assert size(name) == dimensions, (name, size(name))
frames = sorted(directory.glob('throbber-*.png'))
assert [frame.name for frame in frames] == [f'throbber-{i:04d}.png' for i in range(1, 61)]
assert all(size(frame.name) == (56, 8) for frame in frames)
# Two identical 1.0 s pulses fill Plymouth's fixed 2.0 s throbber loop: thirty
# distinct frames, repeated, so the loop point is seamless.
data = [frame.read_bytes() for frame in frames]
assert len(set(data)) == 30, 'the loader does not animate through a full pulse'
assert data[:30] == data[30:]
assert len(list(directory.glob('*.png'))) == 66
PYTHON

# Migration: only an exact earlier Luma setting moves, once; nothing else does.
bash "$repo_root/tests/smoke/boot-splash-migrate.sh" "$migrate" "$desktop_config" "$previous"
bash -n "$repo_root/config/boot/dracut/module-setup.sh"

printf 'Desktop boot splash contract: PASS\n'
