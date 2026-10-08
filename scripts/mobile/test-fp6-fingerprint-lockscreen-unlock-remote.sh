#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# One bounded physical acceptance test: lock the active local Luma/Phosh
# session, require a secure match-on-chip authentication, then clear the idle
# hint before asking logind and Phosh to wake/unlock that exact session. No PIN,
# HAT, template, or raw image is logged.

set -Eeuo pipefail
umask 077

smoke=/tmp/run-fp6-stock-biometric-service-smoke-v1-remote.sh
stage=/tmp/luma-qcomtee-stage-rpmb-lazy-v1
client=/tmp/qcomtee-smoke-src/adapter/libluma-fingerprint-enrollment-client.so
wake_input=/tmp/luma-fp6-wake-input
log=/tmp/luma-fp6-lockscreen-auth.events
expected_kernel=7.1.2-luma-fp-cma1
expected_listener_sha=e871f3e7e13e5030945baed60038cb029badd6d69afb5bc6e342c5c343338389
expected_client_sha=5b7f6f48c36bfadefec76fb3c2cf578d193962d802feef538c2a812beeee1303
expected_wake_input_sha=20ed28fe084294c161e9d263342b2af67d225471abec079092e4bf951b17245e

fail() {
  printf 'event=fp6_lockscreen_unlock_failed reason=%s\n' "$1" >&2
  exit 1
}

[[ $(id -u) -eq 0 ]] || fail not_root
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || fail slot
[[ $(uname -r) == "$expected_kernel" ]] || fail kernel
[[ -x $smoke && ! -L $smoke ]] || fail smoke_identity
[[ -x $client && ! -L $client ]] || fail client_identity
[[ -x $wake_input && ! -L $wake_input ]] || fail wake_input_identity
[[ $(sha256sum "$client" | cut -d' ' -f1) == "$expected_client_sha" ]] || \
  fail client_hash
[[ $(sha256sum "$wake_input" | cut -d' ' -f1) == "$expected_wake_input_sha" ]] || \
  fail wake_input_hash
[[ -f $stage/listener.pid && ! -L $stage/listener.pid ]] || fail listener_pid
listener_pid=$(cat "$stage/listener.pid")
[[ $listener_pid =~ ^[0-9]+$ && -d /proc/$listener_pid ]] || fail listener_process
[[ $(sha256sum "/proc/$listener_pid/exe" | cut -d' ' -f1) == "$expected_listener_sha" ]] || \
  fail listener_hash

mapfile -t sessions < <(
  loginctl list-sessions --no-legend |
    awk '$2 == 1000 && $3 == "luma" && $4 == "seat0" && $6 == "user" {print $1}'
)
[[ ${#sessions[@]} -eq 1 ]] || fail local_session_identity
session=${sessions[0]}
[[ $(loginctl show-session "$session" -p Active --value) == yes ]] || fail session_inactive
pgrep -u luma -x phosh >/dev/null || fail phosh_missing
phosh_bus=(
  sudo -n -u luma env
  XDG_RUNTIME_DIR=/run/user/1000
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
  gdbus call --session --dest org.gnome.ScreenSaver
  --object-path /org/gnome/ScreenSaver
)

"${phosh_bus[@]}" --method org.gnome.ScreenSaver.Lock >/dev/null
for _ in $(seq 1 30); do
  [[ $("${phosh_bus[@]}" --method org.gnome.ScreenSaver.GetActive) == '(true,)' ]] && break
  sleep 0.1
done
[[ $("${phosh_bus[@]}" --method org.gnome.ScreenSaver.GetActive) == '(true,)' ]] || \
  fail lock_not_confirmed
printf 'event=fp6_lockscreen_locked session=local-phosh authentication=pending\n'

: >"$log"
chmod 0600 "$log"
status=0
env \
  LUMA_FP6_ENROLLMENT_STAGE="$stage" \
  LUMA_FP6_LISTENER_DAEMON_SHA256="$expected_listener_sha" \
  LUMA_STOCK_ENROLLMENT_CLIENT="$client" \
  LUMA_STOCK_ENROLLMENT_CLIENT_SHA256="$expected_client_sha" \
  LUMA_STOCK_PERSIST_FINGERPRINT=1 \
  LUMA_STOCK_BIOMETRIC_CLIENT_MODE=authenticate \
  "$smoke" fingerprint 2>&1 | tee "$log" || status=${PIPESTATUS[0]}
[[ $status -eq 0 ]] || fail "authentication_exit_$status"
grep -Fqx 'event=fp6_stock_authentication_pass match=secure_match_on_chip raw_images=0' \
  "$log" || fail secure_match_missing
grep -Fqx 'event=stock_fingerprint_authentication_verified template=match_on_chip ree_mirror_required=false raw_images=0 hat_logged=0' \
  "$log" || fail protected_verification_missing

# Phosh blanks an idle session again immediately, even after SetActive(false).
# Clear logind's idle hint first, emit Phosh's documented WakeUpScreen signal,
# power the output on while the screen saver is still active, and only then
# emit the session's real Unlock signal.  Verify the state after a settling
# window so a transient modeset cannot be mistaken for a successful wake.
"$wake_input"
sudo -n -u luma env \
  XDG_RUNTIME_DIR=/run/user/1000 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  gdbus emit --session --object-path /org/gnome/ScreenSaver \
  --signal org.gnome.ScreenSaver.WakeUpScreen >/dev/null
"${phosh_bus[@]}" --method org.gnome.ScreenSaver.SetActive false >/dev/null
gdbus call --system \
  --dest org.freedesktop.login1 \
  --object-path "/org/freedesktop/login1/session/$session" \
  --method org.freedesktop.login1.Session.SetIdleHint false >/dev/null
for _ in $(seq 1 30); do
  [[ $("${phosh_bus[@]}" --method org.gnome.ScreenSaver.GetActive) == '(false,)' ]] && break
  sleep 0.1
done
[[ $("${phosh_bus[@]}" --method org.gnome.ScreenSaver.GetActive) == '(false,)' ]] || \
  fail unlock_not_confirmed
gdbus call --system \
  --dest org.freedesktop.login1 \
  --object-path "/org/freedesktop/login1/session/$session" \
  --method org.freedesktop.login1.Session.Unlock >/dev/null
sleep 3
[[ $(loginctl show-session "$session" -p IdleHint --value) == no ]] || \
  fail idle_hint_rebounded
[[ $("${phosh_bus[@]}" --method org.gnome.ScreenSaver.GetActive) == '(false,)' ]] || \
  fail lock_rebounded
backlight=/sys/class/backlight/ae94000.dsi.0
[[ -r $backlight/bl_power && $(<"$backlight/bl_power") == 0 ]] || \
  fail display_not_awake
find "$log" -delete
printf 'event=fp6_lockscreen_unlock_pass session=local-phosh authentication=secure_match_on_chip idle=active display_wake=verified raw_images=0 hat_logged=0\n'
