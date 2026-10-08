# SPDX-License-Identifier: Apache-2.0

%global extension_uuid tilingshell@ferrarodomenico.com
# The reviewed GSE zip intentionally contains upstream file modes and an
# unresolved @GJS@ build placeholder. Do not let Fedora's shebang helper alter
# or reject this otherwise directly installed payload.
%global __brp_mangle_shebangs %{nil}

Name:           gnome-shell-extension-tiling-shell
Version:        17.3
Release:        1.luma.24%{?dist}
Summary:        Advanced tiling window management for GNOME Shell
License:        GPL-3.0-or-later
URL:            https://github.com/domferr/tilingshell
Source0:        tilingshell-17.3-gse76.zip
Source1:        LICENSE
# Luma defaults: no panel button (tiling lives in Quick Options), outer gap.
Source2:        90_luma-tiling-shell.gschema.override
Patch0:         0001-disconnect-signals-before-destroying-indicator.patch
Patch1:         0002-luma-visible-drag-layout.patch
Patch2:         0004-luma-quick-settings-interface.patch
Patch3:         0006-luma-tell-clients-a-window-is-tiled.patch
Patch4:         0007-luma-right-click-breaks-the-tiling-spell.patch
Patch5:         0008-luma-layouts-follow-monitors-and-setups.patch
Patch6:         0009-luma-unique-layout-ids-and-editor-cursors.patch
Patch7:         0010-luma-window-placement-memory.patch
Patch8:         0011-luma-windows-settle-once-after-monitor-changes.patch
Patch9:         0012-luma-apps-appear-as-soon-as-they-open.patch
Patch10:        0013-luma-late-placed-windows-stay-visible.patch
Patch11:        0014-luma-tiles-follow-the-work-area.patch
Patch12:        0015-luma-a-maximized-window-is-the-largest-tile.patch
Patch13:        0016-luma-windows-open-straight-into-their-tile.patch
BuildArch:      noarch
BuildRequires:  glib2
BuildRequires:  nodejs
BuildRequires:  unzip
Requires:       gnome-shell >= 45

%description
Tiling Shell 17.3 payload reviewed and published by GNOME Shell Extensions as
version 76, with narrow Luma integration patches for reliable teardown and a
discoverable always-visible drag layout. It supports GNOME Shell 45 through 50.

%prep
mkdir source
cd source
unzip -q %{SOURCE0}
find . -type d -exec chmod 0755 {} +
find . -type f -exec chmod 0644 {} +
%patch -P 0 -p1
%patch -P 1 -p1
%patch -P 2 -p1
%patch -P 3 -p1
%patch -P 4 -p1
%patch -P 5 -p1
%patch -P 6 -p1
%patch -P 7 -p1
%patch -P 8 -p1
%patch -P 9 -p1
%patch -P 10 -p1
%patch -P 11 -p1
%patch -P 12 -p1
%patch -P 13 -p1
cp %{SOURCE1} ../LICENSE
install -m 0644 %{SOURCE2} schemas/

%build
glib-compile-schemas source/schemas

%check
# Every shipped script parses, and the schema is valid.
find source -name '*.js' -print0 | xargs -0 -n1 node --check
glib-compile-schemas --strict --dry-run source/schemas
# The Luma defaults apply: no panel button, an outer gap on every edge; the
# one-time migration and the tile hold ship (0014).
test "$(GSETTINGS_SCHEMA_DIR=source/schemas gsettings get org.gnome.shell.extensions.tilingshell show-indicator)" = false
test "$(GSETTINGS_SCHEMA_DIR=source/schemas gsettings get org.gnome.shell.extensions.tilingshell outer-gaps)" = "uint32 16"
grep -qF 'lumaMigrateSettings(this.getSettings())' source/extension.js
grep -qF '_lumaHoldTile(window, desiredWindowRect' source/components/tilingsystem/tilingManager.js
grep -qF 'function lumaOuterGaps(monitorIndex' source/components/tilingsystem/tilingManager.js
grep -qF 'if (!(window instanceof Meta.Window))' source/components/tilingsystem/tilingManager.js
grep -qF 'unmanaging' source/components/tilingsystem/tilingManager.js
test -f source/components/lumaWindowMemory/windowMemory.js
grep -q '_queueEarlyPlacement' source/components/lumaWindowMemory/windowMemory.js
# A held window is never held noticeably (0012).
grep -q '  holdMax: 1000,' source/components/lumaWindowMemory/windowMemory.js
grep -qF 'markShown(signal)' source/components/lumaWindowMemory/windowMemory.js
grep -q '_placeAfterReveal(window, hold)' source/components/lumaWindowMemory/windowMemory.js
# A window placed after it is shown is revealed without an ease the move
# could cut off, and a guard restores any reveal left unfinished (0013).
grep -qF 'const late = placeAfter && why !== "in place";' source/components/lumaWindowMemory/windowMemory.js
grep -qF 'if (!late && animate' source/components/lumaWindowMemory/windowMemory.js
grep -qF '_ensureVisible(window, actor, "reveal guard")' source/components/lumaWindowMemory/windowMemory.js
# A window with a place of its own gets it in Mutter's initial configure, by
# position and size (set_rect's by-value rectangle reaches Mutter as garbage
# from GJS), and the hold behind it is short (0016).
grep -qF 'window.connect("configure", (_window, config) => this._onInitialConfigure(window, data, config))' source/components/lumaWindowMemory/windowMemory.js
grep -qF 'config.set_position(x, y);' source/components/lumaWindowMemory/windowMemory.js
! grep -qF 'config.set_rect(' source/components/lumaWindowMemory/windowMemory.js
grep -q '  holdPreplaced: 150,' source/components/lumaWindowMemory/windowMemory.js

%install
install -d -m 0755 \
  %{buildroot}%{_datadir}/gnome-shell/extensions/%{extension_uuid}
cp -a source/. \
  %{buildroot}%{_datadir}/gnome-shell/extensions/%{extension_uuid}/
# Patched files keep whatever mode patch left them with, and cp -a carries
# it into the payload. Normalize here, after the last step that can change a
# mode, and prove it below.
payload=%{buildroot}%{_datadir}/gnome-shell/extensions/%{extension_uuid}
find "$payload" -type d -exec chmod 0755 {} +
find "$payload" -type f -exec chmod 0644 {} +
# Every payload entry is a directory at 0755 or a file at 0644; anything
# else fails the build, and every such entry is named. Files that must be
# there are checked too, so a walk that finds nothing cannot pass.
find "$payload" -mindepth 1 -printf '%%y %%m %%P\n' | sort -k3 >payload-modes.txt
payload_entries=$(wc -l <payload-modes.txt)
awk '!(($1 == "d" && $2 == "755") || ($1 == "f" && $2 == "644"))' \
  payload-modes.txt >payload-bad-modes.txt
if [ -s payload-bad-modes.txt ]; then
  echo "payload modes: $(wc -l <payload-bad-modes.txt) of $payload_entries entries are not dir 0755 / file 0644:" >&2
  sed 's/^/  /' payload-bad-modes.txt >&2
  exit 1
fi
for required in 'f 644 extension.js' 'f 644 metadata.json' \
    'f 644 components/tilingsystem/tilingManager.js' \
    'f 644 schemas/org.gnome.shell.extensions.tilingshell.gschema.xml' \
    'd 755 schemas'; do
  grep -qxF "$required" payload-modes.txt || {
    echo "payload modes: missing: $required ($payload_entries entries seen)" >&2
    exit 1
  }
done
echo "payload modes: $payload_entries entries, every dir 0755 and every file 0644"

%files
%license LICENSE
%{_datadir}/gnome-shell/extensions/%{extension_uuid}/

%changelog
* Mon Sep 21 2026 Project Luma <maintainers@projectluma.org> - 17.3-1.luma.23
- Install every file 0644 and every folder 0755, and fail the build,
  naming the file, if any is not

* Sun Sep 20 2026 Project Luma <maintainers@projectluma.org> - 17.3-1.luma.22
- Tell Mutter the gap a maximized window keeps, so it lands where the
  full-screen tile does, on every edge and every monitor

* Sun Sep 20 2026 Project Luma <maintainers@projectluma.org> - 17.3-1.luma.21
- Let a window that closes take its hold in the tile with it
- Refuse to hold anything that is not a window, and say so

* Sat Sep 19 2026 Project Luma <maintainers@projectluma.org> - 17.3-1.luma.20
- Follow the Dash's own changes at once, gliding each window on its curve
- Put a window back in its tile when a late answer left it elsewhere
- Keep one gap everywhere: between windows, to an island and to the screen
- Leave the panel button off, once per user
- Ship the Luma defaults as a schema override

* Sat Sep 19 2026 Project Luma <maintainers@projectluma.org> - 17.3-1.luma.19
- Re-tile and put windows back only once the monitors have settled for 500 ms
- Move each window at most once per settled layout, never to another monitor
- Stop moving a window moved 3 times in 2 s, and log it for Vitals

* Sat Sep 19 2026 Project Luma <maintainers@projectluma.org> - 17.3-1.luma.18
- Tiled windows follow the work area when the Dash frees or takes an edge

* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 17.3-1.luma.17
- Never leave a window invisible when it is moved to its place after being shown
- Restore full opacity and size to any window whose reveal was interrupted, and log it

* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 17.3-1.luma.16
- Show a window being placed as soon as it reaches the screen, not only on Mutter's "shown"
- Hold a window at most 250 ms after it is on screen and 1 s in all; place it afterwards if needed
- Log every hold's length and what showed the window; warn above 500 ms

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 17.3-1.luma.15
- Put windows back before the first frame of a new monitor layout, not 1.5 s later
- Recognize monitors returning before logind's wake signal after resume
- Keep a moved window invisible until the client shows it in place (at most 1 s)
- Bring any window off its work area fully inside in one move; never remember such a place
* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 17.3-1.luma.14
- Keep running on the lock screen, so windows keep their tiles across lock and sleep
- Survive GNOME re-enabling extensions at lock without releasing any window's tile
- Finish restoring windows before the unlock reveal when monitors changed during sleep
- Open a remembered app invisibly and show it only once it is in its tile (Wayland and XWayland)
- Re-apply tiles exactly on restore instead of accepting near misses; remember maximized windows
- Log why each window is left alone on restore

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 17.3-1.luma.13
- Learn window habits over months: weight each place by the hours a window lived there, with a six-month half-life
- A strong habit wins at launch; otherwise the last placement, with no age cutoff, so apps open right after a night
- A move kept for three sessions replaces the old habit; one-off detours do not erode it
- After a monitor change, windows that were open come back exactly as they were left
- Save a placement the person makes within a quarter second

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 17.3-1.luma.12
- Remember where each app's windows live for every monitor setup, by app identity rather than process or window id
- Open an app's first window in its recent or clearly habitual place, and leave it alone otherwise
- Put windows back where they lived when monitors change, after sleep and after unlock; never scatter in a new setup
- Never move dialogs, transients, popups or splash windows, or a window the person has moved
- Add Remember window positions and Forget Window Positions to Tiling Settings (ADR-036)

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 17.3-1.luma.11
- Give every new layout its own id and repair repeated ids, so a new layout can be used
- Move layout editor dividers with GNOME 50's cursor interface

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 17.3-1.luma.10
- Remember tiling layouts per monitor setup and per physical monitor, and restore them when monitors change

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 17.3-1.luma.9
- A tap of Control breaks the tiling spell too, without holding it; toggles are logged

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 17.3-1.luma.8
- A right-click while dragging keeps the window from tiling

* Thu Aug 06 2026 Project Luma <builds@project-luma.local> - 17.3-1.luma.6
- Hide the redundant top-panel indicator by default
- Expose layout selection and creation through the existing Shell D-Bus object

* Thu Aug 06 2026 Project Luma <builds@project-luma.local> - 17.3-1.luma.5
- Make Control the default drag-time tiling escape modifier
- Honor deactivation both while rendering zones and when the window is dropped

* Thu Aug 06 2026 Project Luma <builds@project-luma.local> - 17.3-1.luma.4
- Add an explicit Show layout while dragging preference, enabled by default
- Make a two-column Luma Split the first fresh-profile layout

* Thu Aug 06 2026 Project Luma <builds@project-luma.local> - 17.3-1.luma.3
- Disconnect extension signals before destroying their indicator target

* Thu Aug 06 2026 Project Luma <builds@project-luma.local> - 17.3-1.luma.2
- Normalize archive permissions without changing upstream file content

* Thu Aug 06 2026 Project Luma <builds@project-luma.local> - 17.3-1.luma.1
- Package the unmodified GNOME Shell Extensions version 76 payload
