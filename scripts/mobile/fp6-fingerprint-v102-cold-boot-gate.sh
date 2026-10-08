#!/bin/sh
set -eu

printf 'slot='
grep -o 'androidboot.slot_suffix=_[ab]' /proc/cmdline || true
printf '\nkernel='
uname -r
printf 'state='
systemctl is-system-running || true
printf 'failed='
systemctl --failed --no-legend | wc -l
printf 'modules='
lsmod | grep -E '^(qcomtee|qseecomtee) ' || true
printf 'firmware='
readlink -f /lib/firmware/postmarketos
printf 'credential='
if test -e /var/lib/luma/fingerprint/credential.handle; then
    echo present
else
    echo absent
fi
printf 'faults='
dmesg | tail -n 800 | grep -Eic 'Oops|kernel panic|GPU fault|GMU.*(fault|timeout)|QSEE.*(fault|timeout)|TEE.*(fault|timeout)' || true
