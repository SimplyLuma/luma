# Luma Connect native continuity candidate

Private experimental source, not installed, composed, or release accepted.
This worktree's base predates the active native apps. Adapters import the
canonical Prairie source; no copied Messages/Phone implementation is present.

The receiver uses Python/OpenSSL TLS 1.3 with mutual certificate validation,
exact approved leaf fingerprints and ALPN `luma-continuity/1`. Device identities
are independent of accounts. Each endpoint must explicitly approve the other
certificate through a trusted out-of-band comparison. The bootstrap CLI does
not discover peers or silently trust a first connection.

Run synthetic tests:

```sh
LUMA_NATIVE_SOURCE=/absolute/path/to/canonical/src/prairie-core \
PYTHONPATH=src/luma-continuity python3 -W error::ResourceWarning \
  -m unittest discover -s src/luma-continuity/tests -v
```

Create each endpoint identity in a new private directory outside the repository:

```sh
PYTHONPATH=src/luma-continuity python3 -m luma_continuity identity /private/new-device
```

Exchange only `device.pem`. Compare its full SHA-256 fingerprint on the other
device, using `fingerprint` command to display it. Generate a fresh random
32-hex epoch (`python3 -c 'import secrets; print(secrets.token_hex(16))'`) and
agree it on both devices. On **each** endpoint run `approve DIRECTORY PEER.pem
--verified-fingerprint FULL_HASH --epoch EPOCH --grant messages.read` with the
capabilities that endpoint explicitly consents to receive. No grant is implicit
in account membership or relay tickets. `revoke DIRECTORY FINGERPRINT` denies
all subsequent operations, including cached result retrieval. CLI approval is
the temporary local consent path; a native Settings panel remains required.

Private keys expire after 30 days for this experiment. Rotation requires new
approval. They must never be copied to the relay or committed. Deleting state
is not normal recovery: it loses replay tombstones. Preserve the journal and
identity across restart; loss of either requires fresh identity and pairing.

The cloud task owns WSS `/v1/relay/{session_id}` with ticket header
`X-Luma-Relay-Ticket`. A relay ticket is not device authorization. `relay.py`
uses OpenSSL MemoryBIO for inner TLS over bounded binary frames. Relay-only
routing exposes IP addresses to the relay, but not to the other peer at the
application layer. Outer WSS uses public CA/hostname validation; inner TLS uses
only approved device trust. No relay is deployed by this package.

Current implementation: durable capability and request journal, CLI device
bootstrap, direct TLS framing, opaque-relay TLS adapter, existing native SMS
sender and message/picture read adapter. Real SMS modem and MMS transport,
notification-owner integration, client UI, contacts, call/audio and mirroring
are not claimed complete. All tests use synthetic temporary state; no personal
messages/contacts, phone connection or modem access is performed.

See the [integration contract and test boundaries](integration/README.md).

## Account app and daemon (2026-09-07)

`luma-connect` is one responsive App Kit application. Desktop defaults to
880×620 with a 560×440 minimum; handheld presentation supports 360 pixels and
uses a centered identity followed by grouped touch controls. The same daemon
snapshot supplies both. `luma-connect-daemon` is activated on the user session
bus as `org.projectluma.Connect1`. Without root-owned immutable
`/etc/luma-connect/client.json`, it remains signed out. Configuration keys are
`issuer`, `api_origin`, and `client_id`, all using the reviewed native client;
both endpoints require HTTPS. No production endpoint is bundled.

Account authorization uses external-browser Authlib S256 PKCE, validated
joserfc ID tokens plus API identity agreement, and libsecret only. Token
renewal binds to the original identity/session. There is no token-file fallback.
The account broker has no sync adapters enabled. Usage stays unknown, clipboard
is hidden, and sessions are server facts rather than an invented device list.
Server revocation precedes local credential removal and never deletes files.
A polling watchdog pauses account work if an observation takes too long; its
physical end-to-end timing is still a release gate.

Host protocol tests: 41 passing with Authlib 1.7.0, httpx 0.28.1, joserfc 1.7.5
and websockets 17.1 in a disposable venv. Authlib emits its upstream JOSE
migration deprecation warning. Tests without Authlib skip the login cases and
must not be reported as equivalent coverage. Private ARM emulator runtime
proved on-demand D-Bus activation, signed-out UI, adaptive layouts and stale
snapshot preservation after daemon loss. Signed-in native UI fixtures are
explicitly synthetic: they do not establish working account login or sync.

Remaining gates include actual trusted HTTPS browser/libsecret sign-in,
package builds and dependency availability on both architectures, reviewed
D-Bus permissions and shutdown/resource behavior, clean boot/upgrade/removal,
accessibility and physical-device acceptance. Account-storage encryption,
password recovery, real sync adapters, continuity consent UI, notification
composition, call audio and mirroring remain incomplete. Do not enable this
candidate in a composed release on the basis of these tests.


## Fedora 44 package qualification

The initial exact PyPI pins were not installable from Fedora 44 repositories.
The current constraints retain distro HTTPX0.28.1 and WebSockets15.0.1, with
private noarch Authlib1.7.2 and joserfc1.7.5 RPMs built from unmodified upstream
source. Authlib1.5.2 in Fedora predates relevant upstream security fixes;
Authlib1.8 introduces HTTPX2, so this candidate uses the maintained 1.7 line.
Provenance is recorded in `dependencies/provenance.json`. Supplemental specs are
owned in `packaging/rpm/python-authlib.spec` and `python-joserfc.spec`.

Normal dependency RPM checks passed 203 JWK/JWS/JWT tests and 22 synchronous
OAuth2 HTTPX tests. The full Connect suite now passes 43 tests on x86_64 and
ARM using these RPMs, including real local WSS TLS, header carriage and wrong
hostname rejection. ARM testing uses privately extracted packages; no emulator
package installation occurred. The actual libsecret API also passed a synthetic
store/read/clear on an isolated Secret Service and session bus.

The application spec requires all account/login/native/notification tests and
fails on missing dependencies or skipped tests. Normal source and noarch binary
RPM builds passed after the shared native App Kit51 build was repaired with
real accessibility/portal test services. Installed-package D-Bus activation and
file integrity passed in the disposable x86 builder. No emulator application
package installation, release composition or live account login is claimed.

### Explicit local paired worker

`PairedExchange(directory, approved_fingerprint, pairing_epoch, numeric_address,
port)` is a callable for `QueuedMessages.flush`. It loads the previously approved
device certificate and private identity, uses a fresh mutually authenticated TLS
1.3 connection per request, verifies the exact peer pin and protocol, and bounds
handshake/send/receive work. It never discovers peers, approves grants, starts a
listener, or selects itself as the user's Messages provider. The owning phone
service supplies `Receiver` adapters and an explicitly bound test listener.

Local send admission serializes with revocation: an already admitted bounded send
may finish before revoke returns. Results revoked while in flight are rejected;
the owning UI must also gate later rendering/consumption. The Messages provider
checks approval again before importing a receipt. Failed exchanges preserve the
same queued operation ID rather than inventing retries.

The integration test uses real loopback sockets, TLS, journals, canonical native
MessageStore/ReplySender and the shared queued provider. Only the modem and records
are synthetic; it does not demonstrate real SMS delivery, physical pairing, or
account sync. Phone-owner consent/selection, actual service composition, incoming
updates, call audio and mirroring remain unfinished.

Native browser login uses exact `/callback` with an OS-selected IPv4 loopback
port. The Keycloak public desktop client registers `http://127.0.0.1/callback`
without a port; state, nonce and S256 PKCE remain required. The same current native
handheld flow uses this public desktop client. Custom-scheme mobile callback
support is not implemented. Public HTTPS issuer/API deployment remains required.

`updates.read_status()` is an account-independent, read-only view of the existing
rpm-ostree updater. It distinguishes staged/default-pending from booted and
retains the observation time when unavailable/stale. Layered base matches are
reported separately from exact deployment matches. `correlate_release` requires
signature verification supplied by the canonical updater owner; cloud metadata
or account membership cannot establish it. This module does not stage, enroll,
reboot, upload status, or infer successful rollback/health. Native UI and consented
Depot-reporting integration remain separate work.

## Companion devices (Android, ADR-021)

`companion.py` pairs an Android phone running Luma Connect with a QR invitation and
receives its requests; `companion_desktop.py` hands each effect to its desktop owner
and joins the pieces for the daemon. No account is needed: the Connect link switch
and a pairing are the only gates.

- **Effects:** mirrored notifications, links and received files go to
  `org.freedesktop.Notifications` (actions become `notifications.act`; a reply action
  asks the app for a reply prompt; links and files open only after a click). Copied
  text goes through the Shell's `org.projectluma.Connect.Clipboard1`
  (`patches/gnome-shell/0094`). Phone media is one MPRIS player per phone. The phone
  as touchpad/keyboard (`input.control`, off by default) uses a lazily created
  `org.gnome.Mutter.RemoteDesktop` session that closes after 60 idle seconds and
  drops input while the screen is locked. Ringing uses GSound for up to 30 seconds.
  The listener (TCP 47810) runs only while Connect is on and a phone is paired or a
  pairing is waiting, on explicit private addresses from NetworkManager, and is
  advertised as `_luma-connect._tcp` (`v=1`, random instance name) through Avahi.
- **Daemon (`org.projectluma.Connect1`):** `StartCompanionPairing(as, as) -> s`,
  `CancelCompanionPairing()`, `CompanionDevices() -> s`, `RemoveCompanion(s)`,
  `SetCompanionGrants(s, as)`, `CompanionInvoke(s, s, s) -> s` (only `device.ring`,
  `clipboard.write`, `links.open`, `notifications.act`, `media.control`; payload at
  most 64 KiB), `CompanionSendFile(s, s) -> s` (512 KiB ordered `files.write` chunks,
  SHA-256 on the last), `CancelCompanionTransfer(s)`, `CancelCompanionReply()`;
  signals `CompanionChanged` and `CompanionReplyRequested(s)`. `GetState` adds
  `companion_devices`, `companion_pairing` (state, six-digit code, phone name; never
  the invitation secret), `companion_transfers`, `companion_reply` and
  `companion_listening`.
- **App:** a Phones panel (also reachable from the sign-in page) lists each phone's
  battery, last contact and permissions with Ring, Send clipboard, Send file… and
  Remove, and a pairing dialog: choose permissions, scan the QR code (python3-qrcode,
  with the link as a fallback), then compare the six-digit code.

Tested on macOS with synthetic state (`tests/test_companion.py`,
`tests/test_companion_desktop.py`): notification id and action mapping, MPRIS
property and control mapping, `input.control` validation and the Mutter call
sequence, the Avahi collision path, address selection, the `CompanionInvoke`
allowlist, and a real loopback pairing, device call and three-chunk file transfer
through `CompanionService` to a fake phone endpoint. Every D-Bus owner is a recorder
there. `daemon.py` and `application.py` were only byte-compiled: no GTK, D-Bus,
GNOME Shell, Mutter, Avahi, NetworkManager, GSound or Android device was used.

Gates: runtime of every owner on a Luma session (Notify actions and the reply
prompt, the Shell clipboard patch, MPRIS in the Shell media controls, Mutter remote
desktop permission and its screen-control indicator, Avahi, GSound); the app on 360,
500, 1024 and wide widths with keyboard and screen reader; a physical phone; the
desktop identity's 30-day expiry and rotation without re-pairing; D-Bus caller policy
for `CompanionInvoke` and `CompanionSendFile` (any same-user session client can call
them; a portal file descriptor should replace the path argument); package
dependencies (python3-qrcode, GSound typelib); and an independent security review.
