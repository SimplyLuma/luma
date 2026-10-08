#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Runtime FP6/Phosh fingerprint lock agent.  This is deliberately a thin
# policy layer over the hash-gated stock-QREL authentication harness: it arms
# only while Phosh is locked, cancels when another authentication method wins,
# and rearms on the next lock.  It never receives a PIN, template, HAT, or raw
# fingerprint image.

set -Eeuo pipefail
umask 077

runtime=${LUMA_FP6_RUNTIME_ROOT:-/usr/lib/luma/fp6-fingerprint}
smoke=${LUMA_FP6_BIOMETRIC_SMOKE:-/usr/libexec/luma-fp6-fingerprint-auth}
stage=${LUMA_FP6_ENROLLMENT_STAGE:-/run/luma/fp6-fingerprint}
client=${LUMA_FP6_AUTH_CLIENT:-$runtime/adapter/libluma-fingerprint-enrollment-client.so}
expected_listener_sha=${LUMA_FP6_LISTENER_DAEMON_SHA256:-e871f3e7e13e5030945baed60038cb029badd6d69afb5bc6e342c5c343338389}
expected_client_sha=${LUMA_FP6_AUTH_CLIENT_SHA256:-5b7f6f48c36bfadefec76fb3c2cf578d193962d802feef538c2a812beeee1303}
backlight=/sys/class/backlight/ae94000.dsi.0
wake_input=${LUMA_FP6_WAKE_INPUT:-/usr/libexec/luma-fp6-wake-input}
expected_wake_input_sha=${LUMA_FP6_WAKE_INPUT_SHA256:-20ed28fe084294c161e9d263342b2af67d225471abec079092e4bf951b17245e}
child=
log=
monitor_pid=
fifo=

event() {
  printf 'event=luma_fp6_fingerprint_lock_agent %s\n' "$*"
}

cleanup() {
  local status=$?
  set +e
  if [[ $child =~ ^[0-9]+$ ]] && kill -0 "$child" 2>/dev/null; then
    # Android's stock biometric daemons catch SIGTERM for Android lifecycle
    # handling and can remain in Binder waits. SIGHUP is unhandled by those
    # disposable processes and cleanly tears down the whole isolated session.
    kill -HUP -- "-$child" 2>/dev/null || kill -HUP "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
  fi
  if [[ $monitor_pid =~ ^[0-9]+$ ]] && kill -0 "$monitor_pid" 2>/dev/null; then
    kill -TERM "$monitor_pid" 2>/dev/null || true
    wait "$monitor_pid" 2>/dev/null || true
  fi
  [[ -z $log || ! -e $log ]] || find "$log" -delete
  [[ -z $fifo || ! -p $fifo ]] || find "$fifo" -delete
  return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 0' TERM
trap 'exit 129' HUP

[[ $(id -u) -eq 0 ]] || { event state=failed reason=not_root; exit 1; }
case $(uname -r) in
  7.1.2-luma-fp-cma1|7.1.2-luma-fp-ims1) ;;
  *)
  event state=failed reason=kernel_identity
  exit 1
  ;;
esac
grep -qw androidboot.slot_suffix=_b /proc/cmdline || {
  event state=failed reason=slot_identity
  exit 1
}
[[ -x $smoke && ! -L $smoke && -x $client && ! -L $client ]] || {
  event state=failed reason=runtime_identity
  exit 1
}
[[ $(sha256sum "$client" | cut -d' ' -f1) == "$expected_client_sha" ]] || {
  event state=failed reason=client_hash
  exit 1
}
[[ -x $wake_input && ! -L $wake_input ]] || {
  event state=failed reason=wake_input_identity
  exit 1
}
[[ $(sha256sum "$wake_input" | cut -d' ' -f1) == "$expected_wake_input_sha" ]] || {
  event state=failed reason=wake_input_hash
  exit 1
}

phosh_call() {
  sudo -n -u luma env \
    XDG_RUNTIME_DIR=/run/user/1000 \
    DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
    gdbus call --session --dest org.gnome.ScreenSaver \
    --object-path /org/gnome/ScreenSaver \
    --method "org.gnome.ScreenSaver.$1" "${@:2}"
}

locked() {
  [[ $(phosh_call GetActive 2>/dev/null) == '(true,)' ]]
}

local_session() {
  loginctl list-sessions --no-legend |
    awk '$2 == 1000 && $3 == "luma" && $4 == "seat0" && $6 == "user" {print $1}'
}

wake_and_unlock() {
  local session=$1
  "$wake_input"
  sudo -n -u luma env \
    XDG_RUNTIME_DIR=/run/user/1000 \
    DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
    gdbus emit --session --object-path /org/gnome/ScreenSaver \
    --signal org.gnome.ScreenSaver.WakeUpScreen >/dev/null
  phosh_call SetActive false >/dev/null
  gdbus call --system --dest org.freedesktop.login1 \
    --object-path "/org/freedesktop/login1/session/$session" \
    --method org.freedesktop.login1.Session.SetIdleHint false >/dev/null
  gdbus call --system --dest org.freedesktop.login1 \
    --object-path "/org/freedesktop/login1/session/$session" \
    --method org.freedesktop.login1.Session.Unlock >/dev/null
  sleep 3
  [[ $(loginctl show-session "$session" -p IdleHint --value) == no ]]
  [[ $(phosh_call GetActive) == '(false,)' ]]
  [[ -r $backlight/bl_power && $(<"$backlight/bl_power") == 0 ]]
}

# On a cold boot multi-user.target can legitimately precede the user's Phosh
# session bus.  Treat that as lifecycle ordering, not an authentication fault:
# wait without starting a monitor or burning systemd restart attempts.
waiting_reported=false
while ! initial=$(phosh_call GetActive 2>/dev/null); do
  if [[ $waiting_reported == false ]]; then
    event state=waiting reason=lock_state_unavailable
    waiting_reported=true
  fi
  sleep 1
done

fifo=/run/luma-fp6-fingerprint-lock-agent.events
[[ ! -e $fifo ]] || find "$fifo" -delete
mkfifo -m 0600 "$fifo"
sudo -n -u luma env \
  XDG_RUNTIME_DIR=/run/user/1000 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  stdbuf -oL gdbus monitor --session --dest org.gnome.ScreenSaver \
  --object-path /org/gnome/ScreenSaver >"$fifo" &
monitor_pid=$!
exec 3<>"$fifo"
if [[ $initial == '(true,)' ]]; then
  lock_state=true
else
  lock_state=false
fi
armed_reported=false
cancelled=false
retry_after=0
event state=ready policy=event_driven_rearm raw_images=0 hats_logged=0

while :; do
  if IFS= read -r -t 0.25 line <&3; then
    case $line in
      *'ActiveChanged (true,)'*) lock_state=true ;;
      *'ActiveChanged (false,)'*) lock_state=false ;;
    esac
  fi

  if [[ $child =~ ^[0-9]+$ ]]; then
    if kill -0 "$child" 2>/dev/null; then
      if [[ $armed_reported == false ]] &&
        grep -Fqx 'event=fp6_stock_authentication_waiting touch_sensor_now=true' "$log"; then
        armed_reported=true
        event state=armed display_state=independent
      fi
      if [[ $lock_state == false ]]; then
        kill -HUP -- "-$child" 2>/dev/null || kill -HUP "$child" 2>/dev/null || true
        cancelled=true
      fi
      continue
    fi

    status=0
    wait "$child" || status=$?
    child=
    if [[ $cancelled == true ]]; then
      event state=cancelled reason=lock_released_elsewhere
    elif [[ $status -eq 0 ]] &&
      grep -Fqx 'event=fp6_stock_authentication_pass match=secure_match_on_chip raw_images=0' "$log" &&
      grep -Fqx 'event=stock_fingerprint_authentication_verified template=match_on_chip ree_mirror_required=false raw_images=0 hat_logged=0' "$log"; then
      if wake_and_unlock "$session"; then
        lock_state=false
        event state=unlocked authentication=secure_match_on_chip display_wake=verified
      else
        event state=failed reason=visible_unlock_boundary
      fi
    else
      event state=retry reason=authentication_exit status="$status"
      retry_after=$((SECONDS + 1))
    fi
    find "$log" -delete
    log=
    armed_reported=false
    cancelled=false
    continue
  fi

  if [[ $lock_state == true && $SECONDS -ge $retry_after ]]; then
    # The monitor can still contain the earlier ActiveChanged(true) event when
    # wake_and_unlock has already made the authoritative state false.  Never
    # rearm from that stale edge; confirm the live lock state first.
    if ! locked; then
      lock_state=false
      continue
    fi
    mapfile -t sessions < <(local_session)
    if [[ ${#sessions[@]} -ne 1 ]]; then
      event state=deferred reason=local_session_identity
      retry_after=$((SECONDS + 2))
      continue
    fi
    session=${sessions[0]}
    log=$(mktemp /run/luma-fp6-auth.XXXXXX)
    chmod 0600 "$log"
    event state=arming session=local-phosh
    setsid systemd-inhibit \
      --what=sleep \
      --who=Luma-Fingerprint-Agent \
      --why=Secure-biometric-session \
      --mode=block \
      env \
      LUMA_FP6_ENROLLMENT_STAGE="$stage" \
      LUMA_FP6_LISTENER_DAEMON_SHA256="$expected_listener_sha" \
      LUMA_STOCK_ENROLLMENT_CLIENT="$client" \
      LUMA_STOCK_ENROLLMENT_CLIENT_SHA256="$expected_client_sha" \
      LUMA_STOCK_ADAPTER_DIR="$runtime/adapter" \
      LUMA_STOCK_MANAGER_ACCESS_SHIM="$runtime/libluma-servicemanager-access.so" \
      LUMA_STOCK_PERSIST_FINGERPRINT=1 \
      LUMA_STOCK_BIOMETRIC_CLIENT_MODE=authenticate \
      "$smoke" fingerprint >"$log" 2>&1 &
    child=$!
  fi
done
