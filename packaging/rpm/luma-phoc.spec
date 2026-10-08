# SPDX-License-Identifier: GPL-3.0-or-later

%global gvdb_commit 4758f6fb7f889e074e13df3f914328f3eecb1fd3

Name: phoc
Version: 0.55.1
Release: 2.luma.7%{?dist}
Summary: Display compositor designed for phones
License: GPL-3.0-or-later
URL: https://gitlab.gnome.org/World/Phosh/phoc
Source0: phoc-v0.55.1.tar.gz
Source1: gvdb-%{gvdb_commit}.tar.gz
Patch0: 0001-luma-quiet-graphical-session-shield.patch
Patch1: 0002-luma-render-prism-during-shell-startup.patch
Patch2: 0003-luma-persistent-presence-handoff.patch
Patch3: 0004-luma-presence-above-startup-shield.patch
Patch4: 0005-luma-trace-presence-handoff.patch
Patch5: 0006-luma-render-presence-after-shield-callback.patch
Patch6: 0007-luma-physical-drawer-settling.patch

BuildRequires: gcc
BuildRequires: meson
BuildRequires: gettext
BuildRequires: pkgconfig(gio-2.0)
BuildRequires: pkgconfig(glib-2.0)
BuildRequires: pkgconfig(gobject-2.0)
BuildRequires: pkgconfig(glesv2)
BuildRequires: pkgconfig(gnome-desktop-3.0)
BuildRequires: pkgconfig(libinput)
BuildRequires: pkgconfig(libudev)
BuildRequires: pkgconfig(libdrm)
BuildRequires: pkgconfig(pixman-1)
BuildRequires: pkgconfig(wayland-client)
BuildRequires: pkgconfig(wayland-server)
BuildRequires: pkgconfig(wayland-protocols)
BuildRequires: pkgconfig(xkbcommon)
BuildRequires: pkgconfig(gmobile)
BuildRequires: pkgconfig(wlroots-0.20)
BuildRequires: pkgconfig(gsettings-desktop-schemas)
Requires: gmobile

%description
Phoc with Luma's source-owned quiet shield and persistent Presence-to-Home
handoff. One compositor retains the physical display across authentication.

%prep
%autosetup -a1 -p1 -n phoc-v0.55.1
mv gvdb-%{gvdb_commit} subprojects/gvdb

%build
%meson -Dembed-wlroots=disabled -Dman=false -Dtests=false
%meson_build

%install
%meson_install
%find_lang %{name}

%check
%{buildroot}%{_bindir}/phoc --help | grep -Fq -- --persistent
grep -Fq 'phoc_server_is_handoff_view' src/render.c
grep -Fq '#define DRAG_ACCEPT_THRESHOLD_DISTANCE 8' src/layer-shell-effects.c
grep -Fq '#define DRAG_SETTLE_V_MIN 650' src/layer-shell-effects.c
grep -Fq 'parallel swipe recognizer' src/cursor.c

%files -f %{name}.lang
%doc README.md
%license LICENSES
%{_bindir}/phoc
%{_bindir}/phoc-outputs-states
%{_datadir}/phoc
%{_datadir}/glib-2.0/schemas/mobi.phosh.phoc.gschema.xml
%{_datadir}/applications/mobi.phosh.Phoc.desktop
%{_datadir}/icons/hicolor/symbolic/apps/mobi.phosh.Phoc.svg

%changelog
* Tue Sep 01 2026 Project Luma <maintainers@projectluma.org> - 0.55.1-2.luma.7
- Give draggable layer surfaces one continuous distance-and-velocity settle
  path and prevent duplicate swipe animation over the same touch sequence

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.55.1-2.luma.6
- Append Presence after the render-end startup-shield callback

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.55.1-2.luma.5
- Add bounded diagnostics for the Presence identity and post-shield render gate

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.55.1-2.luma.4
- Render Presence above the startup shield and preserve its exclusive input

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.55.1-2.luma.3
- Retain one compositor across the authenticated Presence-to-Home handoff
