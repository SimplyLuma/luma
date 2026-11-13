<!-- SPDX-License-Identifier: GPL-3.0-only -->
<!-- SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts® -->

# imsd

Userspace IMS/VoLTE daemon for mainline Linux phones.

Most Linux phones drive the modem's internal IMS engine.
imsd implements the whole IMS client in userspace instead wired into
PipeWire so a stock mobile dialer like Plasma Dialer or GNOME Calls under Phosh
can place and receive real VoLTE calls on a commercial network.

Calls verified by community members to be fully working:

| Device | OS | Carrier |
|---|---|---|
| The Fairphone (Gen. 6) | postmarketOS, Linux 7.1.2 | KPN NL |
| The Fairphone (Gen. 6) | postmarketOS, Linux 7.1.2 | Telekom Deutschland GER |
| The Fairphone (Gen. 6) | postmarketOS, Linux 7.1.2 | Phonero |
| The Fairphone (Gen. 6) | postmarketOS, Linux 7.1.2 | Telia Norge |
| The Fairphone (Gen. 6) | postmarketOS, Linux 7.2.0 | o2-uk |

## What it needs

imsd talks to the modem through ModemManager plus `qmicli` over QRTR, installs
its security associations with `ip xfrm` and plays audio through PipeWire, so
in practice: a Qualcomm modem on a mainline kernel with working
WWAN/ModemManager integration, kernel ESP, PipeWire, systemd and polkit.

## Install and configure

For automatic updates add the Catcrafts Alpine repository. Note this does also pull in other packages.

Otherwise just download the latest `imsd` and `imsd-systemd` APKs and install them.

Both can be found in the packages tab:

```
https://forgejo.catcrafts.net/Catcrafts/-/packages/alpine/imsd
```

## Dialers

imsd itself is desktop-agnostic: everything a dialer needs is on the system
bus as `net.catcrafts.IMS1`. A small translation daemon per desktop speaks
the backend protocol its dialer already consumes, so dialer, shell and the
call-history/contacts stack run unmodified.

| Desktop | Daemon | Package | Owns | Consumed by |
|---|---|---|---|---|
| Plasma Mobile | `imsd-dialerd` | `imsd` | `org.kde.telephony.*` (session bus) | plasma-dialer + kde-telephony-daemon, replacing `modem-daemon` |
| Phosh / GNOME Mobile | `imsd-ofonod` | `imsd-ofono` | `org.ofono` (system bus) | GNOME Calls' `ofono` provider plugin |

Plasma Mobile needs nothing beyond the `imsd` package: `imsd-dialerd` is
autostarted in the session in place of plasma-dialer's `modem-daemon`, whose
own autostart must be off — the package hides it for accounts created after
the install (`/etc/skel`); an existing account hides it itself with a
`Hidden=true` copy of `org.kde.modem.daemon.desktop` in `~/.config/autostart`.

For Phosh and GNOME Mobile, `apk add imsd-ofono` enables `imsd-ofonod` and
points GNOME Calls at its `ofono` provider (a gschema override; the per-user
equivalent is `gsettings set org.gnome.Calls autoload-plugins "['ofono']"`).
Do not run it alongside a real `ofonod`.

Call control is polkit's `net.catcrafts.IMS1.Voice`: the active local
session and root may place, answer and end calls; an ssh login may not.

## Full config:

Configuration is environment variables in `/etc/imsd.env`, none are required.

| Variable | Default | Meaning |
|---|---|---|
| `PCSCF` | *(the first of `PCSCF_DISCOVERED`)* | P-CSCF address (IPv6), exactly one — not a comma-separated list — in RFC 5952 §4 form (lowercase, leading zeros dropped, the longest zero run compressed, no brackets — what `ip` prints for a plain IPv6 address; an IPv4-mapped address only as hex, `::ffff:a00:1` — the dotted spelling RFC 5952 §5 and `ip` use for it is refused); upper case, leading zeros, an uncompressed run or brackets are refused at start with the spelling to use (in the packaged unit the bring-up script refuses brackets first, with the rule), a dotted-quad tail with the rule; before 1.0.0 an upper-case, zero-padded or uncompressed `PCSCF=` matched no SA in `ip xfrm`'s text and registered fresh at every restart — an upgraded phone with such a setting stops at start with the spelling to use. Set, it wins over `PCSCF_DISCOVERED`. It is where a fresh registration goes; a registration resumed from the kernel's SAs stays on the P-CSCF it was made to until its next fresh registration. With neither this nor `PCSCF_DISCOVERED` there is no fresh registration: a phone with nothing to resume idles (`idle: no P-CSCF …`), unless a resume failed with its pair still resumable, or the reader did not answer (`PCSCF_DISCOVERY=failed`): those exit non-zero for systemd's restart |
| `PCSCF_DISCOVERED` | *(from the PDN's PCO)* | the IPv6 P-CSCF list the network put in the ims PDN's PCO, comma-separated in the order the modem reports it; the first entry `CanonicalIp6` accepts is used when `PCSCF` is unset. The packaged service gets it via `/run/imsd.env` from `ims-pdn-up.sh`, which reads it from the modem with `imsd-pcscf` (at most eight; it routes the first; imsd routes the P-CSCF it uses — the fresh target, the resumed SA peer — itself as well); entries `CanonicalIp6` does not read — an IPv4 address, a name, an IPv6 literal with a zone or a dotted-quad tail (an IPv4-mapped entry in hex only) — are ignored (logged by the engine when the daemon goes on to register); `PCSCF_DISCOVERY=found|none|failed|skipped` beside it says how the list came to be — `failed` (the reader did not answer) makes a daemon with nothing to resume exit 1 for the restart instead of idling. In `/etc/imsd.env` it is a bench seam |
| `IMSD_PCSCF` | `/usr/libexec/imsd-pcscf` | the P-CSCF reader `ims-pdn-up.sh` runs — a bench seam (the mock bench's fake reader; a stub through a unit drop-in on the dev phone), from the environment |
| `PCSCF_PORT` | `5060` | unprotected SIP port for the initial REGISTER |
| `DEV` | `qmapmux0.0` | ims-PDN netdev; the packaged service passes the real one via `/run/imsd.env` |
| `LOCAL` | *(auto)* | UE address (compared in RFC 5952 form). The packaged service gets the PDN address ModemManager assigned, via `/run/imsd.env` from `ims-pdn-up.sh`; a manual run prefers a global IPv6 on `DEV` that is neither temporary nor `dynamic` (RA-derived), then any non-temporary one, then the first (the kernel's temporary SLAAC address is listed first, and a registration bound to it is lost when the kernel retires it) |
| `USER_AGENT` | `imsd/<version>` | REGISTER User-Agent. Some networks fingerprint UAs; setting your device's stock build string reproduces the stock modem's registration exactly. Empty omits the header |
| `SIP_OUTBOUND` | `0` | Set to `1` to advertise RFC 5626 flow affinity (`+sip.instance`, `reg-id=1`, and `Supported: outbound`) for carrier cores that route terminating requests through the registered flow |
| `STATE_FILE` | `/var/lib/imsd/imsreg.state` (root) / `$XDG_STATE_HOME/imsd/imsreg.state` | persisted registration context for warm resume |
| `HOLDS_FILE` | `/run/imsd.holds` (root) / `$XDG_RUNTIME_DIR/imsd.holds` (`/tmp/imsd.holds` without a runtime dir) | the waits the network imposed (a `Retry-After`, the refusal schedule), where that schedule stands whether the old registration is gone (`old_gone`) and the refusal count (`refusals`: a resume refused without a Retry-After at a start with no fresh target is one, the restart's the second), kept across a restart of imsd for the PDN they were set on — not across a reboot; `/dev/null` switches the file off (only a regular file is ever removed; anything else is emptied). `imsd-handover.sh` honours it too |
| `RESUME` | `auto` | `0` forces a fresh registration (ignores a warm SA) where there is a fresh target; with none the untried pair is removed and the bring-up ends as AGENTS §7.2 (the daemon's own bring-up) step 1 says |
| `REFRESH_INTERVAL` | *(auto)* | keepalive re-REGISTER period in s; default = half the granted expiry, clamped to [120, 1800] |
| `EALG` | `aes-cbc` | offered ESP cipher (`aes-cbc`, `des-ede3-cbc`, `null`). From the 401's `Security-Server` list imsd selects the highest-preference `ipsec-3gpp` mechanism that matches this offer (RFC 3329 §2.3.1) and takes the SPIs/ports from the entry carrying them (some cores list every mechanism they support and put the SPIs on the one they applied); when no entry matches, the P-CSCF's SPI-carrying entry is installed as listed (`hmac-md5-96`, an absent `ealg` = null) and the journal says so. The line, the selection and the SA parameters are logged at every fresh registration |
| `SIP_TRANSPORT` | `auto` | transport of the protected leg (the second REGISTER and everything after it): `tcp`, `udp`, or `auto` = TCP, falling back to a fresh registration over UDP when the P-CSCF leaves two TCP connects unanswered — but only on a phone that has never registered over TCP: once the state file records a TCP registration, a silent connect is treated as an outage, not a policy (delete the file or set `udp` to override, e.g. after a SIM change). The challenge (first REGISTER) is always UDP. Over UDP a REGISTER or INVITE larger than ~1.2 KB leaves as IPv6 fragments at the ims PDN's 1280-byte MTU (the journal says so once per flow); there are no SIP retransmission timers over UDP yet, so a lost datagram costs a timeout. The SAs, the listener and the firewall rule cover both transports |
| `EMERGENCY_NUMBERS` | *(empty)* | comma-separated additions to the builtin 112/911 emergency numbers (e.g. a private test core's short code). SIM `EF_ECC` is not read yet. `imsd-ofonod` reads it too, for the oFono `EmergencyNumbers` property |
| `RTP_PORT` | `50004` | local RTP port advertised in SDP |
| `PRECOND` | `0` | `1` offers SDP QoS preconditions |
| `AUTO_ANSWER` | `0` | Diagnostic only: synchronously accept a valid incoming INVITE before returning to the receive loop; never enable in a normal user session |
| `DEVICE_IMEI` | unset | Validated 15-digit device identity for modems whose mainline DMS path reports it as unknown; keep in a root-only environment file and never log it |
| `CODECS` | *(empty — defaults)* | comma-separated codec preference list over `AMR-WB`, `AMR` (or `AMR-NB`), `PCMA`, `PCMU`: restricts and orders both the codecs offered on an outgoing call and those accepted from an inbound offer (default: offer AMR-WB + AMR, accept all four in that order). A bench knob — a network whose gateway transcodes every caller up to AMR-WB otherwise never lets the narrowband path run |
| `DUMP_SIP` | `0` | `1` writes raw REGISTER-200/SUBSCRIBE-200 dumps, the last refused REGISTER or SUBSCRIBE final (`imsd-register-fail.raw`, `imsd-subscribe-fail.raw`), the last inbound INVITE (`imsd-invite-in.raw`) and the last outgoing INVITE (`imsd-invite-out.raw`), mode 0600, for debugging — they contain your IMSI/MSISDN and addresses. A refused REGISTER or SUBSCRIBE is logged with its status line and any Retry-After / Warning / Reason / Server headers |
| `DUMP_DIR`, `OUT_DIR` | state dir | where dumps / per-call media stats land |
| `IMSD_MEDIA` | *(auto)* | path to `imsd-media` (default: next to `imsd`, else `/usr/libexec/imsd-media`) |
| `IMSD_ENV` | `/etc/imsd.env` | the configuration file's path — a bench seam for manual runs of `ims-pdn-up.sh` (the packaged unit reads `/etc/imsd.env` through `EnvironmentFile=`). The file's existence changes nothing |

The protected client/server ports are fixed at 45061/45062
(`imsd::util::PortUc`/`PortUs`) — changing them in source also requires
updating the firewall rules `ims-pdn-up.sh` installs.

### imsd-media (per-call, set through imsd's environment)

| Variable | Default | Meaning |
|---|---|---|
| `MIC`, `PLAY` | `1`, `1` | uplink mic capture / downlink playout via PipeWire, as imsd passes them (`imsd-media` started by hand defaults both to off) |
| `AUDIO_USER` | *(auto)* | account whose PipeWire session carries call audio; imsd sets it per call from logind's seat0 session. Set in imsd's own environment it pins an account instead |
| `GAIN`, `PLAY_GAIN` | `10`, `1.0` | uplink / downlink gain, as imsd passes them (`imsd-media` started by hand defaults `GAIN` to `1.0`) |
| `AMR_MODE` | `2` | AMR encoder mode of whichever AMR codec is active, as imsd passes it (12.65 kbit/s for AMR-WB; `imsd-media` started by hand defaults to 2 for AMR-WB and 7 for AMR) |
| `DTX` | `0` | encoder discontinuous transmission |
| `MEDIA_TIMEOUT` | `6.0` | seconds of downlink silence before exit 3 (far-end-hangup signal) |
| `RTP_DUMP` | `0` | `1` captures raw downlink RTP next to the stats file |

## D-Bus ABI

`net.catcrafts.IMS1` at `/net/catcrafts/IMS1`. The ABI is frozen.

| Member | Signature |
|---|---|
| `Dial(number)` | `s → s` (callUni) |
| `HangUp(callUni)` | `s` |
| `Accept(callUni)` | `s` |
| `SendDtmf(callUni, tones)` | `ss` |
| `GetCalls()` | `→ aa{sv}` |
| `GetStatus()` | `→ a{sv}` |
| `CallAdded` | `s a{sv}` |
| `CallStateChanged` | `s s s` (callUni, state, reason) |
| `CallDeleted` | `s` |
| `RegistrationChanged` | `b` |

Both `imsd-dialerd` and `imsd-ofonod` are users of this ABI.
A new desktop needs a new translation daemon, not changes here.

## License

GPL-3.0-only — see [LICENSE](LICENSE).

## Copyright

Copyright (C) 2026 Catcrafts®
catcrafts.net
