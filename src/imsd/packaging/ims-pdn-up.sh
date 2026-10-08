#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®
# ims-pdn-up.sh — boot bring-up for the IPv6 `ims` PDN (journal/ims.md s45).
#
# Finds-or-creates the ims ipv6 bearer via ModemManager, connects it, and
# configures the muxed netdev with the MM-assigned address (the s34 recipe,
# automated). Idempotent — safe to run when the PDN is already up. Runs as
# ExecStartPre of imsd.service, so imsd only starts once the PDN exists; a
# nonzero exit fails the unit and systemd retries per Restart/RestartSec.

log() { echo "ims-pdn-up: $*"; }

kv() { mmcli "$@" -K 2>/dev/null; }

bearer_paths() {
    kv -m "$MODEM" | sed -n 's/^modem\.generic\.bearers\.value\[[0-9]*\] *: *//p'
}

# ---- wait for a modem (MM + modem firmware take a while after boot)
n=0
while :; do
    MODEM=$(mmcli -L 2>/dev/null | sed -n 's,.*/Modem/\([0-9]*\).*,\1,p' | head -n1)
    [ -n "$MODEM" ] && break
    n=$((n + 1))
    [ "$n" -ge 60 ] && { log "no modem after 120 s"; exit 1; }
    sleep 2
done
log "modem $MODEM"

# ---- find a connected ims bearer; else find-or-create one and connect it
# (LTE attach can lag boot, so connect attempts retry)
find_ims_bearer() {  # $1 = required bearer.status.connected value
    for B in $(bearer_paths); do
        INFO=$(kv -b "$B") || continue
        echo "$INFO" | grep -q '^bearer\.properties\.apn  *: *ims$' || continue
        echo "$INFO" | grep -q "^bearer\.status\.connected  *: *$1\$" || continue
        echo "$B"
        return 0
    done
    return 1
}

BEARER=$(find_ims_bearer yes)
n=0
while [ -z "$BEARER" ]; do
    n=$((n + 1))
    [ "$n" -gt 10 ] && { log "bearer connect failed after 10 attempts"; exit 1; }
    B=$(find_ims_bearer no)   # reuse a stale disconnected ims bearer
    if [ -z "$B" ]; then
        B=$(mmcli -m "$MODEM" --create-bearer='apn=ims,ip-type=ipv6' 2>/dev/null |
            sed -n 's,.*\(/org/freedesktop/ModemManager1/Bearer/[0-9]*\).*,\1,p')
        [ -n "$B" ] && log "created bearer $B"
    fi
    if [ -n "$B" ] && mmcli -b "$B" --connect >/dev/null 2>&1; then
        BEARER=$B
    else
        log "connect attempt $n failed; retrying in 10 s"
        sleep 10
    fi
done
log "connected: $BEARER"

# ---- configure the muxed netdev with the MM-assigned address
INFO=$(kv -b "$BEARER")
IFACE=$(echo "$INFO" | sed -n 's/^bearer\.status\.interface  *: *//p')
ADDR=$(echo "$INFO" | sed -n 's/^bearer\.ipv6-config\.address  *: *//p')
PREFIX=$(echo "$INFO" | sed -n 's/^bearer\.ipv6-config\.prefix  *: *//p')
if [ -z "$IFACE" ] || [ -z "$ADDR" ]; then
    log "bearer up but no interface/address in mmcli output"
    exit 1
fi
ip link set "$IFACE" up || exit 1
ip -6 addr replace "$ADDR/${PREFIX:-64}" dev "$IFACE" || exit 1

# wait out IPv6 DAD: binding a tentative address gives EADDRNOTAVAIL
# (first boot attempt cost a 120 s systemd retry exactly this way)
n=0
while ip -6 addr show dev "$IFACE" | grep -q tentative; do
    n=$((n + 1))
    [ "$n" -ge 10 ] && { log "address still tentative after 10 s"; break; }
    sleep 1
done
log "$IFACE up, $ADDR/${PREFIX:-64}"

# ---- hand the connected ims netdev to imsd (imsd.service reads
# /run/imsd.env) so the daemon carries no baked-in interface name
echo "DEV=$IFACE" > /run/imsd.env

# ---- open the IMS protected ports in the firewall (rung 5b root cause,
# journal/ims.md s56): pmOS's default nftables INPUT chain is policy-drop and
# explicitly drops inbound on qmapmux*. That prevents a protected server-flow
# SYN from reaching the listener. Opening the ports is a prerequisite for
# network-initiated requests; a physical MT INVITE remains the proof gate.
# 45061/45062 = imsd's protected client/server ports (kPortUc/kPortUs).
# Best-effort: never fail the unit over a missing/foreign firewall.
if nft list table inet filter >/dev/null 2>&1; then
    if ! nft list chain inet filter input | grep -q imsd-protected-ports; then
        nft insert rule inet filter input iifname "qmapmux*" tcp dport 45061-45062 accept comment '"imsd-protected-ports"' &&
        nft insert rule inet filter input iifname "qmapmux*" udp dport 45061-45062 accept comment '"imsd-protected-ports"' &&
        log "nftables: opened protected ports 45061-45062 on qmapmux*" ||
        log "nftables: rule insert failed (continuing)"
    fi
fi
exit 0
