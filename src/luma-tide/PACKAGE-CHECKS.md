# Tide package checks after the v70 port

All seven GTK runtime files in the RPM check list remain. Their stores,
credentials, window state and audio belong to temporary test fixtures. The
package runs them on Xvfb and a private session bus with its built Tide on
PYTHONPATH. No timing budget in `large_library_runtime.py` was changed.

The port changes the following presentation contracts. Updating tests for
these changes does not add retired controls to the approved v70 UI.

| Check | Retired UI contract | v70 contract and retained functional proof |
|---|---|---|
| empty_runtime | Stack empty page, three player islands, source-kind popover | Empty album grid, hidden inactive deck and closed pane; existing folder/server actions and actual Sources form |
| empty_runtime | Grouped Search page, empty playlist page, sidebar eye | Inline Songs results and zero-match rows; playlist stays empty while browsing; existing store visibility filters restore every record |
| large_library_runtime | Legacy list/loading/search widgets | Actual completed asynchronous library reads, bounded SongRows, playback/ticks, concurrent sync, refresh/filter/search timings and complete final song count; original budgets preserved |
| navigation_runtime | Breadcrumb strip and dedicated artist page | Shared trail and album tile, back/forward and real Alt keys; artist tile opens a release; inline Songs search and return through history |
| credit_link_runtime | Markup links inside legacy song rows | Actual deck artist/album buttons; real pointer, Tab and Enter navigate while controller state and play/toggle call counts stay unchanged |
| columns_runtime | Configurable columns, Plays from/copy menu, playlist chooser | Fixed aligned Title/Album/Time schema, source facts/membership and true copy counts; controller copy switching preserves record/queue/position; inline and rebound Love, album queue order, playlist contents, artist releases and narrow geometry |
| interaction_runtime | One click selects/two clicks play; six-pixel dot and eye | v70 `data-tplay` plays on one click; actual Return/Space, pause preservation, queue/search playback, nine-pixel source mark and state changes; retained visibility filters and real mouse/keyboard More/context menu actions |
| sign_in_runtime | Attention banner, ServerDialog, Test Connection button | Actual startup/import and Sources details/Edit form; wrong-password/no-write, read-only remote test, successful sync without restarting, lost-password recovery, cached songs and offline Sync retry |

Required modules are listed in Meson, including identity, preview, recency
and deferred removals. Tide's source export includes the existing canonical
`tests/fixtures/tide-v70.json` under `fixtures/`, where the package unit tests
expect it. No reference artwork is copied.

Two app fixes accompany these checks: an Edit form reads the saved full server
URI instead of its display host, and repeated refresh requests cancel obsolete
queued reads while retaining the running read and latest requested state.
Native buttons handle their own Space activation; the window shortcut applies
outside buttons and editable fields.

Native checks found and fixed the app's four-pixel song/header inset difference:
aligned rows now take their padding from `TableHeader.align`, while album rows
keep Tide's token-based inset. Cover/column slots use native container disposal
with the same custom measurements. Rebuilt search results restore an unmapped
keyboard focus to the search entry; the window captures its own shortcuts.
Input checks target the actual GdkX11 surface, rather than a title-matched
window. The 420px phone assertion measures that whole surface including the
kit frame, and Xvfb's absent window manager is replaced by a native X11 resize.
The same two-pixel alignment, queue/copy/playlist and input assertions remain.
Checks wait for allocation and the asynchronous player snapshot before acting.
Source exports omit local `_build` artifacts, and installed styles resolve
inside the imported package's own prefix, including a contained DESTDIR.

Run the RPM's commands with the built package on PYTHONPATH. In particular:

```sh
PYTHONPATH=.:tests python3 -m unittest discover -s tests -v
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1440x1000x24" \
  env GSK_RENDERER=cairo PYTHONPATH=.:tests python3 tests/empty_runtime.py
```

The RPM also runs large-library (with its existing fatal-warning and 10,000
seed-song settings), navigation, credit, columns, interaction, sign-in and
real-GStreamer gapless checks. Preparation and syntax checks are not package
PASS evidence; include the exact source/package identity and command results
in the relevant issue or pull request. Visual comparison remains separate from
functional and installed-package qualification.

## Finite native artwork admission candidate

The isolated cover-admission candidate requires shared platform
91~cover20261003.1. AlbumCover passes its real local path to the shared native
CoverArt viewport owner and receives `artwork-loaded` for the existing artwork
lighting. PlayingBackdrop uses the same bounded native preview lifetime without
changing its blur, generated fallback, scrim, playback, sources or files.

The new normal check `artwork_admission_runtime.py` constructs 96 actual Tide
AlbumCover widgets, saturates the shared64-key loader and requires all real
textures plus the existing on_loaded callbacks. It also maps/unmaps a native
PlayingBackdrop and requires real pixels then released texture ownership.
Temporary generated images are the only inputs. Source parsing is not PASS.

Run the artwork checks against the source and shared-platform package actually
being built. Component-level checks do not establish full-package or image
qualification. Use temporary generated images and isolated stores so verification
does not modify personal accounts, playback state, metadata or artwork.
