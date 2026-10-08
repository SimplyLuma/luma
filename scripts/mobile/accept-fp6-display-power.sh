#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Bounded physical acceptance for the FP6 short power-button display policy.
# Run this as the active graphical user over SSH, then press the phone's power
# button once to blank and once to wake.  The script only reads live state.

set -euo pipefail
umask 077

duration=${1:-25}
case $duration in
  ''|*[!0-9]*) printf 'error: duration must be an integer in seconds\n' >&2; exit 2 ;;
esac
[ "$duration" -ge 15 ] && [ "$duration" -le 60 ] || {
  printf 'error: duration must be between 15 and 60 seconds\n' >&2
  exit 2
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
read_property() {
  busctl --user get-property "$1" "$2" "$3" "$4"
}

[ "$(id -u)" -ne 0 ] || die 'run as the active graphical user, not root'
[ "$(uname -r)" = 7.1.2 ] || die 'kernel release differs'
[ "$(tr -d '\000' </proc/device-tree/model)" = 'The Fairphone (Gen. 6)' ] ||
  die 'device identity differs'
for tool in awk busctl cat date find grep id journalctl loginctl pgrep sed sleep \
  sudo systemctl systemd-inhibit tr uname wc; do
  command -v "$tool" >/dev/null 2>&1 || die "missing tool: $tool"
done

user_id=$(id -u)
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$user_id}
export DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}
[ -S "$XDG_RUNTIME_DIR/bus" ] || die 'graphical user bus is unavailable'
sudo -n true 2>/dev/null || die 'passwordless diagnostic sudo is unavailable'

power_nodes=()
for name_path in /sys/class/input/event*/device/name; do
  [ -f "$name_path" ] || continue
  [ "$(cat "$name_path")" = pmic_pwrkey ] || continue
  power_nodes+=("/dev/input/$(printf '%s' "$name_path" | awk -F/ '{print $5}')")
done
[ "${#power_nodes[@]}" -eq 1 ] ||
  die "expected one pmic_pwrkey event node, found ${#power_nodes[@]}"
[ -r "${power_nodes[0]}" ] || die 'active user cannot read the PMIC power key'
backlight_nodes=()
for backlight in /sys/class/backlight/*; do
  [ -d "$backlight" ] || continue
  backlight_nodes+=("$backlight")
done
[ "${#backlight_nodes[@]}" -eq 1 ] ||
  die "expected one panel backlight, found ${#backlight_nodes[@]}"
[ -r "${backlight_nodes[0]}/bl_power" ] || die 'panel power state is unreadable'

display_bus=org.gnome.Mutter.DisplayConfig
display_path=/org/gnome/Mutter/DisplayConfig
display_interface=org.gnome.Mutter.DisplayConfig
handheld_bus=org.project_luma.Handheld
handheld_path=/org/project_luma/Handheld
handheld_interface=org.project_luma.Handheld1
screensaver_bus=org.gnome.ScreenSaver
screensaver_path=/org/gnome/ScreenSaver
screensaver_interface=org.gnome.ScreenSaver

if systemctl --user is-active luma-shell-session.target >/dev/null 2>&1; then
  renderer=luma-shell
  [ "$(systemctl --user is-active luma-power-key-broker.service)" = active ] ||
    die 'Luma power-key broker is not active in the Luma Shell session'
  session_identity=$(systemctl --user show luma-shell-session.target \
    --property=InvocationID --value)
  inhibitor_pattern='Luma Handheld Shell'
elif phosh_pid=$(pgrep -xo phosh); then
  renderer=phosh
  [ "$(systemctl --user is-active luma-power-key-broker.service 2>/dev/null || true)" != active ] ||
    die 'Luma raw power-key broker must be inactive while Phosh owns the key'
  session_identity=$phosh_pid
  inhibitor_pattern='Phosh handling power key'
else
  die 'neither the Luma Shell nor Phosh renderer is active'
fi
[ -n "$session_identity" ] || die 'graphical session identity is missing'
inhibitors=$(systemd-inhibit --list --no-legend | grep -c "$inhibitor_pattern" || true)
[ "$inhibitors" -eq 1 ] ||
  die "expected one $renderer power-key inhibitor, found $inhibitors"

read_session_state() {
  if [ "$renderer" = luma-shell ]; then
    read_property "$handheld_bus" "$handheld_path" \
      "$handheld_interface" Sleeping | awk '{print $2}'
  else
    busctl --user call "$screensaver_bus" "$screensaver_path" \
      "$screensaver_interface" GetActive | awk '{print $2}'
  fi
}

initial_mode=$(read_property "$display_bus" "$display_path" \
  "$display_interface" PowerSaveMode | awk '{print $2}')
initial_bl_power=$(cat "${backlight_nodes[0]}/bl_power")
initial_session_state=$(read_session_state)
[ "$initial_mode" -eq 0 ] || die "display does not begin awake: mode $initial_mode"
[ "$initial_bl_power" -eq 0 ] ||
  die "panel does not begin powered: bl_power $initial_bl_power"
[ "$renderer" != luma-shell ] || [ "$initial_session_state" = false ] ||
  die 'Luma Shell begins in sleeping state'

start_time=$(date -Is)
printf 'LUMA_FP6_DISPLAY_POWER_ACCEPTANCE_VERSION=2\n'
printf 'BOOT_ID=%s\n' "$(cat /proc/sys/kernel/random/boot_id)"
printf 'RENDERER=%s\n' "$renderer"
printf 'DURATION_SECONDS=%s\n' "$duration"
printf 'INITIAL_POWER_SAVE_MODE=%s\n' "$initial_mode"
printf 'INITIAL_BL_POWER=%s\n' "$initial_bl_power"
printf 'INITIAL_SESSION_STATE=%s\n' "$initial_session_state"
printf 'OWNER_ACTION=short_press_once_to_blank_then_once_to_wake\n'
printf 'COUNTDOWN_SECONDS=3\n'
sleep 3

saw_off=false
saw_return=false
mode_trace=0
bl_power_trace=$initial_bl_power
session_state_trace=$initial_session_state
samples=$((duration * 5))
for _sample in $(awk -v count="$samples" 'BEGIN { for (i=1; i<=count; i++) print i }'); do
  mode=$(read_property "$display_bus" "$display_path" \
    "$display_interface" PowerSaveMode | awk '{print $2}')
  bl_power=$(cat "${backlight_nodes[0]}/bl_power")
  session_state=$(read_session_state)
  case $mode in
    0|1|2|3) ;;
    *) die "unexpected display power mode: $mode" ;;
  esac
  case $session_state in
    true|false) ;;
    *) die "unexpected session state: $session_state" ;;
  esac
  case $bl_power in
    0|1|2|3|4) ;;
    *) die "unexpected panel bl_power: $bl_power" ;;
  esac
  if [ "$mode" -ne 0 ] || [ "$bl_power" -ne 0 ]; then
    saw_off=true
  elif [ "$saw_off" = true ]; then
    saw_return=true
  fi
  case ",$mode_trace," in *",$mode,"*) ;; *) mode_trace=$mode_trace,$mode ;; esac
  case ",$bl_power_trace," in
    *",$bl_power,"*) ;;
    *) bl_power_trace=$bl_power_trace,$bl_power ;;
  esac
  case ",$session_state_trace," in
    *",$session_state,"*) ;;
    *) session_state_trace=$session_state_trace,$session_state ;;
  esac
  sleep 0.2
done

final_mode=$(read_property "$display_bus" "$display_path" \
  "$display_interface" PowerSaveMode | awk '{print $2}')
final_bl_power=$(cat "${backlight_nodes[0]}/bl_power")
final_session_state=$(read_session_state)
if [ "$renderer" = luma-shell ]; then
  final_session_identity=$(systemctl --user show luma-shell-session.target \
    --property=InvocationID --value)
else
  final_session_identity=$(pgrep -xo phosh || true)
fi

printf 'POWER_SAVE_MODE_TRACE=%s\n' "$mode_trace"
printf 'BL_POWER_TRACE=%s\n' "$bl_power_trace"
printf 'SESSION_STATE_TRACE=%s\n' "$session_state_trace"
printf 'SAW_DISPLAY_OFF=%s\n' "$saw_off"
printf 'SAW_DISPLAY_RETURN=%s\n' "$saw_return"
printf 'FINAL_POWER_SAVE_MODE=%s\n' "$final_mode"
printf 'FINAL_BL_POWER=%s\n' "$final_bl_power"
printf 'FINAL_SESSION_STATE=%s\n' "$final_session_state"
printf 'SESSION_IDENTITY_UNCHANGED=%s\n' "$([ "$session_identity" = "$final_session_identity" ] && printf true || printf false)"
printf 'FAILED_USER_UNITS=%s\n' "$(systemctl --user --failed --no-legend --no-pager | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')"
failed_system_units=$(sudo -n systemctl --failed --no-legend --no-pager |
  sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')
printf 'FAILED_SYSTEM_UNITS=%s\n' "$failed_system_units"
printf 'NEW_POWER_LOG_BEGIN\n'
power_log=$(journalctl --user --since "$start_time" --no-pager | grep -E \
  'Luma Handheld: display (sleep|wake)|Luma power-key broker|phosh.*(lock|blank|wake|power)|Setting backlight.*failed' || true)
printf '%s\n' "$power_log"
printf 'NEW_POWER_LOG_END\n'
backlight_faults=$(printf '%s\n' "$power_log" | grep -E 'Setting backlight.*failed' || true)
printf 'NEW_GPU_FAULTS_BEGIN\n'
gpu_faults=$(sudo -n journalctl -k --since "$start_time" --no-pager | grep -Ei \
  'hangcheck|GMU.*timeout|gpu lockup|drm.*ERROR|dpu.*ERROR' || true)
printf '%s\n' "$gpu_faults"
printf 'NEW_GPU_FAULTS_END\n'
printf 'PERSISTENT_MUTATIONS=none\n'

[ "$saw_off" = true ] || die 'physical short press did not blank the panel'
[ "$saw_return" = true ] || die 'second physical short press did not wake the panel'
[ "$final_mode" -eq 0 ] || die 'display did not finish awake'
[ "$final_bl_power" -eq 0 ] || die 'panel did not finish powered'
[ "$renderer" != luma-shell ] || [ "$final_session_state" = false ] ||
  die 'Luma Shell did not finish awake'
[ "$session_identity" = "$final_session_identity" ] ||
  die 'graphical session restarted during the test'
[ "$failed_system_units" -eq 0 ] || die 'system has failed units after display cycle'
[ -z "$backlight_faults" ] || die 'Phosh reported a backlight failure during the display cycle'
[ -z "$gpu_faults" ] || die 'new GPU/display fault appeared during display cycle'
printf 'RESULT=pass\n'
