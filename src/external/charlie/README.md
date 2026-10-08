# Charlie for Luma

Charlie is the native adaptive mail candidate for Project Luma. The interface
uses GTK 4, libadwaita and `luma_appkit`; the presentation-independent engine
uses Python's MIME, IMAP, SMTP and SQLite libraries. WebKitGTK 6 is restricted
to sandboxed HTML message bodies.

First run is empty and offers account enrollment. Common providers have manual
app-password presets, and custom TLS IMAP/SMTP remains available. The Gmail
PKCE/XOAUTH2 implementation is present in the source, but Google OAuth is excluded
from the first Charlie release pending approval. Tokens and passwords live only
in Secret Service. Set `CHARLIE_DEMO=1` for the reserved `.test` fixture.
The native app does not discover, migrate or modify the existing Flutter
Charlie's data.

## Development

```sh
PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_*.py' -v
PYTHONPATH=. python3 -m charlie_luma
```

On Luma, run against the host toolkit rather than inside a container so the
real frame, islands and semantic palette are exercised.

## Package

```sh
scripts/build-rpm.sh
```

The script creates an isolated RPM work tree under `.build/rpmbuild` unless
`CHARLIE_RPM_WORK_ROOT` names another location.

## Safety boundaries

- App ID: `org.projectluma.Charlie`
- Local data: `$XDG_DATA_HOME/charlie` (normally `~/.local/share/charlie`)
- Credentials: Secret Service only; never SQLite, arguments or logs
- No tray, JVM, Flutter, Electron, telemetry or private contact store
- HTML: JavaScript off and navigation blocked; the preview and expanded reader
  enable remote images, which can make requests to senders' image servers
- Search: local, bounded and query text is neither logged nor persisted

See the [integration guide](docs/HANDOFF.md) for component boundaries, build
entry points, and current limitations.
