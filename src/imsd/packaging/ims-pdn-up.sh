#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®
# ims-pdn-up.sh — boot bring-up for the `ims` PDN (journal/ims.md s45).
#
# Finds-or-creates the ims bearer via ModemManager, connects it, and
# configures the muxed netdev with the MM-assigned address (the s34 recipe,
# automated), reads the P-CSCF list the network put in the PDN's PCO from the
# modem (imsd-pcscf) unless PCSCF= names one, and gives the configured or
# the first discovered address (the daemon's fresh target) a host route —
# never a default route (F15 below). Idempotent — safe to run when the PDN
# is already up. Runs as
# ExecStartPre of imsd.service, so imsd starts once the PDN exists — or once
# this script has written IMSD_IDLE= and exited 0 with nothing to do, when
# imsd logs the idle and exits cleanly (idle_exit below); a nonzero exit
# fails the unit and systemd retries per Restart/RestartSec.
#
# Configuration, via the unit's EnvironmentFile /etc/imsd.env (also read
# directly so manual runs behave the same):
#   IMS_APN          ims APN name (default ims)
#   IMS_IP_TYPE      bearer ip-type (default ipv6)
#   IMS_PROFILE_ID   3GPP profile index to connect through (default: looked up
#                    by APN in the modem's profile list; empty/none = connect by
#                    APN string). A profile-indexed call is exempt from the
#                    modem's attach-family forcing that refuses an APN-string
#                    IPv6 request when the attach PDN was granted IPv4-only
#                    (journal/ims 2026-09-16/17 bench: Odido, O2 UK, Swisscom).
#   IMS_REG_TIMEOUT  max seconds to wait for network registration before
#                    connect attempts start counting anyway (default 300)
#   PCSCF            one P-CSCF address, by hand: it wins over discovery
#                    (which is then skipped) and gets the host route; unset,
#                    imsd-pcscf reads the PDN's PCO list from the modem and
#                    this script hands it to imsd as PCSCF_DISCOVERED= in
#                    /run/imsd.env, the first of them routed
#   IMSD_PCSCF       the reader's path (default /usr/libexec/imsd-pcscf) — a
#                    bench seam, from the environment only (not from the file)
#   IMSD_ENV         the configuration file's path (default /etc/imsd.env) —
#                    a bench seam. The file is optional (overrides only); its
#                    existence changes nothing

log() { echo "ims-pdn-up: $*"; }

kv() { mmcli "$@" -K 2>/dev/null; }

# mmcli's multi-line error text as one journal-friendly line
squash() { printf '%s' "$1" | tr '\n' ' ' | sed 's/  */ /g'; }

modem_state()  { kv -m "$MODEM" | sed -n 's/^modem\.generic\.state *: *//p'; }
packet_state() { kv -m "$MODEM" | sed -n 's/^modem\.3gpp\.packet-service-state *: *//p'; }

bearer_paths() {
    kv -m "$MODEM" | sed -n 's/^modem\.generic\.bearers\.value\[[0-9]*\] *: *//p'
}

# A disabled modem has its radio off (airplane mode): leave it alone. A bearer
# connect on it fails with no-service and leaves ModemManager's state
# `enabled` with the modem still in low power; switching the radio back on
# then no longer brings the modem up (dev phone 2026-10-06).
radio_off() { [ "$(modem_state)" = disabled ]; }
# A locked SIM (a PIN not entered yet) is the same kind of state the user
# controls: the modem registers once the PIN is in, so like a disabled radio
# it exits non-zero and the unit's restart tries again.
sim_locked() { [ "$(modem_state)" = locked ]; }
unlock_required() { kv -m "$MODEM" | sed -n 's/^modem\.generic\.unlock-required *: *//p'; }

IMSD_ENV=${IMSD_ENV:-/etc/imsd.env}
envval() {  # $1 = key — for manual runs; under systemd the vars are inherited
    sed -n "s/^$1=//p" "$IMSD_ENV" 2>/dev/null | tail -n1 | tr -d '"'
}
IP_TYPE=${IMS_IP_TYPE:-$(envval IMS_IP_TYPE)}
IP_TYPE=${IP_TYPE:-ipv6}
IMS_APN=${IMS_APN:-$(envval IMS_APN)}
IMS_APN=${IMS_APN:-ims}
PROFILE_ID=${IMS_PROFILE_ID:-$(envval IMS_PROFILE_ID)}
REG_TIMEOUT=${IMS_REG_TIMEOUT:-$(envval IMS_REG_TIMEOUT)}
REG_TIMEOUT=${REG_TIMEOUT:-300}

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

# ---- nothing to do: a clean exit, not a retry loop. imsd reads IMSD_IDLE=
# from /run/imsd.env, logs it and exits 0, which Restart=on-failure leaves
# alone — the unit stays inactive until the next boot, a `systemctl start
# imsd` or a start of imsd-ofonod (its Wants=imsd.service, the Phosh image).
# Installing the package and inserting a SIM is the opt-in; no configuration
# file changes what follows. The dead ends are facts: no SIM (the modem's
# `failed` state with a SIM reason; final until ModemManager re-probes;
# checked here and while waiting for registration below), a profile-indexed
# connect series refused ten times in a row — each a persistent 3GPP cause
# in ModemManager's typed error (not subscribed, unknown APN, barred, not
# authorised in this PLMN, authentication failed, the service option not
# supported, the wrong PDN type), with packet service; counted only while the
# modem reads registered, connecting or connected with packet service not
# detached — any other read, a timeout or a transient cause starts the count
# again — and — the daemon's own, with the same clean exit — a
# bring-up that ends with no P-CSCF (none configured, the PCO names none)
# and no registration of imsd's own to resume. One attempt per boot (or per
# such imsd-ofonod start). What is not a fact exits 1 like a disabled radio
# and the restart retries: ten failures without coverage, a SIM still waiting
# for its PIN, a modem without an ims profile (the APN-string connect is
# tried), an APN-string series (the modem's own wall under an IPv4 attach,
# F6), a P-CSCF reader that did not answer (PCSCF_DISCOVERY=failed, below).
idle_exit() {  # $1 = key, $2 = log text
    log "$2"
    printf 'IMSD_IDLE=%s\n' "$1" > /run/imsd.env
    log "wrote /run/imsd.env: IMSD_IDLE=$1"
    exit 0
}
sim_check() {
    [ "$(modem_state)" = failed ] || return 0
    REASON=$(kv -m "$MODEM" | sed -n 's/^modem\.generic\.state-failed-reason *: *//p')
    case "$REASON" in
        sim-missing|esim-without-profiles|sim-error)
            idle_exit no-sim "no SIM ($REASON): not bringing the ims PDN up — systemctl start imsd once one is in" ;;
    esac
}
sim_check

# ---- the ims profile index (WDS profile list), unless configured. The
# lookup itself can fail while ModemManager is still enabling the modem; a
# failed lookup is not "no profile" (the APN-string request it would fall
# back to is the one the modem refuses under an IPv4 attach), so it is
# retried after the registration wait; a list without the APN is final —
# the APN string is used — only once the modem is registered.
LOOKUP=pending
if [ -n "$PROFILE_ID" ]; then
    [ "$PROFILE_ID" = none ] && PROFILE_ID=
    LOOKUP=done
fi
lookup_profile() {  # $1 = modem state, $2 = log suffix; sets PROFILE_ID, LOOKUP=done on a final answer
    [ "$LOOKUP" = done ] && return 0
    if OUT=$(qmicli -d qrtr://0 --wds-get-profile-list=3gpp 2>&1); then
        # a list with no profiles at all is the modem still coming up, not
        # a modem without an ims profile (dev phone cold boot 2026-09-20
        # 16:47: exit 0, no entries, index 2 present a minute later)
        if ! printf '%s\n' "$OUT" | grep -q -E '^[[:space:]]*\[[0-9]+\] 3gpp'; then
            log "ims profile lookup returned no profiles$2"
            return 0
        fi
        PROFILE_ID=$(printf '%s\n' "$OUT" | awk -v apn="$IMS_APN" '
            /^[ \t]*\[[0-9]+\] 3gpp/ { idx = $1; gsub(/[^0-9]/, "", idx) }
            /APN:/ { a = $0; sub(/.*APN: '"'"'/, "", a); sub(/'"'"'.*/, "", a);
                     if (tolower(a) == tolower(apn) && idx != "") { print idx; exit } }')
        if [ -n "$PROFILE_ID" ]; then
            LOOKUP=done
            log "ims profile: index $PROFILE_ID (apn $IMS_APN)"
            return 0
        fi
        # a list without the APN is final only once the modem is registered:
        # while it enables, the list can be partial (dev phone cold boot
        # 2026-09-24 18:18: entries but no ims at `enabling`, ims listed when
        # read minutes later), and on a walled carrier the APN-string fallback
        # that would follow is the request the modem refuses
        case "$1" in
            registered|connecting|connected)
                LOOKUP=done
                log "ims profile: none for apn $IMS_APN, connecting by APN string" ;;
            *)  log "ims profile: not in the list yet$2" ;;
        esac
    else
        log "ims profile lookup failed$2: $(squash "$OUT")"
    fi
}
STATE=$(modem_state)
lookup_profile "$STATE" " (modem state: $STATE)"

# ---- find a connected ims bearer; else find-or-create one and connect it.
# APN names are case-insensitive. A DISCONNECTED bearer is reused only when
# it is the request this run would make (same profile index, or same APN when
# connecting by APN string): a leftover APN-string bearer from a run whose
# lookup failed is the walled request itself, and reconnecting it fails on
# every restart until the modem is reset (field: O2 UK, 2026-09-20).
has() { printf '%s\n' "$INFO" | grep -qi "^bearer\.$1  *: *$2\$"; }
find_ims_bearer() {  # $1 = required bearer.status.connected value; $2 = "relaxed" ignores ip-type
    for B in $(bearer_paths); do
        INFO=$(kv -b "$B") || continue
        has status.connected "$1" || continue
        if [ -n "$PROFILE_ID" ]; then
            if [ "$1" = yes ]; then
                has properties.profile-id "$PROFILE_ID" || has properties.apn "$IMS_APN" || continue
            else
                has properties.profile-id "$PROFILE_ID" || continue
            fi
        else
            has properties.apn "$IMS_APN" || continue
        fi
        [ "$2" = relaxed ] || has properties.ip-type "$IP_TYPE" || continue
        echo "$B"
        return 0
    done
    return 1
}
# leftover disconnected APN-string ims bearers when this run connects by
# profile index: never reused (above), deleted so a later run without an
# index cannot pick one up either
delete_stale_bearers() {
    [ -n "$PROFILE_ID" ] || return 0
    for B in $(bearer_paths); do
        INFO=$(kv -b "$B") || continue
        has status.connected no || continue
        has properties.apn "$IMS_APN" || continue
        has properties.profile-id "$PROFILE_ID" && continue
        OUT=$(mmcli -m "$MODEM" --delete-bearer="$B" 2>&1) &&
        log "deleted stale bearer $B (apn $IMS_APN, no profile index)" ||
        log "could not delete stale bearer $B: $(squash "$OUT")"
    done
}

BEARER=$(find_ims_bearer yes)

# ---- gate the connect attempts on network registration: the 10x10 s window
# below is shorter than some carriers' post-boot attach (measured ~2 min), so
# without this every attempt can fail on no-service and the retries end
# before the network is even attached
if [ -z "$BEARER" ]; then
    waited=0 last=
    while :; do
        STATE=$(modem_state)
        case "$STATE" in registered|connecting|connected) break ;; failed) sim_check ;; esac
        [ "$STATE" != "$last" ] && log "waiting for registration (state: ${STATE:-unknown})"
        last=$STATE
        if [ "$waited" -ge "$REG_TIMEOUT" ]; then
            if [ "$STATE" = disabled ]; then
                log "modem still disabled after $REG_TIMEOUT s (radio off): not connecting"
                exit 1
            fi
            if [ "$STATE" = locked ]; then
                log "SIM locked ($(unlock_required)) after $REG_TIMEOUT s: not connecting until the PIN is entered"
                exit 1
            fi
            log "not registered after $REG_TIMEOUT s — attempting anyway"
            break
        fi
        sleep 5
        waited=$((waited + 5))
    done
    log "modem state: $(modem_state), packet service: $(packet_state)"
    # a lookup with no final answer yet: it failed, or its list lacked the
    # APN while the modem was still coming up
    n=0
    while [ "$LOOKUP" != done ] && [ "$n" -lt 3 ]; do
        n=$((n + 1))
        sleep 5
        STATE=$(modem_state)
        lookup_profile "$STATE" " (retry $n, modem state: $STATE)"
    done
    if [ "$LOOKUP" != done ]; then
        PROFILE_ID=
        log "ims profile: lookup keeps failing, connecting by APN string"
    fi
    BEARER=$(find_ims_bearer yes)   # the index may now match a connected profile-indexed PDN
    [ -n "$BEARER" ] || delete_stale_bearers
fi

n=0
REFUSED=0   # connects refused in a row, each a persistent 3GPP cause with the modem registered/connecting/connected and packet service not detached (stricter than the daemon's PacketServiceUp)
while [ -z "$BEARER" ]; do
    n=$((n + 1))
    if [ "$n" -gt 10 ]; then
        # only a profile-indexed series the network refused says anything
        # about it: an APN-string series (a lookup that kept failing, a late
        # registration) meets the modem's own wall under an IPv4 attach (F6),
        # which the restart's fresh lookup gets past, and a failure without
        # packet service is a coverage gap, not a refusal — counted only while
        # the modem reads registered, connecting or connected with packet
        # service not `detached` (a CS-registered modem still attaching) and
        # only for a persistent 3GPP cause in ModemManager's typed error; any
        # other read, `disconnecting`, `unknown` or an unanswered mmcli among
        # them, and a timeout or a transient cause, reset it — stricter than
        # the daemon's PacketServiceUp, which counts an unknown state as
        # service — so the
        # count is of refusals in a row, each with packet service at the time,
        # never one state read after the series: a series that failed without
        # coverage and found the network at its end exits 1 like any other,
        # and the restart retries on the attached modem
        [ -z "$PROFILE_ID" ] || [ "$REFUSED" -lt 10 ] || idle_exit no-ims-pdn "bearer connect refused 10 times in a row by profile index with packet service and a persistent cause (last: $(squash "$OUT")): no ims PDN for this SIM here — systemctl start imsd to try again"
        log "bearer connect failed after 10 attempts (modem state: $(modem_state), last: $(squash "$OUT"))"; exit 1
    fi
    radio_off && { log "modem disabled (radio off): not connecting"; exit 1; }
    sim_locked && { log "SIM locked ($(unlock_required)): not connecting until the PIN is entered"; exit 1; }
    B=$(find_ims_bearer no)   # reuse a stale disconnected ims bearer of this run's shape
    if [ -z "$B" ]; then
        if [ -n "$PROFILE_ID" ]; then
            SPEC="profile-id=$PROFILE_ID,ip-type=$IP_TYPE"
        else
            SPEC="apn=$IMS_APN,ip-type=$IP_TYPE"
        fi
        OUT=$(mmcli -m "$MODEM" --create-bearer="$SPEC" 2>&1)
        B=$(printf '%s' "$OUT" | sed -n 's,.*\(/org/freedesktop/ModemManager1/Bearer/[0-9]*\).*,\1,p')
        if [ -n "$B" ]; then
            log "created bearer $B ($SPEC)"
        else
            log "create-bearer attempt $n failed: $(squash "$OUT"); retrying in 10 s"
            REFUSED=0   # not the network's answer to a connect
            sleep 10
            continue
        fi
    fi
    if OUT=$(mmcli -b "$B" --connect 2>&1); then
        BEARER=$B
    else
        ST=$(modem_state); PS=$(packet_state)
        log "connect attempt $n failed (state: ${ST:-unknown}, packet service: ${PS:-unknown}): $(squash "$OUT"); retrying in 10 s"
        # a refusal is the network's answer, as ModemManager types it: a
        # persistent 3GPP cause — not subscribed, unknown APN, barred, not
        # authorised in this PLMN, authentication failed, the service option
        # not supported, the wrong PDN type for this attach — with the modem
        # attached. A timeout (D-Bus, esm-proc-timeout), a transient cause
        # (insufficient resources, out of order, a network failure) or any
        # other error starts the count again, as a read without packet service
        # does: an outage must not idle VoLTE until the next boot
        PERSISTENT=
        case "$OUT" in
            *MobileEquipment.ServiceOptionNotSubscribed:*|*MobileEquipment.MissingOrUnknownApn:*|*MobileEquipment.OperatorDeterminedBarring:*|*MobileEquipment.ServiceOptionNotAuthorizedInPlmn:*|*MobileEquipment.UserAuthenticationFailed:*|*MobileEquipment.ServiceOptionNotSupported:*|*MobileEquipment.Ipv4OnlyAllowed:*|*MobileEquipment.Ipv6OnlyAllowed:*) PERSISTENT=1 ;;
        esac
        case "$ST" in
            registered|connecting|connected) if [ "$PS" = detached ] || [ -z "$PERSISTENT" ]; then REFUSED=0; else REFUSED=$((REFUSED + 1)); fi ;;
            *) REFUSED=0 ;;
        esac
        case "$OUT" in *call-already-present*)
            # the PDN is up on a bearer this run did not recognise (ip-type or
            # APN spelling); use it rather than ask the modem for a second one
            if B=$(find_ims_bearer yes relaxed); then
                log "adopting the connected bearer $B"
                BEARER=$B
                continue
            fi ;;
        esac
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
# ---- the ims PDN must never be the phone's IPv6 default route (F15). The
# PGW's router advertisement offers a default router; on IPv4-only wifi
# that is the phone's only v6 default, so every AAAA-first client (apk,
# browsers) sends its SYN into the IMS walled garden, which drops it
# silently (dev phone 2026-09-20: a 9-min apk hang). imsd only talks to the
# P-CSCF (host route below) and to media peers (imsd installs those host
# routes per call); the RA's prefix and MTU are still applied (its hop
# limit is not — the kernel default 64 stays). Per-netdev, via /proc, and
# before link-up: the router solicitation goes out once the link-local
# address passes DAD, ~1–2 s after link-up, and the RA answers at once.
if ! echo 0 > "/proc/sys/net/ipv6/conf/$IFACE/accept_ra_defrtr" 2>/dev/null; then
    log "could not set accept_ra_defrtr=0 on $IFACE (continuing)"
fi
# the PDN's MTU as the modem reports it (1280 here), on the link itself:
# the P-CSCF host route makes the P-CSCF reachable before the RA's MTU
# option has been processed, and a send in that window would otherwise be
# sized by the link's default MTU. NetworkManager does the same for its
# bearers. An RA MTU no larger than the link's is still applied.
MTU=$(echo "$INFO" | sed -n 's/^bearer\.ipv6-config\.mtu  *: *//p')
case "$MTU" in ''|--|*[!0-9]*) MTU= ;; esac
if [ -n "$MTU" ] && [ "$MTU" -ge 1280 ]; then
    ip link set "$IFACE" mtu "$MTU" || log "could not set mtu $MTU on $IFACE (continuing)"
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
log "$IFACE up, $ADDR/${PREFIX:-64}, ipv6 mtu $(cat "/proc/sys/net/ipv6/conf/$IFACE/mtu" 2>/dev/null)"

# a default route an earlier RA already installed (the link was up before
# this run: an imsd restart) — accept_ra_defrtr=0 leaves an existing one in
# place until its lifetime runs out. Any ::/0 via this netdev goes, by the
# policy above.
if [ -n "$(ip -6 route show default dev "$IFACE" 2>/dev/null)" ]; then
    ip -6 route flush default dev "$IFACE" && log "dropped the RA default route on $IFACE"
fi
# ---- the P-CSCF. PCSCF= when the admin set one (one IPv6 address; it wins
# and discovery is skipped), else the list the network put in the PDN's PCO,
# read from the modem by imsd-pcscf: a QMI WDS client bound to this netdev's
# mux data port — the binding ModemManager gives its own clients, and only
# such a client is shown the call's settings. The list goes to imsd as
# PCSCF_DISCOVERED= (its own name: this script inherits the previous run's
# /run/imsd.env through the unit, so a stale discovered value must never
# pass for the admin's PCSCF=, and it is never read from the environment
# here). The PDN is brought up IPv6-only, so only IPv6 addresses are usable;
# IPv4 ones and names are logged and dropped. Nothing usable is not this
# script's failure, and PCSCF_DISCOVERY= in the hand-off tells the daemon
# which kind it was: `none` — the PCO carried no IPv6 P-CSCF (the reader's
# exit 2), a fact: a registration of imsd's own that can be resumed is
# resumed and stays up, else the daemon idles (its `idle:` exit; after a
# resumable pair's failed attempt the fatal and the restart) — or `failed` —
# the reader did not answer (an error, a timeout, nothing printed), no fact:
# a daemon with nothing to resume exits 1 and the unit's restart runs
# discovery again. `found` and `skipped` (PCSCF= set, an IPv4-only PDN, no
# rmnet mux id) are the other two values.
# The P-CSCF is reachable through the ims PDN only: a host route stands in
# for the default — `dev` without a gateway (a raw-IP netdev without
# header_ops, so the kernel resolves no neighbours), the shape imsd uses for
# media peers; of a discovered list the first address — the daemon's fresh
# target — gets one here; imsd routes the P-CSCF it uses itself as well — the
# fresh target before a fresh registration, the resumed SA peer once its SAs
# are confirmed — with the same idempotent replace as a call's media peer, so
# a target this script did not route (a list set in /etc/imsd.env, a route
# lost with the PDN) is reachable.
# Anything else on the IMS network (its DNS servers, a traceroute
# target) needs its own host route. Without the route imsd cannot reach the
# P-CSCF, so a route failure is the unit's failure.
route_pcscf() {  # $1 = address
    OUT=$(ip -6 route replace "$1/128" dev "$IFACE" 2>&1) ||
        { log "host route to P-CSCF $1 failed: $(squash "$OUT")"; exit 1; }
    log "host route to P-CSCF $1 dev $IFACE"
}
PCSCF=${PCSCF:-$(envval PCSCF)}
DISCOVERED=
DISCOVERY=skipped
case "$PCSCF" in
    '')
        if [ "$IP_TYPE" = ipv4 ]; then
            log "P-CSCF discovery: not for IMS_IP_TYPE=$IP_TYPE — the bring-up configures only the PDN's IPv6 address"
        else
            MUX=$(ip -d link show "$IFACE" 2>/dev/null | sed -n 's/.*rmnet mux_id \([0-9]*\).*/\1/p' | head -n1)
            if [ -z "$MUX" ]; then
                log "P-CSCF discovery: no rmnet mux id on $IFACE (ip -d link); set PCSCF="
            else
                DISCOVERY=failed
                OUT=$("${IMSD_PCSCF:-/usr/libexec/imsd-pcscf}" --mux "$MUX" ${PROFILE_ID:+--profile "$PROFILE_ID"} --timeout 10 2>&1); rc=$?
                V6=; SHOWN=; OTHER=; FLAGS=; ERR=; n=0
                while IFS= read -r line; do
                    case "$line" in
                        '') ;;
                        v4\ *|fqdn\ *) OTHER="${OTHER:+$OTHER }$line" ;;
                        flag\ *) FLAGS="${FLAGS:+$FLAGS, }${line#flag }" ;;
                        *[!0-9a-f:]*) ERR="${ERR:+$ERR }$line" ;;
                        *:*) [ "$n" -ge 8 ] && continue; n=$((n + 1))
                             V6="${V6:+$V6 }$line"; DISCOVERED="${DISCOVERED:+$DISCOVERED,}$line"; SHOWN="${SHOWN:+$SHOWN, }$line" ;;
                        *) ERR="${ERR:+$ERR }$line" ;;
                    esac
                done <<EOF
$OUT
EOF
                if [ -n "$V6" ]; then
                    DISCOVERY=found
                    log "P-CSCF discovered: $SHOWN${FLAGS:+ ($FLAGS)}"
                    [ -n "$OTHER" ] && log "P-CSCF discovery also gave $OTHER (not usable on the IPv6-only ims PDN)"
                    route_pcscf "${V6%% *}"   # the first: the daemon's fresh target; imsd routes the P-CSCF it uses itself as well (the fresh target, the resumed peer)
                else
                    case $rc in
                        0) log "P-CSCF discovery: nothing usable (imsd-pcscf printed no address${ERR:+: $ERR})" ;;
                        2) DISCOVERY=none; log "P-CSCF discovery: nothing usable (the PCO carried no IPv6 P-CSCF${OTHER:+; it gave $OTHER}${FLAGS:+; $FLAGS})" ;;
                        *) log "P-CSCF discovery: nothing usable (imsd-pcscf rc $rc${ERR:+: $ERR})" ;;
                    esac
                fi
            fi
        fi ;;
    *,*) log "PCSCF '$PCSCF' is a list; one address here (a list is PCSCF_DISCOVERED, the bring-up's)"; exit 1 ;;
    *\[*) log "PCSCF '$PCSCF': write the bare address, as ip prints it (no brackets)"; exit 1 ;;
    *:*) log "P-CSCF $PCSCF configured: discovery skipped"; route_pcscf "$PCSCF" ;;
    *)   log "PCSCF '$PCSCF' is not an IPv6 address; the ims PDN is brought up IPv6-only"; exit 1 ;;
esac

# ---- hand the connected ims netdev, the PDN's own address and the
# discovered P-CSCF list to imsd (imsd.service reads /run/imsd.env): the
# daemon carries no baked-in interface name and registers on the address
# the network assigned — not on the kernel's temporary SLAAC address from
# the RA, which the netdev lists first and which is deprecated after a day
# and removed after two (ims ledger F18; the SAs and the Contact would go
# with it). PCSCF_DISCOVERED= is written on every run, empty when nothing
# was discovered or PCSCF= is set; PCSCF_DISCOVERY= says how it came to be
# (found, none, failed, skipped — above).
# The dead end after the PDN is up — nothing to register to (no PCSCF=,
# nothing discovered, no registration of imsd's own in the kernel to resume)
# — is the daemon's to decide (its `idle:` exit before it takes the bus name,
# §7.3): it knows the fresh target, the kernel's SA pair and the holds, and
# PCSCF_DISCOVERY= tells it whether an empty list is the PCO's answer (none:
# the idle) or the reader's failure (failed: exit 1, the restart runs
# discovery again). The hand-off is written on every run.
printf 'DEV=%s\nLOCAL=%s\nPCSCF_DISCOVERED=%s\nPCSCF_DISCOVERY=%s\n' "$IFACE" "$ADDR" "$DISCOVERED" "$DISCOVERY" > /run/imsd.env
log "wrote /run/imsd.env: DEV=$IFACE LOCAL=$ADDR PCSCF_DISCOVERED=$DISCOVERED PCSCF_DISCOVERY=$DISCOVERY"

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
