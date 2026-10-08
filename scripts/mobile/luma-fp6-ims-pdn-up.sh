#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only

# Bring up the FP6 IMS IPv6 PDN without disturbing Luma's default data bearer.
# ModemManager's generic Bearers property omits secondary Qualcomm bearers on
# this device, so enumerate the authoritative D-Bus object tree instead.

set -Eeuo pipefail
umask 077

log() { printf 'luma-ims-pdn: %s\n' "$*"; }
kv() { mmcli "$@" -K 2>/dev/null; }

bearer_paths() {
    busctl --system tree org.freedesktop.ModemManager1 --list 2>/dev/null |
        sed -n 's,^\(/org/freedesktop/ModemManager1/Bearer/[0-9][0-9]*\)$,\1,p'
}

find_ims_bearer() {
    local required=$1 bearer info
    while IFS= read -r bearer; do
        [[ -n $bearer ]] || continue
        info=$(kv -b "$bearer") || continue
        grep -Eq '^bearer\.properties\.apn[[:space:]]*:[[:space:]]*ims$' <<<"$info" || continue
        grep -Eq "^bearer\\.status\\.connected[[:space:]]*:[[:space:]]*$required$" <<<"$info" || continue
        printf '%s\n' "$bearer"
        return 0
    done < <(bearer_paths)
    return 1
}

modem=
for _ in $(seq 1 60); do
    modem=$(mmcli -L 2>/dev/null | sed -n 's,.*/Modem/\([0-9][0-9]*\).*,\1,p' | head -n1)
    [[ -n $modem ]] && break
    sleep 2
done
[[ -n $modem ]] || { log 'no modem after 120 seconds'; exit 1; }

# Creating or connecting the IMS bearer while ModemManager is still enabling
# jumps its generic state directly from disabled/enabling to connected. That
# skips Messaging's enable step and leaves the SMS list absent for the entire
# boot. The cellular-link service owns modem enablement; wait until that normal
# registration path and the actual Messaging.List operation are both ready
# before touching any bearer, including a reusable IMS bearer.
messaging_ready=false
for _ in $(seq 1 90); do
    modem_info=$(kv -m "$modem" || true)
    modem_state=$(sed -n 's/^modem\.generic\.state[[:space:]]*:[[:space:]]*//p' <<<"$modem_info")
    registration=$(sed -n 's/^modem\.3gpp\.registration-state[[:space:]]*:[[:space:]]*//p' <<<"$modem_info")
    if [[ $modem_state == registered || $registration == home || $registration == roaming ]]; then
        if mmcli -m "$modem" --messaging-list-sms >/dev/null 2>&1; then
            messaging_ready=true
            break
        fi
    fi
    sleep 2
done
[[ $messaging_ready == true ]] || {
    log 'ModemManager messaging boundary unavailable before IMS bring-up'
    exit 1
}

# A stopped/restarted daemon leaves the carrier bearer and its IPsec SA up,
# while ModemManager may temporarily omit the secondary bearer object. Reuse
# that already-configured interface instead of tearing down a healthy IMS path.
if [[ -r /run/imsd.env ]]; then
    existing_iface=$(sed -n 's/^DEV=//p' /run/imsd.env | head -n1)
    if [[ $existing_iface =~ ^[A-Za-z0-9_.:-]+$ ]] &&
       ip link show dev "$existing_iface" >/dev/null 2>&1 &&
       ip -6 addr show dev "$existing_iface" scope global | grep -q 'inet6 '; then
        printf 'DEV=%s\nAUDIO_USER=luma\n' "$existing_iface" > /run/imsd.env
        chmod 0600 /run/imsd.env
        log 'reusing healthy IMS bearer after Messaging readiness'
        exit 0
    fi
fi

bearer=$(find_ims_bearer yes || true)
for attempt in $(seq 1 10); do
    [[ -n $bearer ]] && break
    candidate=$(find_ims_bearer no || true)
    if [[ -z $candidate ]]; then
        candidate=$(mmcli -m "$modem" --create-bearer='apn=ims,ip-type=ipv6' 2>/dev/null |
            sed -n 's,.*\(/org/freedesktop/ModemManager1/Bearer/[0-9][0-9]*\).*,\1,p')
    fi
    if [[ -n $candidate ]] && mmcli -b "$candidate" --connect >/dev/null 2>&1; then
        bearer=$candidate
        break
    fi
    log "IMS bearer connect attempt $attempt failed"
    sleep 10
done
[[ -n $bearer ]] || { log 'IMS bearer unavailable'; exit 1; }

info=$(kv -b "$bearer")
iface=$(sed -n 's/^bearer\.status\.interface[[:space:]]*:[[:space:]]*//p' <<<"$info")
address=$(sed -n 's/^bearer\.ipv6-config\.address[[:space:]]*:[[:space:]]*//p' <<<"$info")
prefix=$(sed -n 's/^bearer\.ipv6-config\.prefix[[:space:]]*:[[:space:]]*//p' <<<"$info")
[[ -n $iface && -n $address ]] || { log 'IMS bearer lacks an interface or IPv6 address'; exit 1; }

ip link set "$iface" up
ip -6 addr replace "$address/${prefix:-64}" dev "$iface"
for _ in $(seq 1 10); do
    ip -6 addr show dev "$iface" | grep -q tentative || break
    sleep 1
done

printf 'DEV=%s\nAUDIO_USER=luma\n' "$iface" > /run/imsd.env
chmod 0600 /run/imsd.env

if nft list table inet filter >/dev/null 2>&1 &&
   ! nft list chain inet filter input | grep -q imsd-protected-ports; then
    nft insert rule inet filter input iifname 'qmapmux*' tcp dport 45061-45062 accept comment '"imsd-protected-ports"' || true
    nft insert rule inet filter input iifname 'qmapmux*' udp dport 45061-45062 accept comment '"imsd-protected-ports"' || true
fi

log 'IMS bearer ready'
