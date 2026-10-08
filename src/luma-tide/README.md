# Tide

Tide is Project Luma's native source-aware music library and player. It is one
GTK/libadwaita application and one `luma-tide` package across windowed desktop
and fullscreen-mobile presentation modes.

## Ownership and architecture

Tide is an independent Luma-owned application rather than a
`prairie-core-apps` member. The Prairie package contract enumerates the native
communications, personal-information, and hardware core; Tide is an additive
media application built on the public Luma Developer Platform.

- `model.py` owns the atomic SQLite schema, sources, logical tracks, physical
  copies, playlists, queue, corrections, and playback session state.
- `metadata.py` reads common tags and embedded artwork off the GTK thread.
- `indexer.py` scans local GIO-compatible paths with bounded concurrency. GIO
  directory monitors schedule event-driven rescans; there is no polling loop.
- `subsonic.py` is a standard-library Subsonic/OpenSubsonic REST client: any
  server that answers the protocol works, not only Navidrome.
- `credentials.py` is the only code that talks to the system secret service
  (libsecret). It has no Subsonic-specific knowledge.
- `remote.py` owns the Subsonic source lifecycle on top of those two: account
  verification, syncing songs into the shared library, and resolving a
  playable URL at the moment of playback.
- `playback.py` is the only playback controller. Its GStreamer playbin uses a
  platform audio sink through GStreamer and supplies every UI and system control.
  A resolver turns a stored copy into the URI it opens, so a remote copy
  authenticates only at the moment of playback.
- `subsonic.py` is a standard-library client for the Subsonic/OpenSubsonic API.
- `remote.py` owns Navidrome and other Subsonic-compatible sources: verifying and
  saving an account, sync, artwork, stream resolution, offline downloads, and
  removal.
- `credentials.py` keeps source passwords in the system secret service.
- `mpris.py` adapts that controller to MPRIS. It does not own another player.
- `credit_markup.py` turns a track's real `artists`/`album_artists` tuples
  into Pango markup: each artist that resolves to a known library
  `album_artist` grouping is a clickable link, an unresolvable one is plain
  text, and a given album name is always a link. It has no GTK dependency.
- `application.py` owns startup, stores and system integration. `ui.py` and
  `ui_parts.py` compose the Studio v70 LumaUI desktop and phone surfaces.
  `live.py` adapts the existing library and playback controller; library reads
  run in the window worker. `presentation.py` owns GTK-free view values.
  Album navigation uses the shared navigation trail. Artist and album links
  in the attached player use the same library views.
- `LUMAUI-INVENTORY.md` maps the port's surfaces, actions and thirty conform
  states to their implementation and outstanding shared kit requests.

## Library identity and recovery

A track is merged only when Tide has a strong media identity:

1. a MusicBrainz recording identifier supplied by the file; or
2. an exact SHA-256 content digest.

Title, artist, album, duration, and filename are never sufficient to merge two
tracks. This deliberately leaves transcoded copies separate unless they share
a trusted recording identifier; a later fingerprint migration may offer a
reviewable merge without weakening the rule.

Each URI is a physical copy attached to one source. Missing paths and offline
sources change availability but do not immediately delete track history. A
completed scan marks absent copies unavailable; a cancelled or failed scan
does not. Removing a source only removes Tide's database records and never
unlinks original media.

Schema migrations run inside an explicit SQLite `BEGIN IMMEDIATE` transaction.
The database uses WAL plus full synchronous durability. A newer unknown schema
is rejected rather than opened destructively.

## Sources and secrets

Local folders/removable storage and Subsonic-compatible servers (Navidrome and
others answering the same REST API) are both production adapters. URI
userinfo is rejected by `model.py.add_source()`, and `auth_ref` may contain
only an opaque system-secret-service reference: `remote.py` never passes a
raw password to `add_source()`, and `credentials.py` is the sole path into the
keyring. A Subsonic source's own `copies.uri` is deliberately credential-free
(the server's base URL and the song's ID); an authenticated stream URL is
built fresh, in memory, only for one playback or one sync request, and is
never logged, stored, or included in an exception message. Credentials must
never be stored in the library, artwork URI, semantic object, MPRIS metadata,
or logs.

A Subsonic source Tide cannot currently reach is never dropped: `SourceState`
(`offline`, `auth-required`, `error`) records why, its previously synced songs
stay in the library exactly as they were, and the Sources view offers retry
and removal, never automatic deletion.

The v70 Sources pane shows real sources and their status. **Add source** opens
the Navidrome/Subsonic form in that pane; editing uses the same form. Local
folders remain supported by the existing source adapter. Tide verifies the account
before saving anything, keeps the password in the keyring, and stores only the
server's base URL, the account name, and each song's ID. Each request
authenticates with a fresh salted token; redirects are refused so a token never
follows a server elsewhere. HTTPS is used when the address asks for it, or
when a bare host name answers securely. Plain HTTP works with any server once
the person confirms connecting without encryption; a bare host name that doesn't
answer over HTTPS offers that choice.

The v70 search filters songs by the complete query across song, album and
artist text. Results use the sortable Songs columns and include an empty
result state. Escape closes search. Large song and album track lists recycle
only the rows around the viewport while retaining their full scroll height.

The store retains per-source visibility (schema v3). A song stays visible
while any copy belongs to a shown source. Hiding does not remove records or
change playlists, and queued songs still play.

A source's More button, right-click, touch hold and keyboard context action
open the same menu. Remote sources offer Sync, Edit, Sign out and Remove;
local sources offer Sync and Show in Filer. Sign-out confirms, saves a one-time
backup, clears the server login and retains downloaded songs. Its Undo restores
the login from memory. Sign-in uses the account form again.
Removing a server cancels its sync, forgets its songs (songs held by a playlist
stay as unavailable), deletes its downloads and cached artwork, and clears the
keyring item. Confirmation and a deferred removal provide Undo; each removal
has its own timer. Nothing on the server changes.

Sync is event-driven: at launch, when the network changes, and on Refresh. An
unchanged server library (same scan count and time) is not re-read.

Albums and songs can be downloaded for offline listening. A download belongs to
the server copy it duplicates, lives under `$XDG_DATA_HOME/luma-tide/offline`,
is written atomically, and plays when the server is out of reach. A lost
connection requeues a download; other failures can be retried.

Automatic selection prefers a reachable local copy, then a download, and then
another reachable copy. An explicit copy wins only while it remains reachable. Switching copies
keeps the queue item and seeks the replacement pipeline to the current
position.

## Playback failure policy

GStreamer errors identify the current item and advance to the next reachable
queue item. Tide stops after every item has failed once; it never loops over a
broken queue indefinitely. End of queue stops unless repeat-all is enabled.
Repeat-one restarts the current track. Quit stops playback; closing the window
while playback is active intentionally keeps the application held for MPRIS
and media-key control.

Position publication is bounded to two updates per second while playing and
stops while paused. Continuous MPRIS `Position` property-change signals are not
emitted; standard clients extrapolate position and receive `Seeked` after an
explicit seek.

## Contained LumaUI preview

The native preview uses `python3 -m luma_tide.preview`, which passes
`org.projectluma.Tide.LumaUIPreview` directly to `TideApplication`. Production
startup retains `org.projectluma.Tide`. Preview MPRIS uses its own bus name and
desktop entry, and the live semantic extension receives the actual application
ID. The launcher does not rely on a renamed Python constant or an environment
variable that a different application class might ignore.
The window also uses that native ID for its own saved geometry, keeping
preview window state separate from production.

After four full conform PASS reports, sync the shared kit into
`~/.local/share/luma-dev/lumaui-tide`, copy this source directory into its
`app/tide` directory, and install `bin/luma-tide-lumaui-preview` as its `bin/run`.
Use `data/org.projectluma.Tide.LumaUIPreview.desktop.in` for the preview launcher,
replacing `@PREVIEW_PREFIX@` with that absolute preview prefix. Its Exec has no
Prairie module argument and D-Bus activation is disabled. The native runner
retains the shared preview's BASE-NVR compatibility check.

Before a real-data launch, verify native identity on a private bus and display:

```sh
dbus-run-session -- xvfb-run -a env LUMA_TIDE_IDENTITY_PRIVATE_BUS=1 \
  PYTHONPATH=src/luma-platform/appkit:src/luma-tide \
  python3 tests/luma-tide/check_preview_identity.py
```

This exercises the actual preview entry point, Gtk/GApplication constructor,
unique registration and MPRIS name. Startup data access and window creation
are intercepted; XDG paths are also isolated. A production-ID sentinel must
keep its ID and activation, and preview bus names must be released afterward.
Installing a preview never replaces the production launcher or leaves a window
open. Runtime evidence is required; preparing this check is not a PASS claim.

## Build and verification

The attached deck's song title opens expanded Now Playing. Its native button
supports mouse, Enter and Space without extra visual chrome; Space on this
entry does not toggle playback. Existing close and Escape actions remain.
Verify the real UI actions, including desktop Up next/About and phone entry,
on an isolated display and bus:

```sh
dbus-run-session -- xvfb-run -a env GDK_BACKEND=x11 TIDE_NOW_PRIVATE_BUS=1 \
  PYTHONPATH=src/luma-platform/appkit:src/luma-tide \
  python3 tests/luma-tide/now_playing_runtime.py
```

This uses in-memory fixture data, actual pointer/key events and unchanged
playback-state assertions. The phone retains v70's hidden side panel.

Run local model and contract tests:

```sh
PYTHONPATH=src/luma-tide python3 -m unittest discover -s tests/luma-tide -v
PYTHONPATH=src/luma-platform/sdk python3 src/luma-platform/sdk/bin/luma lint \
  src/luma-tide/luma-app.toml
tests/smoke/luma-tide.sh
```

Build the noarch RPM on Fedora 44 x86_64 or aarch64:

```sh
scripts/packages/build-luma-tide.sh
```

The static SDK contract does not substitute for the outstanding graphical,
screen-reader, suspend/resume, codec, PipeWire device-change, cold-boot,
upgrade/rollback, and physical FP6 gates. Those require the canonical Luma
images and honest readiness-ledger evidence.
# LumaUI album ordering

The Albums view remembers locally played albums in `album-history.json` beside
Tide's existing library database. This additive store contains hashed album
identifiers and playback timestamps only; it does not change the database,
music files, server data or credentials. Playback history is written in a
worker using a locked read-modify-write and atomic replacement. Unknown fields
are preserved, and the first edit of an existing file saves a private
`album-history.json.before-first-write` backup. Invalid history is left intact.
Fixture mode never creates or reads this store.
