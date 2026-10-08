# Charlie integration guide

Charlie combines a native GTK 4/libadwaita/Luma AppKit interface with a Python
mail engine. This guide describes the component source and integration boundaries;
it is not a certification that every account provider or hardware profile has
been qualified.

## Source and services

- `charlie_luma/` owns the application, mail engine, local message store, account
  configuration, and HTML reader.
- `luma-app.toml` declares the application runtime integration.
- [Engine decisions](ENGINE_DECISIONS.md) records upstream and implementation
  choices; [independent application integration](INDEPENDENT-APP.md) describes
  the retained mail-host service boundary.
- The shared UI comes from the Luma Application Kit; the app adapts between
  mailbox/thread/reader panes and compact navigation.

## Accounts and privacy

Manual TLS IMAP/SMTP account configuration is available. The source also contains
Google PKCE/XOAUTH2 support, but Google OAuth is excluded from the first Charlie
release pending approval. Do not present an unapproved OAuth flow as publicly
available account support.

Credentials and OAuth material belong in Secret Service, not the message database
or command arguments. Local mail data uses the application's XDG data directory.
Tests and demo mode use isolated synthetic accounts.

HTML mail uses WebKitGTK with JavaScript disabled and navigation intercepted.
The current preview and expanded reader enable remote images; these can contact
senders' image servers. The renderer's content security policy restricts the
permitted resource classes. This behavior should not be described as remote images
being disabled by default.

## Development and packaging

Run the component commands from its directory; [the component README](../README.md)
contains the unit-test, launch, and RPM entry points. The repository-wide
[build guide](../../../../BUILDING.md) explains platform and release inputs.

A source build does not publish a signed app update or certify provider login.
Account-connected syncing, reconnect/background behavior, notifications, HTML
rendering, accessibility, and device-specific layouts need the corresponding
runtime checks. Report synthetic-fixture checks separately from live-provider
results and retain failures or skipped coverage.

## Compatibility limits

Existing-GOA-account adoption and EDS address completion are not established by
this component's source integration. Provider authorization, server configuration,
and network availability can independently prevent enrollment or syncing.
Preserve local mail and credentials across upgrades; do not substitute an old
private development artifact or qualification journal for the current source and
package identity.
