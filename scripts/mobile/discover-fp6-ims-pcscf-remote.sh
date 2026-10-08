#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Open one isolated IMS WDS session and retrieve the operator PCO without
# exposing its network values. The caller supplies a hash-locked diagnostic
# qmicli/libqmi pair in /tmp/luma-qmi-v2.

set -Eeuo pipefail
umask 077

q=/tmp/luma-qmi-v2/qmicli
lib=/tmp/luma-qmi-v2/libqmi-glib.so.5
ims_profile=${IMS_PROFILE_ID:-}

event() { printf 'event=luma_fp6_ims_pcscf %s\n' "$*"; }
fail() { event state=failed reason="$1" >&2; exit 1; }
cleanup() {
  local status=$?
  set +e
  systemctl start ModemManager.service luma-fp6-cellular-link.service 2>/dev/null
  return "$status"
}
trap cleanup EXIT INT TERM HUP

[[ $(id -u) -eq 0 ]] || fail not_root
[[ $ims_profile =~ ^[1-9][0-9]*$ ]] || fail ims_profile
[[ $(uname -r) == 7.1.2-luma-fp-ims1 ]] || fail kernel
grep -qw androidboot.slot_suffix=_b /proc/cmdline || fail slot
[[ $(sha256sum "$q" | cut -d' ' -f1) == 65df04cc6a5c60f25e68f1a7def8b596e167dd089206be49c281abca615add1b ]] || fail qmicli_hash
[[ $(sha256sum "$lib" | cut -d' ' -f1) == f736cabf9ba566d5dba68e6d0b4e91f7a0939457101c8abe5cb4ca8e85443da9 ]] || fail libqmi_hash

systemctl stop luma-fp6-cellular-link.service ModemManager.service
export LD_LIBRARY_PATH=/tmp/luma-qmi-v2
"$q" --verbose -d qrtr://0 \
  --wds-bind-mux-data-port='mux-id=2,ep-type=embedded,ep-iface-number=1' \
  --wds-start-network="3gpp-profile=$ims_profile,ip-type=ipv6" \
  --wds-get-current-settings >/run/luma-ims-current-settings.txt 2>&1 || fail compound_query
chmod 0600 /run/luma-ims-current-settings.txt
python3 - /run/luma-ims-current-settings.txt /run/imsd-pcscf.env /etc/imsd.env <<'PY'
import ipaddress
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
match = re.search(
    r'type\s+=\s+0x2e\s+<<<<<<\s+length\s+=\s+(\d+)\s+'
    r'<<<<<<\s+value\s+=\s+([0-9A-F:]+)',
    text,
    re.MULTILINE,
)
if not match:
    raise SystemExit("missing IPv6 P-CSCF list")
payload = bytes.fromhex(match.group(2).replace(":", ""))
count = payload[0] if payload else 0
if count < 1 or count > 8 or len(payload) != 1 + count * 16:
    raise SystemExit("invalid IPv6 P-CSCF list")
addresses = [ipaddress.IPv6Address(payload[1 + i * 16:17 + i * 16]) for i in range(count)]
if any(a.is_unspecified or a.is_multicast or a.is_link_local for a in addresses):
    raise SystemExit("invalid IPv6 P-CSCF address")
selected = addresses[-1]
try:
    for line in open(sys.argv[3], encoding="ascii"):
        if line.startswith("PCSCF="):
            current = ipaddress.IPv6Address(line.partition("=")[2].strip())
            if current in addresses:
                selected = current
except (FileNotFoundError, ValueError):
    pass
with open(sys.argv[2], "w", encoding="ascii") as stream:
    stream.write(f"PCSCF={selected}\n")
    stream.write("PCSCF_CANDIDATES=" + ",".join(map(str, addresses)) + "\n")
    stream.write("AUDIO_USER=luma\n")
    # Match the stock QREL registration surface. Some IMS cores use the UE
    # model/build token when deciding whether an MMTEL binding is terminating-
    # capable; this is the truthful build ID of the vendor firmware supporting
    # the FP6 modem, not an invented carrier credential.
    stream.write("USER_AGENT=Fairphone_Fairphone 6_FP6.QREL.16.95.0\n")
    # Preserve transport-flow affinity for terminating SIP requests. The
    # carrier accepts this RFC 5626 shape; actual terminating delivery remains
    # a separate physical gate and is never inferred from REGISTER success.
    stream.write("SIP_OUTBOUND=1\n")
PY
chmod 0600 /run/imsd-pcscf.env
install -m 0600 -o root -g root /run/imsd-pcscf.env /etc/imsd.env
address_count=$(python3 - /run/luma-ims-current-settings.txt <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
match = re.search(r'type\s+=\s+0x2e\s+<<<<<<\s+length\s+=\s+\d+\s+<<<<<<\s+value\s+=\s+([0-9A-F:]+)', text, re.MULTILINE)
payload = bytes.fromhex(match.group(1).replace(":", ""))
print(payload[0])
PY
)
rm -f /run/imsd-pcscf.env
event state=accepted addresses="$address_count" values_logged=0

trap - EXIT INT TERM HUP
cleanup
