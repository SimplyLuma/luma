# Engine decisions

## Ported behavior

The account/message/conversation/draft model, subject normalization, reference-
based threading, optimistic read state, flagging, folders, local search and
mail transport boundaries were rebuilt in Python independently of GTK. The UI
does not import sockets or wait on network work.

## Replaced components

| Previous boundary | Native candidate | Licence/provenance |
| --- | --- | --- |
| Dart MIME and protocol code | Python 3 standard-library `email`, `imaplib`, `smtplib`, `ssl` | PSF licence; Fedora Python |
| Flutter persistence | SQLite 3 with WAL, transactions and FTS5 | Public domain; Fedora SQLite/Python |
| Flutter secure storage plugin | libsecret through GObject Introspection | LGPL-2.1-or-later; Fedora/libsecret |
| Flutter Gmail authentication | Python standard-library OAuth 2.0 authorization-code PKCE + loopback handoff; XOAUTH2 over `imaplib`/`smtplib` | Ported behavior; PSF licence runtime |
| Flutter desktop shell and window plugins | GTK 4, libadwaita and Luma AppKit | LGPL and Apache-2.0; Luma image |
| App-local HTML rendering | WebKitGTK 6 locked-down reader | LGPL/BSD; Fedora WebKitGTK |
| Private app search UI only | GNOME Shell SearchProvider2 plus local FTS | GNOME public D-Bus contract |

No third-party source is vendored. The application source is Apache-2.0. The
Mail artwork comes from Luma Icon Canvas v3 asset `05-mail.svg`. Charlie's
packaged SVG adds only the canvas's 240px platform-silhouette clip because
freedesktop hicolor consumers do not apply Icon Canvas's web presentation mask;
the PNGs are mechanical rasterizations of that clipped source.

## Dropped deliberately

- Flutter, its GTK 3 embedder and all app-drawn window chrome
- JVM/JNI bridge and JVM runtime dependency
- tray/AppIndicator behavior (Luma has no tray)
- private contact storage and app-local system-service substitutes
- any implicit access to the existing Flutter Charlie profile

The primary window's frame close is deliberately a minimize operation, keeping
the GApplication and mail engine resident; only the explicit Quit Charlie action
or session termination ends the process. Auxiliary windows close normally.

## Still behind integration gates

The transport supports password-backed TLS IMAP/SMTP and Gmail OAuth/XOAUTH2.
Google OAuth support is implemented, but enrollment is excluded from the
first Charlie release pending approval.
Special-use Sent-folder discovery now joins bounded inbound and outbound mail
using RFC ancestry plus an account-scoped subject/peer fallback for broken
providers. Using an existing GNOME Online Account, Microsoft OAuth, broader
provider folder discovery, long-lived IMAP IDLE, server search, EDS address
completion and server-side archive/trash reconciliation remain integration
gates.

Formatted messages render through a locked-down JavaScript-free WebKitGTK
boundary. Following explicit product direction, HTTPS images load by default;
the preview remains bounded and links leave Charlie through the system URI
handler. An eight-message in-memory widget cache avoids reparsing recently
viewed HTML while keeping the persistent SQLite cache authoritative.

`text/html` is not itself treated as evidence of designed mail. A conservative,
presentation-independent classifier looks for strong layout intent such as
nested/presentation tables, repeated designed controls, layout styling or
multiple images. Ordinary Gmail/Outlook and lightly formatted replies become
native conversation bubbles after recognized quote containers and plain-text
quote boundaries are removed. This deliberately does not use fuzzy signature
deletion. Current-message CID images are resolved from the private attachment
payload and shown inline; the same payload is not repeated as an attachment
tile. Uncertain layout-heavy documents continue through WebKit rather than
risk destructive simplification.
