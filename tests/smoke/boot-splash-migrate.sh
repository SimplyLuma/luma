#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
#
# Contract of luma-boot-splash-migrate: only an exact earlier Luma setting
# moves, each default is applied at most once, an administrator's choice is
# never overridden, and --check answers what a real run would do (the
# luma-boot-splash dracut module relies on that answer).
#
# usage: boot-splash-migrate.sh HELPER DEFAULT PREVIOUS
# Runs from the source tree (tests/smoke/boot-firmware-theme.sh) and from the
# package's %check against the files it installs.

set -euo pipefail

[ $# = 3 ] || { echo 'usage: boot-splash-migrate.sh HELPER DEFAULT PREVIOUS' >&2; exit 2; }
migrate=$1 default=$2 previous=$3
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
mkdir -p "$work/etc"
config=$work/etc/plymouthd.conf
marker=$work/etc/.luma-boot-splash-default

run() {
  LUMA_PLYMOUTHD_CONF=$config LUMA_PLYMOUTHD_DEFAULT=$default \
    LUMA_PLYMOUTHD_PREVIOUS=$previous bash "$migrate" "$@" >/dev/null
}
would_replace() { run --check; }
# `! cmd` never trips set -e, so a negative expectation fails explicitly.
wont_replace() {
  if run --check; then
    printf 'error: --check would replace %s\n' "$(head -c 200 "$config" 2>/dev/null)" >&2
    exit 1
  fi
}
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
fresh() { rm -f -- "$config" "$marker"; }

# The current default is a template: no settings, so Luma's splash comes from
# plymouthd.defaults in /usr (ADR-045).
if grep -Evq '^[[:space:]]*(#|$)' "$default"; then fail "the default $default has settings"; fi

# Every earlier desktop default Luma shipped moves to the current one.
luma_loading_0811=$(printf '[Daemon]\nTheme=luma-loading\nShowDelay=0\nDeviceTimeout=8\nUseSimpledrmNoLuks=1\n')
luma_firmware_0915=$(printf '# Luma desktop boot splash. Luma updates this file only while it is exactly a\n# setting Luma shipped before; once it is edited, it is left as it is.\n[Daemon]\nTheme=luma-firmware\nShowDelay=0\nDeviceTimeout=8\nUseSimpledrmNoLuks=1\n')
luma_loading_0917=$(printf '# Luma desktop boot splash. Luma updates this file only while it is exactly a\n# setting Luma shipped before; once it is edited, it is left as it is.\n[Daemon]\nTheme=luma-loading\nShowDelay=0\nDeviceTimeout=8\nUseSimpledrmNoLuks=1\n')
# Fedora plymouth's own template, which a client-side plymouth override put in
# /usr/etc and so, through OSTree's /etc merge, in /etc.
fedora_template=$(printf '# Administrator customizations go in this file\n#[Daemon]\n#Theme=fade-in\n')
for earlier in "$luma_loading_0811" "$luma_firmware_0915" "$luma_loading_0917" "$fedora_template"; do
  fresh
  printf '%s\n' "$earlier" >"$config"
  would_replace
  run
  cmp "$config" "$default"
  [ "$(stat -c %a "$config")" = 644 ]
  [ "$(cat "$marker")" = "$(sha256sum "$default" | cut -d ' ' -f 1)" ]
  wont_replace
  run
  cmp "$config" "$default"
done

# Exactly once: an administrator who goes back to the earlier Luma setting
# after the move keeps it, and --check agrees.
printf '%s\n' "$luma_firmware_0915" >"$config"
wont_replace
run
[ "$(cat "$config")" = "$luma_firmware_0915" ]

# A marker for an older default does not stop the move to a newer one.
fresh
printf '%s\n' "$luma_firmware_0915" >"$config"
printf '%s\n' 0000000000000000000000000000000000000000000000000000000000000000 >"$marker"
would_replace
run
cmp "$config" "$default"

# The current default is only recorded.
fresh
cat "$default" >"$config"
wont_replace
run
cmp "$config" "$default"
test -s "$marker"

# Anything else is somebody's choice, now and after Luma's default changes.
for kept in "$(printf '[Daemon]\nTheme=spinner\n')" \
            "$(printf '[Daemon]\nTheme=luma-firmware\nShowDelay=5\nDeviceTimeout=8\nUseSimpledrmNoLuks=1')" \
            "$(printf '[Daemon]\nTheme=luma-loading-handheld\n')"; do
  fresh
  printf '%s\n' "$kept" >"$config"
  wont_replace
  run
  [ "$(cat "$config")" = "$kept" ]
  printf '%s\n' "$luma_firmware_0915" >"$config"
  wont_replace
  run
  [ "$(cat "$config")" = "$luma_firmware_0915" ]
done

# A link or no file at all is left alone and records nothing.
fresh
ln -s "$default" "$config"
wont_replace
run
[ -L "$config" ] || fail 'a linked plymouthd.conf was replaced'
[ ! -e "$marker" ] || fail 'a linked plymouthd.conf was recorded'
fresh
wont_replace
run
[ ! -e "$config" ] || fail 'a missing plymouthd.conf was created'
[ ! -e "$marker" ] || fail 'a missing plymouthd.conf was recorded'
[ -z "$(find "$work/etc" -name '.plymouthd.conf.luma.*' -o -name '.luma-boot-splash.*')" ] ||
  fail 'staging files were left behind'
if run --bogus 2>/dev/null; then exit 1; fi

printf 'Boot splash migration contract: PASS\n'
