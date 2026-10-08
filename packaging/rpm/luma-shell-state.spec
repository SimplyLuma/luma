# SPDX-License-Identifier: Apache-2.0

Name:           luma-shell-state
Version:        0.2.0
Release:        1.luma.33.preview20260926%{?dist}
Summary:        Shared Project Luma shell-state contract
License:        Apache-2.0
URL:            https://project-luma.local/
Source0:        luma_shell_state.py
Source1:        luma-shell-state-service
Source2:        luma-shell-statectl
Source3:        org.project_luma.shell-state.gschema.xml
Source4:        luma-shell-state.service
Source5:        org.project_luma.ShellState1.service
Source6:        README.md
Source7:        LICENSE.md
Source8:        gtk.css
Source9:        index.theme
Source10:       gtk-dark.css
Source11:       luma-common.css
Source12:       index-dark.theme
Source13:       gtk4.css
Source14:       gtk4-dark.css
Source15:       gtk-translucent.css
Source16:       gtk4-translucent.css
Source17:       index-translucent.theme
BuildArch:      noarch
BuildRequires:  glib2
BuildRequires:  python3
Requires:       glib2
Requires:       python3-gobject-base

%description
Provides the form-factor-neutral state and D-Bus contract consumed by Luma's
GNOME desktop and Phosh handheld presentation engines.

%prep
cp %{SOURCE7} LICENSE

%build

%install
install -D -m 0644 %{SOURCE0} \
  %{buildroot}%{_libexecdir}/luma-shell-state/luma_shell_state.py
install -D -m 0755 %{SOURCE1} \
  %{buildroot}%{_libexecdir}/luma-shell-state-service
install -D -m 0755 %{SOURCE2} \
  %{buildroot}%{_bindir}/luma-shell-statectl
install -D -m 0644 %{SOURCE3} \
  %{buildroot}%{_datadir}/glib-2.0/schemas/org.project_luma.shell-state.gschema.xml
install -D -m 0644 %{SOURCE4} \
  %{buildroot}%{_userunitdir}/luma-shell-state.service
install -D -m 0644 %{SOURCE5} \
  %{buildroot}%{_datadir}/dbus-1/services/org.project_luma.ShellState1.service
install -D -m 0644 %{SOURCE6} %{buildroot}%{_pkgdocdir}/README.md
install -D -m 0644 %{SOURCE8} \
  %{buildroot}%{_datadir}/themes/Luma/gtk-3.0/gtk.css
install -D -m 0644 %{SOURCE9} \
  %{buildroot}%{_datadir}/themes/Luma/index.theme
install -D -m 0644 %{SOURCE10} \
  %{buildroot}%{_datadir}/themes/Luma-dark/gtk-3.0/gtk.css
# GTK's theme-variant convention: <theme>/gtk-3.0/gtk-dark.css is the dark
# variant GTK loads for gtk-application-prefer-dark-theme, and the file
# libhandy's HdyStyleManager probes before deciding a theme supports dark.
# Without it, any libhandy app following the system colour scheme (Disks) is
# switched to Adwaita and loses the Luma theme entirely.
install -D -m 0644 %{SOURCE10} \
  %{buildroot}%{_datadir}/themes/Luma/gtk-3.0/gtk-dark.css
install -D -m 0644 %{SOURCE10} \
  %{buildroot}%{_datadir}/themes/Luma-dark/gtk-3.0/gtk-dark.css
install -D -m 0644 %{SOURCE11} \
  %{buildroot}%{_datadir}/themes/Luma/gtk-3.0/luma-common.css
install -D -m 0644 %{SOURCE11} \
  %{buildroot}%{_datadir}/themes/Luma-dark/gtk-3.0/luma-common.css
install -D -m 0644 %{SOURCE12} \
  %{buildroot}%{_datadir}/themes/Luma-dark/index.theme
install -D -m 0644 %{SOURCE13} \
  %{buildroot}%{_datadir}/themes/Luma/gtk-4.0/gtk.css
install -D -m 0644 %{SOURCE14} \
  %{buildroot}%{_datadir}/themes/Luma/gtk-4.0/gtk-dark.css
install -D -m 0644 %{SOURCE14} \
  %{buildroot}%{_datadir}/themes/Luma-dark/gtk-4.0/gtk.css
install -D -m 0644 %{SOURCE14} \
  %{buildroot}%{_datadir}/themes/Luma-dark/gtk-4.0/gtk-dark.css
# Frost and Glass for toolkits that cannot take part in a treatment: GTK 3
# applications, and Chromium and the Electron applications that read their
# window colours from the GTK 3 theme. One defined opaque stand-in rather than
# whatever each toolkit picks for itself. The light theme's own sheets are
# installed beside it under the name its entry point imports.
install -D -m 0644 %{SOURCE8} \
  %{buildroot}%{_datadir}/themes/Luma-translucent/gtk-3.0/luma-base.css
install -D -m 0644 %{SOURCE11} \
  %{buildroot}%{_datadir}/themes/Luma-translucent/gtk-3.0/luma-common.css
install -D -m 0644 %{SOURCE15} \
  %{buildroot}%{_datadir}/themes/Luma-translucent/gtk-3.0/gtk.css
install -D -m 0644 %{SOURCE15} \
  %{buildroot}%{_datadir}/themes/Luma-translucent/gtk-3.0/gtk-dark.css
install -D -m 0644 %{SOURCE13} \
  %{buildroot}%{_datadir}/themes/Luma-translucent/gtk-4.0/luma-base.css
install -D -m 0644 %{SOURCE16} \
  %{buildroot}%{_datadir}/themes/Luma-translucent/gtk-4.0/gtk.css
install -D -m 0644 %{SOURCE16} \
  %{buildroot}%{_datadir}/themes/Luma-translucent/gtk-4.0/gtk-dark.css
install -D -m 0644 %{SOURCE17} \
  %{buildroot}%{_datadir}/themes/Luma-translucent/index.theme
install -d -m 0755 \
  %{buildroot}%{_userunitdir}/graphical-session.target.wants
ln -s ../luma-shell-state.service \
  %{buildroot}%{_userunitdir}/graphical-session.target.wants/luma-shell-state.service

%check
glib-compile-schemas --strict --dry-run %{buildroot}%{_datadir}/glib-2.0/schemas
# The stand-in colour lives in two places -- the design tokens and this
# theme -- so something has to compare them. A `grep` that matches nothing
# exits 1 here rather than passing quietly.
fallback=$(sed -n 's/^@define-color luma_header_opaque \(#[0-9a-f]\{6\}\);$/\1/p' \
  %{buildroot}%{_datadir}/themes/Luma-translucent/gtk-3.0/gtk.css)
test "$fallback" = "#f7f8f8" || { echo "GTK 3 fallback header is $fallback"; exit 1; }
fallback4=$(sed -n 's/^@define-color luma_header_opaque \(#[0-9a-f]\{6\}\);$/\1/p' \
  %{buildroot}%{_datadir}/themes/Luma-translucent/gtk-4.0/gtk.css)
test "$fallback4" = "$fallback" || { echo "GTK 4 fallback header is $fallback4"; exit 1; }
for entry in gtk-3.0/gtk.css gtk-3.0/luma-base.css gtk-3.0/luma-common.css \
             gtk-4.0/gtk.css gtk-4.0/luma-base.css index.theme; do
  test -s "%{buildroot}%{_datadir}/themes/Luma-translucent/$entry" ||
    { echo "Luma-translucent is missing $entry"; exit 1; }
done
PYTHONPYCACHEPREFIX=%{_builddir}/luma-state-check-pycache python3 -m py_compile %{buildroot}%{_libexecdir}/luma-shell-state/luma_shell_state.py \
  %{buildroot}%{_libexecdir}/luma-shell-state-service %{buildroot}%{_bindir}/luma-shell-statectl

%files
%license LICENSE
%doc %{_pkgdocdir}/README.md
%{_libexecdir}/luma-shell-state/
%{_libexecdir}/luma-shell-state-service
%{_bindir}/luma-shell-statectl
%{_datadir}/glib-2.0/schemas/org.project_luma.shell-state.gschema.xml
%{_userunitdir}/luma-shell-state.service
%{_userunitdir}/graphical-session.target.wants/luma-shell-state.service
%{_datadir}/dbus-1/services/org.project_luma.ShellState1.service
%{_datadir}/themes/Luma/
%{_datadir}/themes/Luma-dark/
%{_datadir}/themes/Luma-translucent/

%posttrans
glib-compile-schemas %{_datadir}/glib-2.0/schemas &>/dev/null || :

%changelog
* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.2.0-1.luma.33.preview20260926
- Rebuilt for the integrated Shell candidate: the luma group layout, a
  fourteen pixel Shelf padding and the Studio clock sizes.

* Mon Sep 22 2026 Project Luma <maintainers@projectluma.org> - 0.2.0-1.luma.31.preview20260922
- Frost and Glass give toolkits that cannot be translucent one defined
  title band instead of the stock window colour: GTK 3 applications, and
  Chromium and the Electron applications that read their window colours
  from the GTK 3 theme, follow the Luma-translucent theme.

* Sun Sep 20 2026 Project Luma <maintainers@projectluma.org> - 0.2.0-1.luma.30.preview20260920
- Touchpad gestures: three fingers switch app, four switch workspace, and the
  defaults are written once so a person's own choice is kept

* Sat Sep 20 2026 Project Luma <maintainers@projectluma.org> - 0.2.0-1.luma.29.preview20260920
- Keys for the dock's folders: what is pinned, how each opens and what was seen

* Sat Sep 19 2026 Project Luma <maintainers@projectluma.org> - 0.2.0-1.luma.28.preview20260919
- Add shelf-free-placement (b, default false): without it, movable islands go
  beside another island or to an edge's start, center or end, and a stored
  free position is normalised to the nearest of those. Additive.

* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 0.2.0-1.luma.27.preview20260918
- Add shelf-arrangement (aa{sv}, default []) for movable shelf islands
  (ADR-044), and normalise it in the shell-state snapshot the way the Shell
  reads it. The key is additive; schema-version stays 2 and
  shelf-layout-version 1.

* Wed Sep 02 2026 Project Luma <maintainers@projectluma.org> - 0.2.0-1.luma.16
- Style the window-owned title row and the pane toolbar of the one-title-row
  rule for leaflet windows (Disks): flat title row, collapsed pane toolbar.

* Wed Sep 02 2026 Project Luma <maintainers@projectluma.org> - 0.2.0-1.luma.15
- Ship gtk-dark.css in both Luma GTK 3 theme directories so GTK's variant
  lookup and libhandy's HdyStyleManager keep the Luma theme when an
  application follows the system dark colour scheme (Disks lost the theme).

* Wed Sep 02 2026 Project Luma <builds@project-luma.local> - 0.2.0-1.luma.13
- Present paned and leaflet panes on the native work surface as sibling islands,
  flatten secondary header bars inside them, and style hosted title-slot
  controls on the command row

* Wed Sep 02 2026 Project Luma <builds@project-luma.local> - 0.2.0-1.luma.12
- Add the generic GTK 3/libhandy command-row surface, connected command groups
  and their RTL and backdrop states to the native Luma GTK 3 theme

* Tue Sep 01 2026 Project Luma <builds@project-luma.local> - 0.2.0-1.luma.10
- Use the GTK 3 legal 600 weight for the automatic identity label; GTK 3
  rejects 650 and dropped the rule with a theme parser warning

* Tue Sep 01 2026 Project Luma <builds@project-luma.local> - 0.2.0-1.luma.9
- Begin the GTK 3 native work surface one 9px step below the title row, use the
  shared 12px/8px title-row inset, and give the automatic identity label the
  Figtree 650 weight used by GTK 4 and libadwaita

* Tue Sep 01 2026 Project Luma <builds@project-luma.local> - 0.2.0-1.luma.8
- Carry the native work-surface treatment through libhandy's internal window
  deck while preserving the direct-child boundary for ordinary GTK windows

* Tue Sep 01 2026 Project Luma <builds@project-luma.local> - 0.2.0-1.luma.7
- Add the system-owned GTK 3 native window and work-surface presentation used
  automatically by ordinary GtkApplicationWindow applications

* Tue Sep 01 2026 Project Luma <builds@project-luma.local> - 0.2.0-1.luma.6
- Apply shared identity-button geometry and states to native GTK 3 menu buttons
  as well as non-menu identity displays

* Mon Aug 31 2026 Project Luma <builds@project-luma.local> - 0.2.0-1.luma.5
- Add the native GTK 3 automatic application identity presentation shared by
  ordinary legacy application header bars

* Mon Aug 31 2026 Project Luma <builds@project-luma.local> - 0.2.0-1.luma.4
- Add native light/dark Luma GTK 3 themes and synchronize legacy apps with
  the shared system appearance

* Sat Aug 29 2026 Project Luma <builds@project-luma.local> - 0.2.0-1.luma.3
- Add the versioned, renderer-independent Shelf layout and material contract

* Wed Aug 19 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.2
- Refine the native Phosh top panel, overview, activity cards, search, Home bar,
  favorites, and app-grid feedback against the pinned Phosh 0.55 widget tree

* Wed Aug 19 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.1
- Introduce one shell-state contract for Phosh handheld and Luma GNOME desktop
- Reuse standard GNOME keys instead of duplicating favorites and appearance
- Add a session D-Bus broker with validated, non-sensitive diagnostics
- Add a targeted native Phosh presentation over the upstream GTK 3 foundation
