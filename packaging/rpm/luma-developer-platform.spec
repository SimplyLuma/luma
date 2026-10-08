# SPDX-License-Identifier: Apache-2.0

%global use_source_date_epoch_as_buildtime 1

Name:           luma-developer-platform
Version:        0.1.0
Release:        1.luma.107.creator20261007.1%{?dist}
Summary:        Adaptive and semantic application platform for Project Luma
License:        Apache-2.0
URL:            https://projectluma.org/developers
Source0:        luma-developer-platform.tar.gz
Source1:        LICENSE.md

BuildRequires:  gcc
BuildRequires:  meson
BuildRequires:  ninja-build
BuildRequires:  glib2-devel >= 2.80
BuildRequires:  gobject-introspection-devel
BuildRequires:  gtk4-devel >= 4.18
# The Glass surface-parity check renders a window that never loads the kit
# and compares its title band with a kit window's. Before 1.luma.52 the
# toolkit gave that window no treatment at all, so the check has something
# real to compare only against a toolkit that carries Patch0042.
BuildRequires:  libadwaita >= 1.9.3-1.luma.52
BuildRequires:  wayland-devel >= 1.23
BuildRequires:  libadwaita-devel >= 1.7
BuildRequires:  appstream
BuildRequires:  desktop-file-utils
BuildRequires:  python3-devel
BuildRequires:  python3-gobject
BuildRequires:  python3-cairo
BuildRequires:  python3-pyyaml
BuildRequires:  systemd-rpm-macros
BuildRequires:  dbus-daemon
BuildRequires:  xorg-x11-server-Xvfb
BuildRequires:  xorg-x11-xauth
BuildRequires:  xdotool
BuildRequires:  weston
Requires:       glib2 >= 2.80
Requires:       gobject-introspection
Requires:       gtk4 >= 4.18
Requires:       libadwaita >= 1.7
Requires:       python3
Requires:       python3-gobject
Requires:       systemd
# Platform56 adds the appearance/protocol surface and additive shared-control
# corrections while preserving the full Platform50 ABI. Keep isolated preview
# applications with an exact Platform50 contract installable while the
# coordinated app tuple catches up.
# Public GTK/GObject-introspection reorder feedback API.
Provides:       luma-ui-reorder-feedback = 1
Provides:       luma-developer-platform = 0.1.0-1.luma.50~preview.20260907.1%{?dist}

%description
Luma UI and Luma Semantics provide one adaptive GTK/libadwaita framework and a
display-independent semantic action/object contract across desktop, tablet,
and handheld presentations.

%package devel
Summary:        Development files for the Luma Developer Platform
Requires:       %{name}%{?_isa} = %{version}-%{release}
Requires:       glib2-devel
Requires:       gtk4-devel
Requires:       libadwaita-devel

%description devel
Headers, pkg-config metadata, GIR sources, schemas, and templates for building
applications with the Luma Developer Platform.

%package sdk
Summary:        Luma application creation and conformance tools
BuildArch:      noarch
Requires:       python3 >= 3.11
Requires:       python3-pyyaml
Requires:       %{name} = %{version}-%{release}

%description sdk
The luma command creates, inspects, lints, and runs declaration-level
conformance checks for adaptive and semantic Luma applications.

%prep
%autosetup -n luma-developer-platform
cp %{SOURCE1} LICENSE.md

%build
%meson
%meson_build

%install
%meson_install
install -d %{buildroot}%{python3_sitelib}/luma_sdk
install -m 0644 sdk/luma_sdk/*.py %{buildroot}%{python3_sitelib}/luma_sdk/
install -d %{buildroot}%{python3_sitelib}/luma_appkit
install -m 0644 appkit/luma_appkit/*.py %{buildroot}%{python3_sitelib}/luma_appkit/
install -d %{buildroot}%{python3_sitelib}/luma_appkit/icons
install -m 0644 appkit/luma_appkit/icons/lumaui-*.svg \
  %{buildroot}%{python3_sitelib}/luma_appkit/icons/
install -d %{buildroot}%{_datadir}/icons/hicolor/scalable/actions
install -m 0644 appkit/luma_appkit/icons/lumaui-*.svg \
  %{buildroot}%{_datadir}/icons/hicolor/scalable/actions/
cp -R sdk/luma_sdk/templates %{buildroot}%{python3_sitelib}/luma_sdk/
install -D -m 0755 sdk/bin/luma %{buildroot}%{_bindir}/luma
ln -s luma %{buildroot}%{_bindir}/luma-sdk
install -D -m 0644 sdk/data/org.projectluma.SemanticInspector.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.SemanticInspector.desktop
install -d %{buildroot}%{python3_sitelib}/luma_semantic_broker
install -m 0644 broker/luma_semantic_broker/*.py \
  %{buildroot}%{python3_sitelib}/luma_semantic_broker/
install -D -m 0755 broker/bin/luma-semantic-broker \
  %{buildroot}%{_libexecdir}/luma-semantic-broker
install -D -m 0755 broker/bin/luma-semantic-consent \
  %{buildroot}%{_libexecdir}/luma-semantic-consent
install -D -m 0644 broker/data/luma-semantic-broker.service \
  %{buildroot}%{_userunitdir}/luma-semantic-broker.service
install -D -m 0644 broker/data/org.projectluma.SemanticBroker1.service \
  %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.SemanticBroker1.service

%check
# Exercise the staged installed kit with its matching data, even on a fresh
# builder that has no platform in /usr/share. The normal source discovery
# accepts sheets beside the Python package, keeping live appearance signals
# enabled. These test-only links are removed before the payload is packaged.
for sheet in %{buildroot}%{_datadir}/luma-appkit/*.css; do
  ln -s "$sheet" "%{buildroot}%{python3_sitelib}/$(basename "$sheet")"
done
PYTHONPATH="$PWD/appkit" python3 tests/icon_assets.py
# MD3 must be importable from the installed package and allocate its full
# story/arrangement surface at desktop and compact widths. No display is a
# failure here, never a silent skip.
PYTHONPATH=%{buildroot}%{python3_sitelib} \
GI_TYPELIB_PATH="$PWD/%{_vpath_builddir}/appearance:$PWD/%{_vpath_builddir}/ui:$PWD/%{_vpath_builddir}/semantics" \
LD_LIBRARY_PATH="$PWD/%{_vpath_builddir}/appearance:$PWD/%{_vpath_builddir}/ui:$PWD/%{_vpath_builddir}/semantics" \
xvfb-run -a --server-args="-screen 0 1280x1024x24" dbus-run-session -- \
  env GDK_BACKEND=x11 GSK_RENDERER=cairo G_DEBUG=fatal-criticals \
  python3 tests/timeline.py
# The standard badge value agents publish for their dock icon.
PYTHONPATH="$PWD/appkit" python3 tests/background_badge.py
# New UI regressions require a display: exit 77 is not package evidence.
test_runtime=$(mktemp -d)
chmod 0700 "$test_runtime"
xvfb-run -a --server-args="-screen 0 1920x1080x24" dbus-run-session -- \
  env GSK_RENDERER=cairo G_DEBUG=fatal-criticals XDG_RUNTIME_DIR="$test_runtime" \
  meson test -C %{_vpath_builddir} --no-rebuild --print-errorlogs --num-processes 1
python3 - <<'PY_CHECK'
import json
from pathlib import Path
rows = [json.loads(line) for line in Path("%{_vpath_builddir}/meson-logs/testlog.json").read_text().splitlines() if line.strip()]
expected = {"luma-developer-platform:" + name for name in ("semantics", "background-agent", "context", "media-fader", "scaffold", "application-icon", "window-frame", "welcome", "application-menu", "native-app-updates", "empty-state", "save-sheet", "surface-policy", "surface-backdrop", "reorder-hint", "media-transport-lcd", "timeline", "mobile-identity", "hidden-width-watch")}
names = {row["name"] for row in rows}
# The thirteen platform executables, plus LumaUI-1's own part tests, token check and C/Python parity.
lumaui = {name for name in names if name.startswith("luma-developer-platform:lumaui-")}
assert expected <= names, sorted(expected - names)
assert names == expected | lumaui, sorted(names - expected - lumaui)
assert lumaui, "the LumaUI-1 part tests did not run"
assert all(row["result"] == "OK" and row["returncode"] == 0 for row in rows), [(row["name"], row["result"]) for row in rows]
print(f"Platform package native checks: {len(expected)} platform and {len(lumaui)} LumaUI executables passed; zero skips.")
PY_CHECK
GI_TYPELIB_PATH="$PWD/%{_vpath_builddir}/appearance:$PWD/%{_vpath_builddir}/ui:$PWD/%{_vpath_builddir}/semantics" \
LD_LIBRARY_PATH="$PWD/%{_vpath_builddir}/appearance:$PWD/%{_vpath_builddir}/ui:$PWD/%{_vpath_builddir}/semantics" \
PYTHONPATH=%{buildroot}%{python3_sitelib} \
LUMA_APPKIT_BASE_PATH="$PWD/appkit/luma-appkit-base.css" \
GSETTINGS_BACKEND=memory \
GSETTINGS_SCHEMA_DIR="$PWD/%{_vpath_builddir}/tests/appearance-schemas" \
xvfb-run -a --server-args="-screen 0 1920x1080x24" dbus-run-session -- env GSK_RENDERER=cairo \
  python3 tests/transaction_card.py
GI_TYPELIB_PATH="$PWD/%{_vpath_builddir}/appearance:$PWD/%{_vpath_builddir}/ui:$PWD/%{_vpath_builddir}/semantics" \
LD_LIBRARY_PATH="$PWD/%{_vpath_builddir}/appearance:$PWD/%{_vpath_builddir}/ui:$PWD/%{_vpath_builddir}/semantics" \
PYTHONPATH=%{buildroot}%{python3_sitelib} \
LUMA_APPKIT_BASE_PATH="$PWD/appkit/luma-appkit-base.css" \
xvfb-run -a --server-args="-screen 0 1920x1200x24" dbus-run-session -- env GSK_RENDERER=cairo \
  python3 tests/window_geometry.py
# How a window opens and what it may never give up (ADR-042): the shared
# opening size on a laptop panel and on an ultrawide, and the title row a
# framed desktop window keeps however narrow it is. This runs on a headless
# Weston, not Xvfb: with no compositor GTK gives an X11 window a solid 5px
# frame (solid-csd), so a window opened exactly as wide as its full layout
# needs would measure 10px short and fold. Luma's desktop is composited
# Wayland, which is what this checks.
for screen in 1536x960 3440x1440; do
check_runtime=$(mktemp -d /tmp/luma-open.XXXXXX)
XDG_RUNTIME_DIR="$check_runtime" weston --backend=headless --width="${screen%%x*}" \
  --height="${screen#*x}" --socket=luma-open --idle-time=0 >"$check_runtime/weston.log" 2>&1 &
weston_pid=$!
for _ in $(seq 100); do [ -S "$check_runtime/luma-open" ] && break; sleep 0.1; done
[ -S "$check_runtime/luma-open" ] || { tail -n 5 "$check_runtime/weston.log"; exit 1; }
status=0
XDG_RUNTIME_DIR="$check_runtime" WAYLAND_DISPLAY=luma-open GDK_BACKEND=wayland \
GI_TYPELIB_PATH="$PWD/%{_vpath_builddir}/appearance:$PWD/%{_vpath_builddir}/ui:$PWD/%{_vpath_builddir}/semantics" \
LD_LIBRARY_PATH="$PWD/%{_vpath_builddir}/appearance:$PWD/%{_vpath_builddir}/ui:$PWD/%{_vpath_builddir}/semantics" \
PYTHONPATH=%{buildroot}%{python3_sitelib} \
LUMA_APPKIT_BASE_PATH="$PWD/appkit/luma-appkit-base.css" \
dbus-run-session -- env GSK_RENDERER=cairo python3 tests/window_opening.py || status=$?
kill "$weston_pid" 2>/dev/null || :
wait "$weston_pid" 2>/dev/null || :
[ "$status" = 0 ] || exit "$status"
done
LUMA_APPKIT_BASE_PATH="$PWD/appkit/luma-appkit-base.css" \
xvfb-run -a dbus-run-session -- env GSK_RENDERER=cairo \
  python3 tests/control_styles.py
# Filled controls (ADR-043): one quiet state fill, 3:1 against the off state
# and 4.5:1 for its glyph in all four modes; ink never fills a state.
LUMA_APPKIT_BASE_PATH="$PWD/appkit/luma-appkit-base.css" \
  python3 tests/filled_controls.py
# One Glass, whatever built the window (owner, 2026-09-22). Four windows built
# four different ways are rendered on one display and their pixels compared:
# the title band must be one colour to the value, island interiors must be
# opaque, and the surface between the islands must still be see-through. This
# fails on the toolkit and the tokens before this release.
GI_TYPELIB_PATH="$PWD/%{_vpath_builddir}/appearance:$PWD/%{_vpath_builddir}/ui:$PWD/%{_vpath_builddir}/semantics" \
LD_LIBRARY_PATH="$PWD/%{_vpath_builddir}/appearance:$PWD/%{_vpath_builddir}/ui:$PWD/%{_vpath_builddir}/semantics" \
PYTHONPATH=%{buildroot}%{python3_sitelib} \
LUMA_APPKIT_BASE_PATH="$PWD/appkit/luma-appkit-base.css" \
GSETTINGS_BACKEND=memory \
GSETTINGS_SCHEMA_DIR="$PWD/%{_vpath_builddir}/tests/appearance-schemas" \
xvfb-run -a --server-args="-screen 0 1280x1024x24" dbus-run-session -- \
  env GSK_RENDERER=cairo python3 tests/glass_surface_parity.py
# Avatars: initials, glyphs for numbers, short codes and groups; the kit
# scrollbar floats over a list's edge 3px in with nothing reserved for it.
GI_TYPELIB_PATH="$PWD/%{_vpath_builddir}/appearance:$PWD/%{_vpath_builddir}/ui:$PWD/%{_vpath_builddir}/semantics" \
LD_LIBRARY_PATH="$PWD/%{_vpath_builddir}/appearance:$PWD/%{_vpath_builddir}/ui:$PWD/%{_vpath_builddir}/semantics" \
PYTHONPATH=%{buildroot}%{python3_sitelib} \
LUMA_APPKIT_BASE_PATH="$PWD/appkit/luma-appkit-base.css" \
xvfb-run -a dbus-run-session -- env GSK_RENDERER=cairo \
  python3 tests/avatars_and_scrolling.py
# Lightbox: geometry, then a real window: open over its content, step, zoom,
# the unavailable state, an animated GIF, reduced motion, close and focus return.
GI_TYPELIB_PATH="$PWD/%{_vpath_builddir}/appearance:$PWD/%{_vpath_builddir}/ui:$PWD/%{_vpath_builddir}/semantics" \
LD_LIBRARY_PATH="$PWD/%{_vpath_builddir}/appearance:$PWD/%{_vpath_builddir}/ui:$PWD/%{_vpath_builddir}/semantics" \
PYTHONPATH=%{buildroot}%{python3_sitelib} \
LUMA_APPKIT_BASE_PATH="$PWD/appkit/luma-appkit-base.css" \
xvfb-run -a dbus-run-session -- env GSK_RENDERER=cairo \
  python3 tests/lightbox.py
# The regular navigation sidebar, loaded as an application loads the kit: the
# selected row shows a neutral fill, groups are ruled, headings sit on the
# rows' inset, and an app bar ends in a rule; light and dark.
GI_TYPELIB_PATH="$PWD/%{_vpath_builddir}/appearance:$PWD/%{_vpath_builddir}/ui:$PWD/%{_vpath_builddir}/semantics" \
LD_LIBRARY_PATH="$PWD/%{_vpath_builddir}/appearance:$PWD/%{_vpath_builddir}/ui:$PWD/%{_vpath_builddir}/semantics" \
PYTHONPATH=%{buildroot}%{python3_sitelib} \
LUMA_APPKIT_BASE_PATH="$PWD/appkit/luma-appkit-base.css" \
LUMA_APPKIT_STYLE_PATH="$PWD/appkit/luma-appkit.css" \
LUMA_APPKIT_TOKENS_PATH="$PWD/appkit/luma-appkit-tokens.css" \
xvfb-run -a dbus-run-session -- env GSK_RENDERER=cairo \
  python3 tests/navigation_sidebar.py
# The save changes sheet: real key presses through the X server, so the
# application's own Ctrl+D (accelerator and key handlers) is proven silent
# while the sheet is open; the three cases, focus, Tab, inline name errors.
GI_TYPELIB_PATH="$PWD/%{_vpath_builddir}/appearance:$PWD/%{_vpath_builddir}/ui:$PWD/%{_vpath_builddir}/semantics" \
LD_LIBRARY_PATH="$PWD/%{_vpath_builddir}/appearance:$PWD/%{_vpath_builddir}/ui:$PWD/%{_vpath_builddir}/semantics" \
PYTHONPATH=%{buildroot}%{python3_sitelib} \
LUMA_APPKIT_BASE_PATH="$PWD/appkit/luma-appkit-base.css" \
LUMA_APPKIT_STYLE_PATH="$PWD/appkit/luma-appkit.css" \
LUMA_APPKIT_TOKENS_PATH="$PWD/appkit/luma-appkit-tokens.css" \
xvfb-run -a --server-args="-screen 0 1920x1080x24" dbus-run-session -- \
  env GDK_BACKEND=x11 GSK_RENDERER=cairo G_DEBUG=fatal-criticals \
  python3 tests/save_sheet.py
# Current shared runtime regressions execute against the staged installed kit.
for regression in identity_menu_position action_center_grown_inset details_pane_footer sidebar_header application_directory share_capabilities share_layout_runtime media_transport_fit media_memory media_cover_memory media_crop sdk_app_updates migration_repair_screen icon_button_accessibility; do
  scale=1
  case "$regression" in media_memory|media_cover_memory) scale=3 ;; esac
  GI_TYPELIB_PATH="$PWD/%{_vpath_builddir}/appearance:$PWD/%{_vpath_builddir}/ui:$PWD/%{_vpath_builddir}/semantics" \
  LD_LIBRARY_PATH="$PWD/%{_vpath_builddir}/appearance:$PWD/%{_vpath_builddir}/ui:$PWD/%{_vpath_builddir}/semantics" \
  PYTHONPATH=%{buildroot}%{python3_sitelib} \
  LUMA_APPKIT_BASE_PATH="$PWD/appkit/luma-appkit-base.css" \
  xvfb-run -a --server-args="-screen 0 1920x1080x24" dbus-run-session -- \
    env GSK_RENDERER=cairo GDK_SCALE="$scale" GSETTINGS_BACKEND=memory \
    python3 "tests/$regression.py"
done
PYTHONPATH=%{buildroot}%{python3_sitelib} \
  %{buildroot}%{_bindir}/luma --version
test_project=$(mktemp -d)
PYTHONPATH=%{buildroot}%{python3_sitelib} \
  %{buildroot}%{_bindir}/luma new Test --id org.projectluma.platformtest \
  --destination "$test_project"
PYTHONPATH=%{buildroot}%{python3_sitelib} \
  %{buildroot}%{_bindir}/luma lint "$test_project/luma-app.toml"
appstreamcli validate --no-net \
  "$test_project/data/org.projectluma.platformtest.metainfo.xml"
desktop-file-validate \
  "$test_project/data/org.projectluma.platformtest.desktop"
PYTHONPATH=%{buildroot}%{python3_sitelib} \
  python3 -m py_compile \
    %{buildroot}%{python3_sitelib}/luma_semantic_broker/*.py \
    %{buildroot}%{python3_sitelib}/luma_appkit/*.py
PYTHONPATH=%{buildroot}%{python3_sitelib} \
  python3 -c 'from luma_semantic_broker.validation import semantic_surface'
for sheet in %{buildroot}%{_datadir}/luma-appkit/*.css; do
  rm "%{buildroot}%{python3_sitelib}/$(basename "$sheet")"
done

%files
%license LICENSE.md
%{_libdir}/libluma-appearance-1.so.0*
%{_libdir}/girepository-1.0/LumaAppearance-1.typelib
%{_libdir}/libluma-semantics-1.so.0*
%{_libdir}/libluma-ui-1.so.0*
%{_libdir}/girepository-1.0/LumaSemantics-1.typelib
%{_libdir}/girepository-1.0/LumaUI-1.typelib
%{_datadir}/dbus-1/interfaces/org.projectluma.SemanticBroker1.xml
%{_libexecdir}/luma-semantic-broker
%{_libexecdir}/luma-semantic-consent
%{python3_sitelib}/luma_semantic_broker/
%{python3_sitelib}/luma_appkit/
%{_datadir}/luma-appkit/
%{_datadir}/icons/hicolor/scalable/actions/luma-*.svg
%{_datadir}/icons/hicolor/scalable/actions/lumaui-*.svg
%{_userunitdir}/luma-semantic-broker.service
%{_datadir}/dbus-1/services/org.projectluma.SemanticBroker1.service

%files devel
%license LICENSE.md
%{_includedir}/luma-1/
%{_libdir}/libluma-appearance-1.so
%{_libdir}/pkgconfig/luma-appearance-1.pc
%{_datadir}/gir-1.0/LumaAppearance-1.gir
%{_libdir}/libluma-semantics-1.so
%{_libdir}/libluma-ui-1.so
%{_libdir}/pkgconfig/luma-semantics-1.pc
%{_libdir}/pkgconfig/luma-ui-1.pc
%{_datadir}/gir-1.0/LumaSemantics-1.gir
%{_datadir}/gir-1.0/LumaUI-1.gir
%{_datadir}/luma-platform/

%files sdk
%license LICENSE.md
%{_bindir}/luma
%{_bindir}/luma-sdk
%{_datadir}/applications/org.projectluma.SemanticInspector.desktop
%{python3_sitelib}/luma_sdk/

%changelog
* Tue Oct 06 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.101.creator20261007.1
- Delegate Python Flatpak app-menu updates to the installed host update portal
- Keep current code running and require terminal progress before success notices
- Exercise real session D-Bus refusal, creation cleanup, owner loss and GTK menu lifecycle

* Mon Oct 05 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.93.creator20261005.1
- Shared raised transaction actions with preserved progress/state geometry

* Tue Sep 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.90.creator20261004.1
- LumaUI kit for Settings round 4: notice and placeholder tokens, fill buttons on fill-2, the phone confirm drawer, and the layer-host focus leak fixed.

* Tue Sep 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.88.lumaui20260929.1
- LumaUI kit for Settings round 3: FloatingMenu pickers, hero text buttons, action-editor forms, the mono-small type role and drawers placed before layout; accent ink follows the v70 formula.

* Mon Sep 28 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.87.lumaui20260928.1
- LumaUI kit for Settings round 2: TextButton, Switch, a compact ModeSwitch, ProgressLine, the Settings sidebar variant, a compact AccountCard and type roles, in C and Python; window controls fold away at phone width on a phone only.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.86.lumaui20260928.1
- The installed kit carries its LumaUI glyphs again, so apps find their control icons on any icon theme.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.85.lumaui20260928.1
- LumaUI kit: rollout of 2026-09-28 reconciled with the device builds.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.81~lumaui.20260926.1
- LumaUI kit fixes from the evening review.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.80~lumaui.20260926.1
- LumaUI kit fixes from on-device review: island clipping, mode switch, 13px bar text, font-independent row height, word-only bar actions, toast status role.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.79~lumaui.20260926.1
- LumaUI foundation: tokens, Lucide icons, window frame and the structure, action, content, rows, bar and media parts every Luma app now uses; includes the reorder hint.

* Wed Sep 23 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.78~preview.20260923.1
- LumaUI gains a reorder hint (luma_reorder_hint), the shared insertion
  feedback Filer's sidebar uses while items are dragged into a new order;
  provides luma-ui-reorder-feedback = 1. Otherwise 1.luma.77 unchanged.

* Mon Sep 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.77~preview.20260922.1
- One Glass for every window. Islands, panes and cards are opaque paper in
  Frost and Glass, so nothing is read through a sidebar or a month grid;
  the window surface, the title band and the gaps between islands are the
  material. The title band, menus, popovers and sheets take one veil,
  thick enough that text behind a surface does not read as text.
- A window's treatment no longer depends on its own compositor handshake:
  the backdrop owns the blur region and nothing else, and what colour a
  window is painted is one decision per display.
- Applications load their style sheets through the kit, so they follow a
  change of treatment instead of keeping the palette they started with.
- %%check renders four windows built four different ways and compares
  their pixels (tests/glass_surface_parity.py).

* Sat Sep 19 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.76~preview.20260919.1
- The save changes sheet is one C implementation in libluma-ui
  (LumaSaveRequest, LumaSaveSheet, LumaSaveDocument; LumaUI in Python), so C
  and C++ applications ask with the same sheet as Python ones. The Python
  kit's confirm_save keeps its API and drives it; AppWindow's sheet layer is
  the platform's (luma_window_set_sheet_layer). A window without that layer
  gets one the first time it asks: the sheet hangs under its header bar, only
  its content dims, the application's commands in the title bar wait.
- Enter answers with the control that has focus when that is Cancel, Don't
  save or Where; everywhere else it saves. Focus returns where it was when the
  sheet closes. Where lists the application's folders, then Documents and
  Desktop, then Other location. More than five documents scroll.
- Without a recorded edit age the body says "Your changes haven't been
  saved." instead of guessing.
- The sheet is opaque in every treatment: a new luma_sheet token (frost and
  glass no longer show through). The light Ctrl D label takes the Don't save
  red (#983b34); #9f463f measured 4.16:1 when pressed. High contrast gets its
  own red, mix(#ff3b30, window_fg, .55): 8.5:1 light, 7.2:1 dark at rest.
- tests/test-save-sheet.c (a plain C window) and tests/save_sheet.py (real
  key presses) in %check.

* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.75~preview.20260918.1
- Frost and glass are one light material with the Shell: light, see-through
  panels over the blurred wallpaper with dark ink. The surface roles are now
  layers sized so the stack composites to the recipe, not finished colours
  stacked on each other: the frame paints 62% on frost and 30% on glass, the
  title band and the panes paint over it (frost to 76% and 84%, glass to 62%
  and 70%), and cards, rows and a pane's header strip paint a thin lift over a
  pane. Stacking finished .82-.94 veils is what made every frost and glass
  window a near-opaque slab with a solid title bar.
- Text roles are one step deeper than paper's on frost and glass, and the
  state fill is a deeper slate (#4a5a6e frost, #3b4757 glass), so every text
  pair holds 4.5:1 and every control 3:1 composited over white, mid grey and
  black. tests/filled_controls.py measures each surface as the stack it
  composites to.
- Ink-filled controls (play, the primary button) print their label in paper,
  not in a translucent surface role, and the save sheet takes the menu's veil.

* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74~preview.20260918.1
- Sidebars show which view is open again. A bare row rule in luma-appkit.css
  outranked the base sheet's selected and hover fills (GTK takes a property
  from the last provider at a priority), so no regular NavigationSidebar drew
  its neutral selection. Headings are the design's 18px band on the rows'
  inset, groups after the first open with a 1px hairline 7px in, and a pane's
  app bar ends in the 1px rule that separates it from its content.

* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.73~preview.20260918.1
- Frost and glass are light, see-through panels with dark ink again, as their
  thumbnails in Settings show. 1.luma.71 smoked every frost and glass window
  surface and projected the rest from the dark family, so both modes read as
  Dark. The roles now come from the light family; the panels are denser than
  before (frost frame .82, panes .92, islands .80; glass panes .88, islands
  .78, the frame stays a see-through .31) so every text and control pair holds
  4.5:1 (3:1 for large text, controls and disabled rows) composited over pure
  black as well as white. Quiet text, section labels, the state fill and the
  menu caps are one step darker than paper's for the same reason.
- The title row's veil (luma_chrome) is its own, denser role instead of the
  frame, matching libadwaita 1.luma.50.
- tests/filled_controls.py measures frost and glass on the surfaces content is
  drawn on (pane, card, island, title row) over white and black; the bare
  see-through frame carries nothing.

* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.72~preview.20260918.1
- Integrates the Sept 17 kit work that 1.luma.71 did not carry, on top of it.
- Filled controls follow ADR-043: a checked toggle, tab or slider takes one
  quiet state fill (luma_state, luma_state_ink) and ink stays for a view's one
  emphasis. Menu headings, shortcut caps, disabled rows and sidebar and section
  headings take measured ledger colours that hold their contrast targets in
  light, dark, frost and glass.
- Frost and glass window surfaces are smoked so they carry white ink, and the
  smoked amber is bright enough for a selected row.
- One Save changes sheet for every Luma app (luma_appkit), hung from the
  window that asked, with Save, Don't Save and Cancel; it is asked on close and
  on quit.
- A window opens 16px clear of its own narrow layout instead of exactly on
  it, so a fractional scale rounding down, or a frame drawn where there is no
  compositor, cannot fold it into its phone layout as it appears.
- The desktop opening rule leaves handheld surfaces alone: the shell sizes
  them full screen, and they keep the application's own default until then.
- The window-opening check in %check runs on a headless Weston: under Xvfb
  GTK frames a window with a solid 5px border that no Luma desktop draws.
- The status dot drops max-width and max-height, which GTK CSS does not have;
  the parser warning aborted every %check test that loads the kit stylesheet.
- Save changes sheet: the name field's invalid state is passed as an integer
  and the card no longer sets labelled-by/described-by relations; through
  PyGObject both reached GTK as the wrong value type, a GObject critical. The
  sheet's check waits for each answer instead of expecting it within 120ms.

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.71~preview.20260917.1
- One rule decides the size every Luma window opens at when nothing is
  remembered for it (luma_appkit.window_policy, ADR-042): one height per
  monitor for every window, a width from the window's shape, capped inside the
  work area, never below what the application says it needs, and never inside
  the window's own narrow layout on a desktop. Applications keep declaring a
  size; it is read as their shape.
- A framed desktop window cannot lose its title row, which carries its close,
  move and resize controls. AppWindow puts the row back if a breakpoint or a
  page change hides it and records it for Luma Vitals; Phone and Messages
  opened narrow enough to trip their phone layouts and left no way to close
  them. Handheld surfaces and windows that draw their own controls are
  unaffected.
- Where the person has moved, resized or tiled a window, Tiling Shell's window
  memory is the one that puts it back: the kit no longer restores a size of its
  own while that memory is enabled. tests/window_opening.py in %check on a
  laptop panel and an ultrawide; tests/unit/test_luma_window_opening.py runs the
  rule over every Luma window as source.

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.70~preview.20260917.1
- A status dot in a NavigationRow stays a 6px dot: the indicator is centred in
  both directions and bounded in luma-appkit-base.css, where it had taken the
  row's full height and drawn as a tall bar in every app with status rows
  (Tide, aad9e5a0). Carries the `badge` value from .69, which was never pinned.

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.69~preview.20260917.1
- Background agents: a standard `badge` value for the unread badge on an app's
  dock icon, an unsigned count or "dot" (luma_appkit.background.BADGE_VALUE,
  BADGE_DOT, Agent.set_badge). BackgroundAgent1 is unchanged; the dock reads the
  value only from agents that list `badge` in `publishes`. Documented in
  docs/developer/kit/background-agents.md; tests/background_badge.py in %check.

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.68~preview.20260916.1
- Lightbox (luma_appkit.lightbox): a picture viewer laid over a window's own
  content, never a new window. It grows from the thumbnail and back, dims the
  window, zooms (pinch, Ctrl+scroll, double-click between fitted and 1:1 device
  pixels, +/-/0) and pans when zoomed, steps through a set of pictures (arrow
  keys, swipe, Prairie pan chevrons), plays animated GIFs, shows loading and
  unavailable states with Try Again, and has Save As…, Copy and Open With… (the
  only way to another app). Focus stays inside while open and returns to the
  thumbnail; motion follows the reduced-motion setting. Only the shown picture
  and its neighbours hold pixels, at display size, with the shown one decoded at
  full size only when zoomed; all are released on close.
- tests/lightbox.py runs in %check.

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.67~preview.20260916.1
- Avatar shows a photo when given one, initials otherwise (first and last
  word, punctuation, emoji and honorifics ignored, one character for scripts
  written without initials), and a person, business or group glyph for phone
  numbers, short codes, unnamed senders and groups instead of a digit or a
  bracket. avatar_initials() and avatar_kind() are public; Avatar gains medium
  (40 px), group and paintable.
- ScrollView: the kit's scrolling container. Its overlay scrollbar floats over
  the content's trailing edge 3px in, clear of rounded corners, with nothing
  reserved for it, and it cannot be switched to a reserved gutter.
  NavigationSidebar uses it, and every scrolled window inside a kit island
  (Python or C) gets the same bar in the same place from luma-appkit-base.css.
- A menu opened at the pointer opens from its corner there instead of centred
  on it, which put half of a row's menu beside the click and off a sidebar.
- tests/avatars_and_scrolling.py runs in %check.

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.66~preview.20260915.2
- The first release of this package built from the repository since
  1.luma.62~preview.20260910.1. 1.luma.64 and 1.luma.65 were both built
  outside the tree and are described below; this release is their source
  landed here, so the shipped platform is reproducible from a revision again.
- Contains, over 1.luma.62: the appkit navigation work that 1.luma.64 carried
  (luma_appkit.navigation and the menu/CSS changes for it, in this tree as
  commit 09045653); the background-agent kit that 1.luma.65 carried, taken
  from work/background-activity; and the appkit token, Semantic Broker and
  portal/agenda/calls work that landed in this tree after the 1.luma.64 build
  tree was taken and that neither shipped release contains.
- 1.luma.66 is therefore a superset of shipped 1.luma.65, not a copy of it:
  packaged payload differs from 1.luma.65 in the appkit token stylesheets and
  in luma_semantic_broker, where this tree is ahead.
- The entries below stop at 1.luma.45~preview.20260906.1. Releases .46 to .62
  were built from the tree but never given changelog entries; this release
  does not invent them.

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.65~preview.20260915.1
- Shipped, and built outside the tree: the 1.luma.64 build tree plus the
  background-agent kit, assembled in background-activity-20260915 on the
  canonical builder rather than from a revision. Its source is recorded
  byte-identically against its own source RPM on the branch
  work/background-activity-platform65 (commit fd178a7a); it does not carry
  the appkit token, broker or portal work this tree had by then.
- Background agents (ADR-033): luma_appkit.background (Agent, run_agent,
  request_background, from_agent, LiveExtensionBinding) and
  LumaBackgroundAgent in LumaSemantics-1, with its own native test.
- luma lint checks the [background] table; the application schema describes
  it; the Semantic Broker accepts a background agent's unit as its app's
  identity.

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.64~preview.20260914.1
- Shipped, and built outside the tree from the deployed 1.luma.62 source in
  luma-desk-fixes on the canonical builder: only the Release line was edited
  and no changelog entry was written. Its one source change over 1.luma.62 is
  the appkit navigation work, which is in this tree as commit 09045653.
- 1.luma.63 was never built or shipped; the release numbering skips it.

* Sun Sep 06 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.45~preview.20260906.1
- Share native compatibility frame geometry and generated tokens.

* Fri Sep 04 2026 Project Luma <hello@projectluma.org> - 0.1.0-1.luma.37
- One menu component for the whole system (menus.py), with the menu contract's tokens

* Fri Sep 04 2026 Project Luma <hello@projectluma.org> - 0.1.0-1.luma.36
- Let the kit's split views stop clipping their islands

* Fri Sep 04 2026 Project Luma <hello@projectluma.org> - 0.1.0-1.luma.35
- Give the C kit's pane the island class the toolkit styles
- Resolve command_menu_model and add_style_sheet by name from luma_appkit

* Fri Sep 04 2026 Project Luma <hello@projectluma.org> - 0.1.0-1.luma.34
- Import GObject in the kit's widgets; NumericField's signal declaration needs it

* Fri Sep 04 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.33
- One kit. The platform source had been copied into every application's
  branch and edited there, which left six versions of the window widget and
  eleven of the Python widget module in circulation, none of them on stage.
  This release is the reconciliation: the newest lineage, plus the additions
  the other copies had made — sheet reloading on treatment change, avatar
  tones, the labelled field row, the numeric field and align cluster, the
  audio meter and media fader.
- The frame belongs to the toolkit. A Luma window no longer draws its own
  title row, identity or window controls; it carries `luma-app-window` and a
  header bar carrying `luma-titlebar`, and the patched libadwaita dresses it
  the same way it dresses Filer and Calculator. The hand-drawn window
  controls widget is removed, and the base sheet no longer states a frame,
  a gutter or an island of its own.
- One vocabulary for the design's line, fill and selection: luma_line,
  luma_fill and luma_selected, as the toolkit already names them. The older
  luma_border, luma_passive and luma_pressed remain defined as aliases.
* Fri Sep 04 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.32
- Ship the kit's base stylesheet, which had never been in the package.
- Correct the appbar, status bar and segment to the design's measurements.
- Restore the island's inset edge in the dark treatment.

* Thu Sep 03 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31
- Build LumaApplicationWindow into the UI library. Luma Write links against
  luma_application_window_new, which is defined on the branch that added the
  audio AppKit work and was never built here, so Write installed cleanly and
  then died at its first symbol lookup. The two lines had added different
  widgets to the same library and neither carried the other's.


* Thu Sep 03 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.30
- Import PresentationMode into the widget module. The restored decoration rule
  reads it, and the module had stopped importing it when the rule became
  unconditional, so the reference raised NameError while the applications were
  being built. .29 was already built with the missing import.

* Thu Sep 03 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.29
- Decide window decoration from the presentation mode again. Deciding it
  unconditionally kept GTK's native frame for windowed applications, which is
  right, by also forcing a frame onto handheld fullscreen, where the shell owns
  the surface chrome; the applications' own responsive check asserts an
  undecorated window there. .28 was already built with the unconditional
  behaviour and is not rebuilt with different contents.

* Thu Sep 03 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.28
- Restore AppWindow.title_bar. The private factory that builds the title bar
  survived an integration but the public attribute holding the result did not,
  so every application that hides its chrome on handheld raised AttributeError.
  .27 was already built without it and is not rebuilt with different contents
  under the same release.

* Thu Sep 03 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.27
- Restore Avatar, EmptyState, NavigationRow, NavigationSidebar, SectionLabel,
  StatusBar and motion_duration to the public AppKit surface, together with the
  styles they depend on. They were lost during an integration while the Prairie
  applications that import them survived, so those applications could not be
  built at all. .26 was already produced without them and is not rebuilt with
  different contents under the same release.

* Thu Sep 03 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.26
- Synchronize the AppKit and SDK package recipe with the accepted shared
  desktop/handheld platform source and composition pin.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.25
- Give inactive AppKit windows a calm backdrop elevation with an unbroken
  title-row surface.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.24
- Preserve native island elevation outside the AppKit content allocation.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.23
- Protect AppKit island strokes with real borders and simulator-matched elevation.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.22
- Replace the invalid final-type split-view subclass with a native AppKit factory.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.21
- Use native libadwaita window controls and transparent split-pane boundaries.
- Make the shared work-island elevation a single semantic token.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.20
- Match simulator elevation on primary AppKit work islands.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.19
- Keep native GTK client-side framing active for compositor-owned shadow and resize behavior
- Clip shared window and island content at the canonical rounded boundaries
- Align identity icons, window controls, and toolbar dividers with simulator geometry

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.18
- Add system-appearance-aware Application Kit materials and exact shared controls.
- Ship the canonical Notes formatting and window-control symbolic assets.

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.17
- Preserve the established shell token aliases beside Application Kit roles.

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.16
- Give unified menu rows explicit semantic ink and canonical menu width.

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.15
- Add shared toolbar groups, inline rename, save status, and menu focus behavior.
- Align the canonical application window and island materials with shared tokens.

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.14
- Make the shared application body claim all available window space.

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.13
- Add the shared Python Application Kit window, island, command, and state primitives

* Thu Aug 27 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.12
- Add application contract 0.2 identity, sandbox, lifecycle, and evidence checks
- Derive Native status from cross-architecture desktop/mobile release evidence
- Require the complete PyGObject/introspection runtime used by broker-owned GTK interfaces

* Thu Aug 27 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.11
- Add authenticated, bounded, expiring Live Extension publication and host APIs
- Keep private activity content redacted while the session is locked

* Thu Aug 27 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.10
- Stabilize verified inspector identity and hardened consent process handling
- Declare the systemd-run runtime boundary used by inspector and consent UI

* Thu Aug 27 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.3
- Add the SDK-only Semantic Inspector for consent, observation, actions, and audit

* Thu Aug 27 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- Add the authenticated, fail-closed per-user Semantic Broker
- Add expiring grants, confirmation, lock denial, audit, and Notes publication

* Thu Aug 27 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- Add introspected Luma UI and Luma Semantics preview libraries
- Add adaptive application templates and declaration-level conformance tools
- Define bounded Live Extensions and the broker wire-contract scaffold
