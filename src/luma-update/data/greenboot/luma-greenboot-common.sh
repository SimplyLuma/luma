# SPDX-License-Identifier: Apache-2.0
# Sourced by Luma's greenboot health checks: this package's luma-updated check
# and the OS image's display manager, shell and NetworkManager checks.
# Interface: luma_trial_boot (status 0 on a trial boot) and
# luma_check_failed MESSAGE (exits 1 on a trial boot, 0 otherwise).
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

luma_trial_boot() {
  local grubenv environment
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
  if luma_trial_boot; then
    echo "luma health check failed on a trial boot: $*" >&2
    exit 1
  fi
  echo "luma health check (not a trial boot, not enforced): $*" >&2
  exit 0
}
