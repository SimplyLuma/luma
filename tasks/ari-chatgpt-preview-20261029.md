# Ari2 ChatGPT manual preview

Owner: Codex, luma-dev-2. Branch: `agent/1003/ari-chatgpt-20261029`.
Source revision: `7a2964d12f195b00985cfa4e0b305924edbcfaba`.

The owner requested a separate Ari2 installation and launch on the Dell XPS.
Delivered the native user-local HOTFIX candidate `ari2-chatgpt-7a2964d-r2`.
Archive SHA-256: `d73dcbecf5be75b43974c98e3ac297581f46137b2278a56388f20948fd739c11`.
The candidate includes its source/transformation manifest, payload checksums,
licenses and reproducible exporter. The archive and payload checksums were
verified before installation. Normal Ari's package and service were retained.

Application identity: `org.projectluma.Ari2.Hotfix`.
Daemon identity: `org.projectluma.Ari2.Hotfix1`.
Conversations, settings and keyring schemas use separate preview locations.
The production enabled switch remains a read-only master cutoff; the preview
defaults to asking before every change.

Server checks passed for the exact candidate: real dependency preflight,
disposable installation and replacement refusal, desktop metadata, actual
launcher/D-Bus activation, private GTK/Secret Service/OAuth fixture flow,
model selection, daemon restart, sign-out and cancellation. Production-name
bus/file/keyring sentinels remained unchanged. Provider responses in these
checks were simulated; no live OpenAI success is claimed.

The first installer incorrectly requested LumaAppearance typelib version
`1.0`; the installed namespace is `1`. Revision r2 corrects that check and
adds an actual server dependency preflight. The Dell has platform generation
118 and Python 3.14; the corrected dependency check passed there.

The owner reported `ServiceUnknown` after the initial launch. The existing
desktop bus had not discovered the newly created user activation directory.
`org.freedesktop.DBus.ReloadConfig` made Ari2 activatable; starting the service
then succeeded. No desktop session or production service was restarted.
Reopened through the graphical user manager in unit
`luma-preview-ari2-chatgpt-20261029-e78e0115.service` on 2026-10-29.
The unit remained active, and both Ari2 application and daemon names were
owned. This confirms launch and registration, not manual acceptance or live
ChatGPT authorization/inference.

Remaining: owner browser consent, actual model selection/reply, reopen and
sign-out checks. PR 92 remains draft. All automated application tests stayed
on the server. Private operational evidence remains outside the repository.
