# SPDX-License-Identifier: Apache-2.0
#
# Release lineage: `Release:` here MUST be higher than the highest value
# actually shipped anywhere (a nightly image, packages.txt/inputs.env, the
# build server's rpm pool, or a device) -- never just "one more than the
# branch you started from". A 2026-09-15 integration accidentally shipped
# 2.luma.2 from a feature branch that forked before 2.luma.3 through
# 2.luma.15 had landed elsewhere; rpm/rpm-ostree correctly read that as a
# downgrade and it was caught only at the pipeline admission step. Before
# bumping this, check: `git log --all --oneline -- packaging/rpm/luma-tide.spec`,
# every branch's spec (list branch names with `git branch -a`, then for each
# run `git show <branch>:packaging/rpm/luma-tide.spec`), and the build
# server's rpm pool/incoming dirs for a NVR higher than what you expected.
#
# The rpm pool and the published nightlies -- not a branch spec -- are the real
# answer to "what exists". Search them with plain `find /`: a `find / -xdev`
# silently skips /mnt and /tmp, which is where the pool
# (/mnt/luma-secondary/luma-build/os-release/fs/rpms/pool), the composed
# nightlies (.../fs/builds/*/packages/Packages) and most builders' output live.
# On 2026-09-15 that exact mistake led to a prairie-icon-theme release being
# described as "never built" when it was in fact composed into five nightlies.
# Also check whether a number is claimed by another agent's branch, and whether
# that branch has actually built and pooled it. If it has not, going above it
# makes their next build a downgrade, so your change belongs in their branch
# rather than in a higher release of your own. If it has, the next number up is
# simply free and your change goes on top. On 2026-09-15 this spec's own
# 2.luma.17 was misread as an unbuilt claim when the gapless workstream had
# already built, admitted and pinned it, which made 2.luma.18 -- not a branch
# cherry-pick -- the right home for the canvas artwork re-sync.
Name:           luma-tide
Version:        0.1.0
Release:        2.luma.33.creator20261007.1%{?dist}
Summary:        Source-aware native music player for Project Luma
License:        Apache-2.0
URL:            https://projectluma.org/apps/tide
Source0:        luma-tide.tar.gz
Source1:        LICENSE.md
Source2:        org.project_luma.shell-state.gschema.xml

BuildArch:      noarch
BuildRequires:  luma-developer-platform >= 0.1.0-1.luma.102.creator20261007.1
BuildRequires:  dbus-daemon
BuildRequires:  glib2
BuildRequires:  xorg-x11-server-Xvfb
BuildRequires:  xdotool
BuildRequires:  python3-gobject-base
BuildRequires:  meson >= 1.3
BuildRequires:  ninja-build
BuildRequires:  python3-devel >= 3.11
BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib
# tests/gapless_runtime.py drives a real GStreamer pipeline (playbin3,
# appsink, and FLAC encode/decode via gst-plugins-good) to prove the
# about-to-finish/STREAM_START gapless hand-off is genuinely one continuous
# pipeline, not a stop/reload. python3-gobject (not just -base) is needed
# for the Gst-1.0 typelib.
BuildRequires:  python3-gobject
BuildRequires:  gstreamer1
BuildRequires:  gstreamer1-plugins-base
BuildRequires:  gstreamer1-plugins-good
# %%check indexes real FLAC files with shared embedded covers.
BuildRequires:  python3-mutagen
Requires:       python3 >= 3.11
Requires:       python3-gobject
Requires:       python3-mutagen
Requires:       gtk4 >= 4.18
Requires:       libadwaita >= 1.7
Requires:       luma-developer-platform >= 0.1.0-1.luma.102.creator20261007.1
Requires:       gstreamer1
Requires:       gstreamer1-plugins-base
# souphttpsrc streams from Navidrome/Subsonic servers; libsoup takes TLS from
# glib-networking, and source passwords live in the secret service via libsecret.
Requires:       gstreamer1-plugins-good
Requires:       glib-networking
Requires:       libsecret

%description
Tide is a responsive GTK/libadwaita music library and player that keeps local,
removable, and supported remote copies in one durable source-aware model. It
uses GStreamer with PipeWire output and exports the same playback session over
MPRIS for system media controls. Navidrome and other Subsonic-compatible servers
stream and download for offline listening, with passwords kept in the keyring.

%prep
%autosetup -n luma-tide
cp %{SOURCE1} LICENSE.md

%build
%meson
%meson_build

%install
%meson_install
desktop-file-validate %{buildroot}%{_datadir}/applications/org.projectluma.Tide.desktop

%check
# The light/dark native checks use the same shell-state schema shipped by the
# OS, compiled into a private check directory and memory-only settings store.
install -d tests/schemas
install -m 0644 %{SOURCE2} tests/schemas/
glib-compile-schemas tests/schemas
export GSETTINGS_SCHEMA_DIR="$PWD/tests/schemas"
export GSETTINGS_BACKEND=memory
# Exercise the installed tree, outside the source cwd/PYTHONPATH. Source-only
# runtime checks previously hid a missing ui_phone module in the actual RPM.
tide_install_test=$(mktemp -d)
(cd "$tide_install_test" && dbus-run-session -- xvfb-run -a --server-args="-screen 0 1440x1000x24" env GSK_RENDERER=cairo GDK_DEBUG=no-portals TIDE_INSTALLED_ROOT=%{buildroot} PYTHONPATH=%{buildroot}%{python3_sitelib} %{python3} "$OLDPWD/tests/installed_startup_runtime.py")
rm -rf "$tide_install_test"
# D-Bus activation starts the binary this package installed, wherever the
# prefix put it, and no template placeholder survives.
# Settings only lists an application that says it notifies, and its
# notification and badge switches are unreachable otherwise.
grep -Fxq 'X-GNOME-UsesNotifications=true' %{buildroot}%{_datadir}/applications/org.projectluma.Tide.desktop
grep -Fxq 'Name=org.projectluma.Tide' %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.Tide.service
grep -Fxq 'Exec=%{_bindir}/org.projectluma.Tide --gapplication-service' %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.Tide.service
if grep -Fq '@' %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.Tide.service; then
  echo 'unsubstituted placeholder in org.projectluma.Tide.service' >&2
  exit 1
fi
PYTHONPATH=$PWD %{python3} -m unittest discover -s tests -v
appstream-util validate-relax --nonet data/org.projectluma.Tide.metainfo.xml
PYTHONPATH=$PWD %{python3} -m py_compile luma_tide/*.py bin/luma-tide-retire-preview-overrides

# The retirement unit must actually run before anything it's meant to
# protect against reading a stale override.
grep -Fxq 'Before=dbus.service graphical-session-pre.target' data/luma-tide-retire-preview-overrides.service
grep -Fxq 'ExecStart=%{_bindir}/luma-tide-retire-preview-overrides' data/luma-tide-retire-preview-overrides.service

dbus-run-session -- xvfb-run -a --server-args="-screen 0 1440x1000x24" env GSK_RENDERER=cairo PYTHONPATH=.:tests python3 tests/empty_runtime.py
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1440x1000x24" env GSK_RENDERER=cairo GDK_DEBUG=no-portals G_DEBUG=fatal-warnings TIDE_LARGE_SONGS=10000 PYTHONPATH=.:tests python3 tests/large_library_runtime.py
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1440x1000x24" env GSK_RENDERER=cairo PYTHONPATH=.:tests python3 tests/navigation_runtime.py
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1440x1000x24" env GSK_RENDERER=cairo PYTHONPATH=.:tests python3 tests/credit_link_runtime.py
# Track list columns, "Plays from" and spoken counts.
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1440x1000x24" env GSK_RENDERER=cairo GDK_DEBUG=no-portals PYTHONPATH=.:tests python3 tests/columns_runtime.py
# Pointer and keyboard behaviour of the lists, and the source rows.
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1440x1000x24" env GSK_RENDERER=cairo GDK_DEBUG=no-portals PYTHONPATH=.:tests python3 tests/interaction_runtime.py
# The real application and window: a preview library upgraded without its
# saved password signs in again from the banner and syncs without a restart.
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1440x1000x24" env GSK_RENDERER=cairo PYTHONPATH=.:tests python3 tests/sign_in_runtime.py

# Current v71 desktop and phone contracts, plus compact player/source checks.
for runtime in deck_narrow_runtime search_panel_runtime v71_album_actions_runtime v71_desktop_runtime v71_startup_search_runtime creator_forms_runtime creator_audio_runtime; do
  dbus-run-session -- xvfb-run -a --server-args="-screen 0 1440x1000x24" env GSK_RENDERER=cairo GDK_DEBUG=no-portals PYTHONPATH=.:tests python3 "tests/$runtime.py"
done

# Real-GStreamer gapless continuity proof. Audio pipelines need neither a
# display nor a session bus, so this runs directly rather than under
# xvfb-run/dbus-run-session like the GTK runtime checks above.
PYTHONPATH=.:tests python3 tests/gapless_runtime.py

%files
%license LICENSE.md
%{_bindir}/org.projectluma.Tide
%{_bindir}/luma-tide-retire-preview-overrides
%{python3_sitelib}/luma_tide/
%{_datadir}/applications/org.projectluma.Tide.desktop
%{_datadir}/dbus-1/services/org.projectluma.Tide.service
%{_datadir}/metainfo/org.projectluma.Tide.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Tide.svg
%{_datadir}/luma-tide/tide.css
%{_userunitdir}/luma-tide-retire-preview-overrides.service
%{_userunitdir}/graphical-session-pre.target.wants/luma-tide-retire-preview-overrides.service

%changelog
* Tue Oct 06 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-2.luma.32.creator20261006.1
- Keep source actions reachable in short windows and adaptive drawers
- Share original local audio files through actual clipboard and GIO receivers
- Separate Now Playing navigation and route Quit through stop-and-quit
- Gate short-window Light/Ink forms and native file delivery

* Mon Oct 05 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-2.luma.31.creator20261005.1
- Supply the canonical glyph for the production Quit Tide command
- Verify all application-menu icon names through actual GTK theme lookup

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-2.luma.29.creator20261004.1
- LumaUI update from the Prairie rollout review.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-2.luma.27
- LumaUI port: the app is rebuilt on LumaUI to match the approved design.

* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-2.luma.26
- Every track of an album that shares one embedded cover now indexes. The
  artwork cache is content-addressed and metadata workers run in parallel;
  the temporary file each wrote was named from the cover's digest and the
  process id only, so workers extracting the same cover collided on it and
  the later tracks failed with "File exists" (6 of 24 in the new test).
  Each write now uses its own mkstemp name and is renamed into place, and a
  cache that cannot be written leaves the track in the library without
  artwork and logs why. %check now has Mutagen, so the tag and artwork
  tests that used to skip run.
- The queue folds away below 1220px instead of the song list being squeezed
  under its minimum and clipped, and Tide opens at 1280x760. With the pinned
  luma-developer-platform the wide layout needs about 1216px of window when
  the Source column shows, and at the old 1120px default %check's large
  library run aborted on libadwaita's "exceeds width" warning; unmodified
  2.luma.25 source fails the same way against platform 1.luma.74.

* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-2.luma.25
- The open queue toggle takes the kit's quiet state fill (ADR-043), so play
  keeps the view's one ink fill. Needs luma-developer-platform 1.luma.72
- The "Plays from" column appears in every row as soon as a second source is
  added; the rows used to be rebound before the column was decided
- Column names sit exactly over their columns: the header's inset now matches
  where a row's cells begin, including the kit's list row padding

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-2.luma.24
- Clicking an artist opens the artist: their albums newest first with
  singles and EPs labelled, their top songs, Play and Shuffle, and every
  song one click further. Albums lead wherever there is a choice
- Album pages carry a fact line (year, tracks, minutes); the header's
  controls share one height; sidebar counts and transport times use
  tabular figures and no longer shift
- A server's "[Unknown Artist]" and "[Unknown Album]" read "Unknown
  artist" and "Unknown album", including songs synced before
* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-2.luma.23
- Track lists are one line per song in columns (number, title, artist,
  album, plays from, time) under a header that lines up with them; an
  album's own list drops the artist and album columns and names a guest
  artist under the title
- "Plays from" replaces the source glyph: the state dot and name of the
  source the song plays from, and how many sources have it; hidden when
  there is only one source
- Counts read as spoken ("1 copy", "2 copies") through gettext, and the
  Now playing section labels are sentence case
* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-2.luma.22
- Source rows show a 6px state dot instead of a stretched bar and a check
  mark, with a show/hide control that appears on hover or keyboard focus
- One click selects a song, two clicks play it; Enter or Space on the
  focused row plays without a double press. Choosing a place still takes one
  click
- Hover, selected, focused and playing are defined once and used by every
  list; the song playing shows a mark where its number was
- Declare X-GNOME-UsesNotifications so Settings lists Tide and its
  per-application notification and badge switches reach it
* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-2.luma.21
- Bring the full remote-source feature set of the Navidrome preview build
  into the packaged Tide: Add Source chooser, server sign-in sheet with a
  connection test, sync, offline downloads, source filters, grouped search
  and virtualized lists, combined with breadcrumbs, credit links, gapless
  playback and startup hardening
- Fix the remote sync crash ('TideWindow' object has no attribute
  'action_bar') that left a server stuck with no way to sign in again
- A server that needs signing in, is out of reach or failed to connect now
  shows a banner and a source action that fixes it; signing in again resumes
  sync without a restart
- Import a retired preview build's library once: sources, account name and
  keyring reference, songs, play history, playlists, queue, cached artwork
  and downloads; read either saved sign-in format
- Converge both earlier schema lineages on library schema 4
* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-2.luma.20
- Fix luma-tide-retire-preview-overrides.service (added in 2.luma.19)
  failing to start at almost every login: its ReadWritePaths listed two
  directories most accounts don't have without the "-" prefix systemd
  requires to tolerate a missing path, and its backup destination needs
  to be created, not merely accessed. Caught before 2.luma.19 was pinned,
  by starting the unit under a real systemd --user session rather than
  only through the Python-level tests
* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-2.luma.19
- Harden startup so a failure is never silent: the "tide" logger had no
  handler configured anywhere, so log.info/log.exception calls were
  silently dropped -- a real, independent contributor to a field incident
  where a user's library was never migrated with zero journal output.
  Opening/migrating the library and adopting a dev-preview library are now
  wrapped so any failure is always logged with a full traceback, and each
  forces an immediate WAL checkpoint so that state can't be lost to a
  later crash before an eventual opportunistic checkpoint
- Retire a stale dev-preview launch override (D-Bus service file and/or
  desktop entry) before the session bus or GNOME Shell can read it: a new
  systemd --user oneshot unit, enabled by default, ordered before
  dbus.service and graphical-session-pre.target, closes a login-time cache
  race where a correctly-retired override could still get served from an
  already-cached resolution. Moves, never deletes, and only ever touches a
  file that doesn't point at the real installed binary
* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-2.luma.18
- A dev-preview build of Tide (with working Subsonic support before shipped
  Tide had it) stored a source's password under an older libsecret schema
  name, keyed by the same opaque reference; SecretServiceStore now falls
  back to that schema transparently on a lookup miss and migrates what it
  finds, so a preview user's saved sign-in survives the upgrade
- Fold a dev-preview build's own SQLite library into the real one on first
  startup after upgrading, exactly once: its source rows (name, kind, uri,
  local, capabilities, auth_ref) are imported read-only, deduplicated
  against the real library by id and by canonical URI, and then populated
  by the normal scan/sync every known source already goes through
* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-2.luma.17
- Play albums gaplessly by default: the next track's URI is handed to
  GStreamer from playbin's about-to-finish signal while the current track is
  still playing, so a track boundary is one continuous pipeline instead of a
  stop/reload, with no silence or glitch. Works for local files and for
  Subsonic/Navidrome streams (the next stream is signed and prebuffered
  ahead of the boundary) and across a format change
- Queue position, MPRIS metadata and the now-playing display now advance on
  the actual stream change rather than on end-of-stream, so nothing names the
  next track before its audio starts
- A track that cannot be prepared in time no longer breaks the track that is
  playing: that one boundary falls back to the previous load-on-eos behavior
  and says so
* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-2.luma.16
- Add Subsonic/Navidrome server support: add/edit/remove a server, browse,
  search, and play a remote library, with credentials held only in the
  system secret service and a clear "can't reach server" state that never
  drops a source or its cached library
- Make artist and album credits clickable links in track rows, the album
  header, the now-playing bar and full-screen player, the queue, and search
  results, navigating on the existing NavigationTrail (2.luma.14's back
  control/breadcrumb) rather than a second navigation mechanism
- Fix multi-artist tag data being silently truncated to one artist at
  ingestion; artist/album-artist are now structured lists carried through
  from real multi-value ID3/Vorbis/MP4 tags, with a schema migration and an
  automatic one-time re-index so existing libraries backfill the data with
  no user action
- MPRIS xesam:artist/xesam:albumArtist now report every credited artist

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-2.luma.15
- Generate the D-Bus service file from the install prefix

* Sun Sep 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-2.luma.14
- Go back from an album, artist or search with a back control, breadcrumb,
  the mouse back button or Alt+Left

* Fri Sep 04 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-2.luma.3
- Ship the D-Bus service the desktop entry's activation needs

* Fri Sep 04 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-2.luma.2
- Tide is the Application Kit's window: the toolkit draws the frame, three islands and the transport

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-2.luma.1
- Fix startup against GTK 4.22 viewport wrapping and remove unsupported CSS

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- Add the native adaptive Tide music library, source/copy model, and player
- Add transactional playlists and queue, asynchronous scanning, and MPRIS
- Package the same application binary for desktop and handheld composition
