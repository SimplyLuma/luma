# SPDX-License-Identifier: Apache-2.0
# Sourced by Luma's greenboot health checks: this package's luma-updated check
# and the OS image's display manager, shell and NetworkManager checks.
# Interface: luma_trial_boot: 0 trial, 1 ordinary, 2 unknown native FP6 state.
# luma_check_failed MESSAGE fails for trial or unknown FP6 state; otherwise passes.
# luma_uptime prints whole seconds since boot, for check deadlines.
# FP6 requires the qualified native qbootctl error-return patch and greenboot
# A/B backend. No GRUB fallback is permitted when the FP6 image marker exists.
#
# greenboot 0.16 reboots the computer whenever a required check fails, even on
# an ordinary boot with no update involved. Luma never restarts on its own
# except to recover from a failed update, so Luma's checks enforce only on a
# trial boot: the first boots of a newly finalized deployment, which greenboot
# marks with greenboot_next_deployment_id in the GRUB environment until the
# boot is declared green. On any other boot they report and pass.
#
# When the GRUB environment cannot be read, the helper cannot tell a trial boot
# from an ordinary one. It then passes, as on an ordinary boot, so a machine
# never enters a reboot loop over a missing tool, but it says so at error
# priority in the journal: a failed update would not roll back on such a
# machine, and the OS image's contract test must catch that before release.

# Check deadlines use the monotonic boot clock. Bash's SECONDS follows the wall
# clock, and a machine without a battery-backed RTC (Raspberry Pi) boots with a
# stale date that NTP then moves forward by days, ending every wait at once.
luma_uptime() {
  local up _
  read -r up _ </proc/uptime
  printf '%s\n' "${up%.*}"
}

luma_greenboot_grubenv() {
  printf '%s\n' "${LUMA_GREENBOOT_GRUBENV:-/boot/grub2/grubenv}"
}

luma_greenboot_error() {
  local message="luma greenboot: $*"
  if command -v logger >/dev/null 2>&1; then
    logger --priority user.err --tag luma-greenboot -- "$message" 2>/dev/null || :
  fi
  printf '%s\n' "$message" >&2
}

# Status: 0 trial, 1 stable, 2 unknown/refused. Queries never change a slot.
luma_fp6_trial_boot() {
  local cmdline word suffix slot_arg count result
  [ -f /usr/lib/greenboot/fp6-qbootctl ] && [ ! -L /usr/lib/greenboot/fp6-qbootctl ] || return 2
  [ -f /run/ostree-booted ] && [ -x /usr/bin/qbootctl ] || return 2
  cmdline=$(cat /proc/cmdline) || return 2
  count=0
  for word in $cmdline; do
    case "$word" in
      androidboot.slot_suffix=_a|androidboot.slot_suffix=_b)
        suffix=${word#androidboot.slot_suffix=}; count=$((count + 1)) ;;
      androidboot.slot_suffix=*) return 2 ;;
    esac
  done
  [ "$count" -eq 1 ] || return 2
  result=$(/usr/bin/qbootctl -c 2>&1) || return 2
  [ "$result" = "Current slot: $suffix" ] || return 2
  slot_arg=${suffix#_}
  result=$(/usr/bin/qbootctl -b "$slot_arg" 2>&1) || return 2
  [ "$result" = "SLOT $suffix: is marked bootable" ] || return 2
  result=$(/usr/bin/qbootctl -n "$slot_arg" 2>&1) || return 2
  case "$result" in
    "SLOT $suffix: is not marked successful") return 0 ;;
    "SLOT $suffix: is marked successful") return 1 ;;
    *) return 2 ;;
  esac
}

luma_trial_boot() {
  local grubenv environment state
  if [ -e /usr/lib/greenboot/fp6-qbootctl ] || [ -L /usr/lib/greenboot/fp6-qbootctl ]; then
    if luma_fp6_trial_boot; then return 0; else state=$?; fi
    if [ "$state" -eq 2 ]; then
      luma_greenboot_error "native FP6 trial state unavailable; required health checks fail closed"
    fi
    return "$state"
  fi
  grubenv=$(luma_greenboot_grubenv)
  if [ -n "${LUMA_GREENBOOT_FORCE_TRIAL:-}" ]; then
    return 0
  fi
  if ! command -v grub2-editenv >/dev/null 2>&1; then
    luma_greenboot_error "cannot tell whether this is a trial boot: grub2-editenv is not installed;" \
      "health checks are not enforced and a failed update will not roll back"
    return 1
  fi
  if [ ! -r "$grubenv" ]; then
    luma_greenboot_error "cannot tell whether this is a trial boot: $grubenv is missing or unreadable;" \
      "health checks are not enforced and a failed update will not roll back"
    return 1
  fi
  if ! environment=$(grub2-editenv "$grubenv" list 2>&1); then
    luma_greenboot_error "cannot tell whether this is a trial boot: grub2-editenv could not read $grubenv" \
      "($environment); health checks are not enforced and a failed update will not roll back"
    return 1
  fi
  if printf '%s\n' "$environment" | grep -q '^greenboot_next_deployment_id='; then
    return 0
  fi
  return 1
}

luma_check_failed() {
  local state
  if luma_trial_boot; then state=0; else state=$?; fi
  case "$state" in
    0)
      echo "luma health check failed on a trial boot: $*" >&2
      exit 1 ;;
    2)
      echo "luma health check failed with unknown native FP6 trial state: $*" >&2
      exit 1 ;;
    1)
      echo "luma health check (not a trial boot, not enforced): $*" >&2
      exit 0 ;;
    *)
      echo "luma health check failed with invalid trial state" >&2
      exit 1 ;;
  esac
}
