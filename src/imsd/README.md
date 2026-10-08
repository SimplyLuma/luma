<!-- SPDX-License-Identifier: GPL-3.0-only -->
<!-- SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts® -->

# imsd

Userspace IMS/VoLTE daemon for mainline Linux phones.

Modem firmware on typical mainline-Linux phones will bring up the IMS PDN but
can't be used for actual call audio. imsd implements the whole IMS client in
userspace instead: SIP registration with USIM AKA authentication over kernel
IPsec (ESP), call signalling, and the AMR-WB RTP media plane wired into
PipeWire so a stock Plasma Mobile dialer can place and receive real VoLTE
calls on a commercial network.

For deterministic media-loop tests, `MIC_FILE` replaces microphone capture
and `MIC_FILE_GAIN` controls that file independently (default `0.5`). `GAIN`
continues to apply only to live microphone capture, preventing mastered test
audio from inheriting a hardware-microphone boost and clipping before encode.

## Status

Call-capable, both directions. Outgoing VoLTE calls place, answer, carry
full-duplex AMR-WB audio, and tear down; incoming calls ring, answer via
`Accept`, and carry media the same way — driven end-to-end from a stock
dialer over the frozen D-Bus ABI. Current state:

The engine core (`imsd-core`) is a GLib-free, pure-`std` static library whose
every decision — SIP framing, SDP selection, the AKAv1-MD5 digest, the
`ip xfrm` command sequence, and the whole call FSM — is unit-tested on a dev
box with no bus, modem, or phone; several suites pin output byte-for-byte
against recorded network traffic. GLib/GDBus, sockets, and subprocess control
live only in the `imsd` daemon shell.

## Architecture

C++26 modules, built with Crafter Build. Four build products:

- **imsd-core** (static library) — the engine, pure `import std` C++ with no
  GLib and no I/O side effects. Everything here is unit-testable on any dev
  box without a modem, a bus, or a phone; several suites pin output
  byte-for-byte against recorded network traffic.
- **imsd** (executable) — the daemon shell: GLib main loop, GDBus service,
  sockets, subprocess control (`qmicli`/`mmcli`/`ip`), and the registration
  sequence + run loop that pumps the core. All GLib and I/O live here.
- **imsd-media** (executable) — the RTP/AMR-WB data plane, spawned once per
  call. Pure `import std` + POSIX, no GLib; links `-ldl` and dlopen's the
  codecs. A separate process for crash isolation and the far-end-hangup
  exit-code contract.
- **imsd-dialerd** (executable) — the Plasma Mobile integration: a session
  daemon owning `org.kde.telephony.{CallUtils,DeviceUtils,UssdUtils}`,
  bridging them to imsd on the system bus. GDBus translation only, no core.
  It is a separate process because those are per-user *session*-bus names
  that the root system daemon cannot own; built with `-- --product=dialerd`,
  autostarted from an `.desktop` file in place of plasma-dialer's
  `modem-daemon` (whose autostart must be disabled).

### Terminating-network dependency (incoming)

A UE only receives a VoLTE call as a SIP INVITE if the network's Terminating
Access Domain Selection (T-ADS) routes it to the PS/IMS domain rather than
paging the modem over CS. That requires the IMS registration to advertise
voice capability — the MMTEL ICSI (`+g.3gpp.icsi-ref=...mmtel`) and
the RFC 3840 `audio` and RFC 7254 `+sip.instance` media-feature tags in the
REGISTER Contact, which `Imsd:Messages` emits. T-ADS may still select CS for
reasons outside the SIP client (operator policy, radio-capability signalling,
or a competing registration), while an accepted registration can also remain
unreachable because of a client-side Contact, security-association, listener,
or firewall defect. Therefore incoming readiness is proven only by receiving
an INVITE on the registered protected flow, never by REGISTER success alone.

## Supported hardware & carriers

imsd talks to the modem through ModemManager plus `qmicli` over QRTR (USIM
AKA via a UIM logical channel), so in practice it currently needs a Qualcomm
modem on a mainline kernel with working WWAN/MM integration, kernel ESP
(`ip xfrm`), and PipeWire for audio.

Everything below has been verified by community members to be fully workking:

| Device | OS | Carrier
|---|---|---|
| The Fairphone (Gen. 6) | postmarketOS, Linux 7.1.2 | KPN NL |
| The Fairphone (Gen. 6) | postmarketOS, Linux 7.1.2 | Telekom Deutschland GER |
| The Fairphone (Gen. 6) | postmarketOS, Linux 7.1.2 | Phonero |
| The Fairphone (Gen. 6) | postmarketOS, Linux 7.1.2 | Telia Norge |

## Build

[Crafter Build](https://forgejo.catcrafts.net/Catcrafts/Crafter.Build) +
gio-2.0 headers (`glib2` on Arch, `glib-dev` on Alpine).

```sh
crafter-build                       # bin/imsd-<target>-<march>/imsd (the daemon)
crafter-build -- --product=media    # bin/imsd-media-.../imsd-media (the media leg)
crafter-build -- --product=dialerd  # bin/imsd-dialerd-.../imsd-dialerd (dialer backend)
crafter-build test                  # unit tests (Util, Aka, Ipsec, Messages, Engine, Sip, Sdp)
```

### Building with make (distro packaging)

For environments without crafter-build, the Makefile mirrors the same clang
module pipeline: GNU make, clang++ with libc++ and the libc++ std module
sources (`std.cppm` — package `llvm-runtimes` on Alpine, `libc++` on Arch),
lld, and gio-2.0 headers.

```sh
make            # build/make/{imsd,imsd-media,imsd-dialerd}
make check      # the same 7 unit-test suites
make install    # DESTDIR/PREFIX staged install incl. the packaging/ files
```

`project.cpp` stays the canonical build description; the Makefile tracks it.

### Cross-compiling for a phone (aarch64 Alpine/postmarketOS)

```sh
packaging/make-sysroot.sh                  # one-time: Alpine aarch64 sysroot from the CDN
crafter-build -- --target=aarch64-alpine-linux-musl \
    --sysroot=$HOME/.cache/imsd/sysroot-aarch64-alpine \
    --march=armv8-a --mtune=generic
# same flags after `crafter-build test --target=aarch64-alpine-linux-musl`
# run the suite under qemu-aarch64 against the sysroot
```

## Run (development)

```sh
./bin/imsd-*/imsd --session   # own net.catcrafts.IMS1 on the session bus
busctl --user call net.catcrafts.IMS1 /net/catcrafts/IMS1 net.catcrafts.IMS1 GetStatus
```

On a phone the daemon runs as root on the system bus (policy in
`packaging/net.catcrafts.IMS1.conf` — it grants the postmarketOS default
account `user` access; adjust it alongside `AUDIO_USER` if your session
account is named differently), started by `packaging/imsd.service`
after `packaging/ims-pdn-up.sh` has brought up the ims PDN through
ModemManager. It spawns `/usr/libexec/imsd-media` per call. Registration
resumes an in-kernel IPsec SA when one is present (avoiding the network's
fresh-SA throttle) and keeps itself alive with a re-REGISTER refresh at half
the granted lifetime. `packaging/APKBUILD` builds the apk from source via
the Makefile; `packaging/APKBUILD.binary` + `make-bin-tarball.sh` wrap a
cross-compiled build into an apk instead.

## Configuration

Everything is environment variables. The packaged unit reads
`/run/imsd.env` (written by `ims-pdn-up.sh`: the connected ims netdev) and
then `/etc/imsd.env` (your configuration, wins on conflict). Minimum viable
`/etc/imsd.env`:

```sh
# REQUIRED until P-CSCF discovery from the PDN's PCO is implemented: your
# carrier's P-CSCF address. Find it in a stock-firmware capture or your
# carrier's IMS documentation.
PCSCF=2001:db8::105
```

### imsd

| Variable | Default | Meaning |
|---|---|---|
| `PCSCF` | *(none — required)* | P-CSCF address; without it registration fails with a clear error |
| `PCSCF_PORT` | `5060` | unprotected SIP port for the initial REGISTER |
| `DEV` | `qmapmux0.0` | ims-PDN netdev; the packaged service passes the real one via `/run/imsd.env` |
| `LOCAL` | *(auto)* | UE address; default = the global IPv6 on `DEV` |
| `USER_AGENT` | `imsd/<version>` | REGISTER User-Agent. Some networks fingerprint UAs; setting your device's stock build string reproduces the stock modem's registration exactly. Empty omits the header |
| `SIP_OUTBOUND` | `0` | Set to `1` to advertise RFC 5626 flow affinity (`+sip.instance`, `reg-id=1`, and `Supported: outbound`) for carrier cores that route terminating requests through the registered flow |
| `STATE_FILE` | `/var/lib/imsd/imsreg.state` (root) / `$XDG_STATE_HOME/imsd/imsreg.state` | persisted registration context for warm resume |
| `RESUME` | `auto` | `0` forces a fresh registration (ignores a warm SA) |
| `REFRESH_INTERVAL` | *(auto)* | keepalive re-REGISTER period in s; default = half the granted expiry, clamped to [120, 1800] |
| `EALG` | `aes-cbc` | offered ESP cipher (`aes-cbc`, `des-ede3-cbc`, `null`) |
| `EMERGENCY_NUMBERS` | *(empty)* | comma-separated additions to the builtin 112/911 emergency numbers (e.g. a private test core's short code). SIM `EF_ECC` is not read yet |
| `RTP_PORT` | `50004` | local RTP port advertised in SDP |
| `PRECOND` | `0` | `1` offers SDP QoS preconditions |
| `AUTO_ANSWER` | `0` | Diagnostic only: synchronously accept a valid incoming INVITE before returning to the receive loop; never enable in a normal user session |
| `DEVICE_IMEI` | unset | Validated 15-digit device identity for modems whose mainline DMS path reports it as unknown; keep in a root-only environment file and never log it |
| `DUMP_SIP` | `0` | `1` writes raw REGISTER-200/SUBSCRIBE-200/INVITE dumps (mode 0600) for debugging — they contain your IMSI/MSISDN and addresses |
| `DUMP_DIR`, `OUT_DIR` | state dir | where dumps / per-call media stats land |
| `IMSD_MEDIA` | *(auto)* | path to `imsd-media` (default: next to `imsd`, else `/usr/libexec/imsd-media`) |

The protected client/server ports are fixed at 45061/45062
(`imsd::util::kPortUc`/`kPortUs`) — changing them in source also requires
updating the firewall rules `ims-pdn-up.sh` installs.

### imsd-media (per-call, set through imsd's environment)

| Variable | Default | Meaning |
|---|---|---|
| `MIC`, `PLAY` | `1`, `1` | uplink mic capture / downlink playout via PipeWire |
| `AUDIO_USER` | `user` | desktop user whose PipeWire session carries call audio (postmarketOS default account) |
| `GAIN`, `PLAY_GAIN` | `10`, `1.0` | uplink / downlink gain |
| `AMR_MODE` | `2` | AMR-WB encoder mode (0–8) |
| `DTX` | `0` | encoder discontinuous transmission |
| `MEDIA_TIMEOUT` | `6.0` | seconds of downlink silence before exit 3 (far-end-hangup signal) |
| `RTP_DUMP` | `0` | `1` captures raw downlink RTP next to the stats file |

## D-Bus ABI

`net.catcrafts.IMS1` at `/net/catcrafts/IMS1`. The ABI is frozen — dialer
integrations depend on it:

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

## License

GPL-3.0-only — see [LICENSE](LICENSE).

## Copyright

Copyright (C) 2026 Catcrafts®
catcrafts.net
