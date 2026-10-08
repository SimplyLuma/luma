#!/usr/bin/bash
# Install a locally entered FP6 IMEI 2 for IMS registration without logging it.
set -euo pipefail

input=/run/user/1000/luma-fp6-imei2
config=/etc/imsd.env
backup=/var/lib/imsd/imsd.env.pre-device-identity

fail() {
  printf 'event=luma_fp6_ims_identity state=stopped reason=%s identifiers_logged=0\n' "$1" >&2
  exit 1
}

[[ $(id -u) == 0 ]] || fail not_root
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model 2>/dev/null) == *Fairphone* ]] || fail identity
[[ $(uname -r) == 7.1.2-luma-fp-ims1 ]] || fail kernel
grep -Eq 'androidboot[.]slot_suffix=_b|androidboot[.]slot=b' /proc/cmdline || fail slot
[[ -f $input && ! -L $input ]] || fail input_missing
[[ $(stat -c %u:%a "$input") == 1000:600 ]] || fail input_permissions
service_state=$(systemctl is-active luma-fp6-imsd.service || true)
[[ $service_state == active || $service_state == inactive ]] || fail ims_service_state
if [[ $service_state == active ]] && \
    gdbus call --system --dest net.catcrafts.IMS1 \
      --object-path /net/catcrafts/IMS1 \
      --method net.catcrafts.IMS1.GetCalls 2>/dev/null | grep -q "'uni':"; then
  fail call_in_progress
fi

python3 - "$input" "$config" "$backup" <<'PY' || exit 1
import os
import re
import shutil
import sys
import tempfile

source, config, backup = sys.argv[1:]
value = open(source, encoding="ascii").read().strip()

def valid_imei(text):
    if not re.fullmatch(r"35\d{13}", text) or len(set(text)) == 1:
        return False
    total = 0
    for index, char in enumerate(text):
        digit = int(char)
        if index % 2:
            digit *= 2
            digit = digit // 10 + digit % 10
        total += digit
    return total % 10 == 0

if not valid_imei(value):
    print("event=luma_fp6_ims_identity state=stopped reason=input_validation identifiers_logged=0", file=sys.stderr)
    raise SystemExit(1)

with open(config, encoding="utf-8") as handle:
    lines = [line for line in handle.read().splitlines() if not line.startswith("DEVICE_IMEI=")]

os.makedirs(os.path.dirname(backup), mode=0o700, exist_ok=True)
if not os.path.exists(backup):
    shutil.copy2(config, backup)
    os.chmod(backup, 0o600)

fd, temporary = tempfile.mkstemp(prefix=".imsd.env.", dir=os.path.dirname(config), text=True)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\nDEVICE_IMEI=" + value + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, config)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)

os.unlink(source)
PY

systemctl restart luma-fp6-imsd.service

registered=false
for _ in $(seq 1 120); do
  if gdbus call --system --dest net.catcrafts.IMS1 \
      --object-path /net/catcrafts/IMS1 \
      --method net.catcrafts.IMS1.GetStatus 2>/dev/null | grep -q "'registered': <true>"; then
    registered=true
    break
  fi
  sleep 1
done
[[ $registered == true ]] || fail registration_timeout

invocation=$(systemctl show luma-fp6-imsd.service -p InvocationID --value)
[[ -n $invocation ]] || fail service_identity
if journalctl _SYSTEMD_INVOCATION_ID="$invocation" --no-pager -o cat | \
    grep -q 'using machine-derived UUID'; then
  fail uuid_fallback
fi

if journalctl _SYSTEMD_INVOCATION_ID="$invocation" --no-pager -o cat | \
    grep -Eq 'GPU|GMU|hangcheck|Oops|panic|segfault'; then
  fail fault_window
fi

find /tmp -maxdepth 1 -name luma-fp6-imei-entry.py -delete
printf 'event=luma_fp6_ims_identity state=accepted registered=true identifiers_logged=0\n'
