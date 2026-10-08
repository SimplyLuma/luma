# luma-messages-e2ee

End-to-end encryption for Luma Messages (ADR-051): the `luma-mls` crate and the
desktop helper `luma`. MPL-2.0.

## The crate (`src/`, library `luma_mls`)

MLS (RFC 9420) through OpenMLS 0.6.0, ciphersuite 0x0003. Every device is a
leaf and every conversation is a group, 1:1 included.

- `identity` — `luma1:<account>:<device>`, the BasicCredential identity.
- `engine` — a device's signing key, key packages (one-time and last-resort),
  groups: create, add, remove, update, confirm or discard a pending commit,
  join from a Welcome, encrypt, decrypt with a sender check (revoked devices).
  `snapshot()`/`from_snapshot()` are the whole state as bytes.
- `attachment` — AES-256-GCM, a fresh key per file.
- `safety` — 60-digit safety numbers and their QR text.
- `sealed` — XChaCha20-Poly1305 sealing of a snapshot with a platform key.
- `payload` — what goes inside an application message (never the sender).

The library has no files, sockets or threads, and its surface is owned bytes,
strings, integers and plain records with one error type, so it can be exported
with UniFFI for Android or wasm-bindgen for the web without an adapter. Neither
is built now. `cargo check --lib --no-default-features` is that surface; the
RPM `%check` runs it. A wasm32 build needs `getrandom`'s `js` feature.

## The helper (`src/bin/luma/`)

`/usr/libexec/luma-messages/luma --data-dir <account directory>` speaks
luma-messages-bridge/1 (docs/research/messages-bridge-protocol.md) plus the
`luma.*` commands below, and talks to the delivery service
(docs/research/luma-messages-delivery-api.md) and the Hub's identity routes with
this device's Luma Connect token (`~/.local/share/luma/connect/device.json`).

- State: one file, `state.sealed`, sealed with a 32-byte key Messages keeps in
  the Secret Service and hands over with `session.load`. Written to a temporary
  file, fsynced and renamed after every change; a queued message is
  acknowledged only after the state that applied it is on disk. No key, no
  state: a locked keyring stops the account.
- Commits wait for the server: a losing race (409 epoch) is discarded, the
  winner applied from the queue, and the commit made again. A crash between
  sending a commit and confirming it is settled at the next start by the
  group's epoch.
- Upkeep every five minutes: key packages topped up, device lists read,
  revoked devices removed from every group, new devices of member accounts
  added, and each group's keys updated daily or after 200 sent messages.
- Deliveries need the person's token from Messages, used once; the shared
  kill switch and more than 12 deliveries a minute stop them.

`luma.*` commands: `identity`, `handle.check`, `handle.claim`, `people`,
`requests`, `request.answer`, `block`, `blocks`, `report`, `safety`, `verify`,
`devices`, `sync`.

## Tests

    cargo test --no-default-features --lib --tests      # core
    cargo test                                          # plus the helper's own
    python3 tests/e2e.py --helper target/release/luma   # end to end
    python3 tests/e2e.py --helper target/release/luma --self-test

`tests/e2e.py` runs four real helpers (two accounts, one with two devices, and
a third) through `tests/fake_hub.py`, a local Hub that keeps to the delivery
contract where it can without MLS (clear headers, epochs, fan-out, requests,
blocks). `--self-test` breaks each mechanism in turn and fails unless the
check for it fails.

## Building

`scripts/packages/build-luma-messages-e2ee-source.sh OUT.tar.gz` makes the
source archive with every crate vendored against `Cargo.lock`, so
`packaging/rpm/luma-messages-e2ee.spec` builds offline.
