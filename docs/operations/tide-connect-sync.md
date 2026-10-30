# Tide server sync

Tide owns its SQLite library and libsecret credential schema. Connect exchanges
network source metadata through `collections/tide-sources`; passwords use the
separate `sync/secrets/tide-source:<source-id>` route. Local music folders and
media files are not copied by this service.

Install the matching native Tide and core-apps candidates together: Tide
`2.luma.36.mobile20261030.1` and core apps `1.luma.124.mobile20261030.2`.
These are source candidate pins, not published or qualified packages. The core
RPM requires the matching Tide API when native Tide is installed. Connect also targets the active signed Flatpak
install. Model and credential
helpers execute inside that app's existing sandbox; no extra filesystem or bus
permissions are requested. Its data stays separate from retained native libraries.
Older Tide builds without the background-read API use their own credential schema
with metadata-only libsecret search. Old Flatpak builds without the source marker
retain timer/Hub-event refresh until Tide itself is updated.

Connect invokes the selected Tide implementation for model writes and credential
access. Background reads search unlocked items under Tide's current and preview
schemas without requesting an unlock prompt. Credentials stay out of metadata,
arguments, logs and public test evidence. Existing opaque sign-in references
survive metadata changes. A missing credential is reported as needing sign-in,
and retried even if no new Hub revision arrived.

A native receiving device can create its library through Tide's model before
opening the app. Its canonical library takes priority over retained previews;
explicit development overrides remain authoritative. Tide touches the empty
`sources-changed` marker after source additions, account edits and removals.
Connect's path unit watches that marker; playback, indexing and network-state
writes do not trigger it. Existing timer and Hub-event recovery remain enabled.

Cross-component regression checks use the real Tide model and credential class
with isolated libsecret and Hub fixtures:

```sh
PYTHONPATH=src/prairie-core:src/luma-tide:src/luma-installer \
  python3 tests/integration/tide_connect.py
```

Tide's package unit suite also tests non-interactive credential reads and source
notifications. Core-app package checks retain the existing collection, watch,
install-selection and Tide adapter tests. Full package/OS qualification and
physical second-device convergence are separate from these source checks.

For a migrated account, a password record under a different source ID is not
sufficient evidence to attach it to the current server. If no matching sign-in
exists on any enrolled device, sign into that server once in Tide. Do not guess
an association or delete unrelated Hub credential records.

On phones, opening Edit from the Sources More menu presents the edit drawer
again after the menu dismisses, keeping the password entry reachable. The Tide
package checks the real menu path at phone and desktop sizes; Titan validation
ran the phone profile on the device.
