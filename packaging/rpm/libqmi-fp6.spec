Name: libqmi
Version: 1.39.1
Release: 0.3.luma1%{?dist}
Summary: Qualcomm QMI library with the FP6 GNSS client-identification API
License: LGPL-2.1-or-later
URL: https://gitlab.freedesktop.org/mobile-broadband/libqmi/
Source0: libqmi-30f3e998e6cbda364ac1bc73223de20561e6d555.tar.gz
Patch0: 0003-qmicli-pdc-select-platform-or-software-load-type.patch

BuildRequires: meson >= 0.53
BuildRequires: gcc
BuildRequires: glib2-devel >= 2.56
BuildRequires: gobject-introspection-devel
BuildRequires: pkgconfig(gi-docgen) >= 2021.1
BuildRequires: pkgconfig(gudev-1.0) >= 147
BuildRequires: libmbim-devel >= 1.18.0
BuildRequires: libqrtr-glib-devel
BuildRequires: python3
BuildRequires: help2man

%description
Fedora's libqmi package rebuilt from the exact upstream commit immediately
after merge request 470. It adds the QMI LOC Register Events client
identification API required by the Fairphone 6 GNSS engine.

%package devel
Summary: Development files for libqmi
Requires: %{name}%{?_isa} = %{version}-%{release}
Requires: glib2-devel%{?_isa}
Requires: pkgconfig

%description devel
Headers and pkg-config metadata for libqmi.

%package utils
Summary: QMI command-line utilities
Requires: %{name}%{?_isa} = %{version}-%{release}
License: GPL-2.0-or-later

%description utils
Command-line utilities for QMI devices.

%prep
%autosetup -p1 -n libqmi-30f3e998e6cbda364ac1bc73223de20561e6d555

%build
%meson -Dgtk_doc=true -Dbash_completion=false
%meson_build

%install
%meson_install
find %{buildroot}%{_datadir}/doc/libqmi-glib-* -type f \
  -exec touch --reference meson.build {} +
mkdir -p %{buildroot}%{_datadir}/bash-completion/completions
cp -a src/qmicli/qmicli %{buildroot}%{_datadir}/bash-completion/completions/

%check
%meson_test

%ldconfig_scriptlets

%files
%license COPYING.LIB
%doc NEWS AUTHORS README.md
%{_libdir}/libqmi-glib.so.*
%{_libdir}/girepository-1.0/Qmi-1.0.typelib

%files devel
%{_includedir}/libqmi-glib/
%{_libdir}/pkgconfig/qmi-glib.pc
%{_libdir}/libqmi-glib.so
%{_datadir}/doc/libqmi-glib-*/
%{_datadir}/gir-1.0/Qmi-1.0.gir

%files utils
%license COPYING
%{_bindir}/qmicli
%{_bindir}/qmi-network
%{_bindir}/qmi-firmware-update
%{_datadir}/bash-completion
%{_libexecdir}/qmi-proxy
%{_mandir}/man1/*

%changelog
* Wed Aug 26 2026 Project Luma <codex@project-luma.local> - 1.39.1-0.3.luma1
- Preserve GMappedFile-owned storage while preparing PDC upload chunks
- Let qmicli select platform or software for bounded PDC config loads

* Wed Aug 26 2026 Project Luma <codex@project-luma.local> - 1.39.1-0.2.luma1
- Let qmicli select platform or software for bounded PDC config loads

* Thu Aug 20 2026 Project Luma <codex@project-luma.local> - 1.39.1-0.1.luma1
- Pin upstream client-identification API required by the FP6 GNSS engine
