#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Recreate the volatile primary GW provisioning session from the enabled FP6
# eUICC profile. Identifiers stay inside this root-only process and are never
# emitted or persisted.

set -Eeuo pipefail
umask 077

[[ $(id -u) -eq 0 ]] || exit 1
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == 'The Fairphone (Gen. 6)' ]] || exit 1
grep -qw androidboot.slot_suffix=_b /proc/cmdline || exit 1

cleanup() {
  unset status aid
  systemctl start ModemManager.service luma-fp6-cellular-link.service 2>/dev/null || true
}
trap cleanup EXIT INT TERM HUP

systemctl stop luma-fp6-cellular-link.service ModemManager.service
status=$(qmicli -d qrtr://0 --device-open-qmi --uim-get-card-status)
aid=$(printf '%s\n' "$status" | awk '
  /Application type:.*usim/ { wanted=1; next }
  wanted && /Application ID:/ {
    getline
    gsub(/[^0-9A-Fa-f]/, "")
    print
    exit
  }
')
unset status
[[ ${#aid} -ge 16 && ${#aid} -le 64 ]] || exit 1
qmicli -d qrtr://0 --device-open-qmi \
  --uim-change-provisioning-session="session-type=primary-gw-provisioning,activate=yes,slot=2,aid=$aid" \
  >/dev/null
unset aid
systemctl start ModemManager.service luma-fp6-cellular-link.service
trap - EXIT INT TERM HUP
printf 'event=luma_fp6_primary_gw_session state=active identifiers_logged=0\n'
