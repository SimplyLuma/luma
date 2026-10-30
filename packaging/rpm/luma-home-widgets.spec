# SPDX-License-Identifier: GPL-3.0-or-later
Name:           luma-home-widgets
Version:        0.1.0
Release:        1.luma.2%{?dist}
Summary:        Shared native Weather and Calendar Home widgets
License:        GPL-3.0-or-later
URL:            https://github.com/SimplyLuma/luma
Source0:        %{name}-%{version}.tar.gz
Requires:       python3-gobject-base
Requires:       prairie-core-apps
BuildRequires:  systemd-rpm-macros
BuildRequires:  gcc
BuildRequires:  python3-gobject-base
BuildRequires:  dbus-daemon
BuildRequires:  xorg-x11-server-Xvfb
BuildRequires:  meson
BuildRequires:  pkgconfig(gio-2.0)
BuildRequires:  pkgconfig(json-glib-1.0)
BuildRequires:  pkgconfig(gobject-introspection-1.0)
BuildRequires:  pkgconfig(gtk+-3.0)

%description
One shared widget model for native Desktop, Tablet and Mobile hosts. Reads
Weather's existing cache and native CalendarServer. A bounded background job
uses Weather's own provider to refresh its cache without opening the app.

%package gtk3
Summary:        GTK3 host for shared Home widgets
Requires:       %{name}%{?_isa} = %{version}-%{release}
%description gtk3
Native GTK3 view for existing Phosh hosts using the shared widget model.

%package devel
Summary:        Development files for shared Home widgets
Requires:       %{name}%{?_isa} = %{version}-%{release}
Requires:       %{name}-gtk3%{?_isa} = %{version}-%{release}
%description devel
Headers, introspection metadata and pkg-config files for native widget hosts.

%prep
%autosetup
%build
%meson
%meson_build
%install
%meson_install
%check
%meson_test
%files
%license LICENSE
%doc README.md
%{_libexecdir}/luma-weather-refresh
%{_userunitdir}/luma-weather-refresh.service
%{_userunitdir}/luma-weather-refresh.timer
%{_userunitdir}/default.target.wants/luma-weather-refresh.timer
%{_libdir}/libluma-home-widgets.so.0*
%{_libdir}/girepository-1.0/LumaWidgets-1.0.typelib
%files gtk3
%{_libdir}/libluma-home-widgets-gtk3.so.0*
%files devel
%{_includedir}/luma-home-widgets/
%{_libdir}/libluma-home-widgets.so
%{_libdir}/libluma-home-widgets-gtk3.so
%{_libdir}/pkgconfig/luma-home-widgets*.pc
%{_datadir}/gir-1.0/LumaWidgets-1.0.gir
