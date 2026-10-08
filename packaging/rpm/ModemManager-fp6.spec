%bcond check 1

%global glib2_version %(pkg-config --modversion glib-2.0 2>/dev/null || echo bad)
%global qmi_version %(pkg-config --modversion qmi-glib 2>/dev/null || echo bad)
%global mbim_version %(pkg-config --modversion mbim-glib 2>/dev/null || echo bad)
%global qrtr_version %(pkg-config --modversion qrtr-glib 2>/dev/null || echo bad)

Name: ModemManager
Version: 1.25.95
Release: 0.2.luma2%{?dist}
Summary: Mobile broadband manager with bounded FP6 GNSS support
License: GPL-2.0-or-later
URL: https://gitlab.freedesktop.org/mobile-broadband/ModemManager
Source0: ModemManager-d776ea38d29ca472a12323c1d45002ee19a66f57.tar.gz
Patch0: 0001-shared-qmi-unlock-AFW-gated-GNSS-engines-at-LOC-start.patch
Patch1: 0002-shared-qmi-derive-location-from-Position-Report-indications.patch
Patch2: 0003-netlink-preserve-completion-callback-before-transaction-removal.patch

Requires: libmbim-utils
Requires: libqmi-utils
Requires: %{name}-glib%{?_isa} = %{version}-%{release}
Conflicts: glib2%{?_isa} < %{glib2_version}
Conflicts: libqmi%{?_isa} < %{qmi_version}
Conflicts: libmbim%{?_isa} < %{mbim_version}
Conflicts: libqrtr-glib%{?_isa} < %{qrtr_version}
Requires(post): systemd
Requires(postun): systemd
Requires(preun): systemd
Requires: polkit
Recommends: xxd

BuildRequires: meson >= 0.53
BuildRequires: dbus-devel
BuildRequires: dbus-daemon
BuildRequires: gettext-devel >= 0.19.8
BuildRequires: glib2-devel >= 2.56
BuildRequires: gobject-introspection-devel >= 1.38
BuildRequires: gtk-doc
BuildRequires: libgudev1-devel >= 232
BuildRequires: libmbim-devel >= 1.32.0
BuildRequires: libqmi-devel >= 1.39.1
BuildRequires: libqrtr-glib-devel >= 1.0.0
BuildRequires: systemd
BuildRequires: systemd-devel >= 209
BuildRequires: vala
BuildRequires: polkit-devel
%if %{with check}
BuildRequires: python3-gobject
BuildRequires: python3-dbus
%endif

%global __provides_exclude ^libmm-plugin-

%description
ModemManager built from a pinned upstream development commit with the two
reviewable patches needed by the SM7635 GNSS engine, plus a narrow netlink
transaction-lifetime repair required by repeated FP6 qmap link transitions.

%package devel
Summary: Development files for ModemManager
Requires: %{name}%{?_isa} = %{version}-%{release}
Requires: pkgconfig

%description devel
Headers and metadata for ModemManager plugins.

%package glib
Summary: GLib bindings for ModemManager
License: LGPL-2.1-or-later
Requires: glib2 >= %{glib2_version}

%description glib
GLib bindings for ModemManager.

%package glib-devel
Summary: Development files for the ModemManager GLib bindings
License: LGPL-2.1-or-later
Requires: %{name}%{?_isa} = %{version}-%{release}
Requires: %{name}-devel%{?_isa} = %{version}-%{release}
Requires: %{name}-glib%{?_isa} = %{version}-%{release}
Requires: glib2-devel >= %{glib2_version}
Requires: pkgconfig

%description glib-devel
Headers and metadata for libmm-glib.

%package vala
Summary: Vala bindings for ModemManager
License: LGPL-2.1-or-later
Requires: vala
Requires: %{name}-glib%{?_isa} = %{version}-%{release}

%description vala
Vala bindings for ModemManager.

%prep
%autosetup -n ModemManager-d776ea38d29ca472a12323c1d45002ee19a66f57 -p1

%build
%meson \
  -Ddist_version='"%{version}-%{release}"' \
  -Dudevdir=/usr/lib/udev \
  -Dsystemdsystemunitdir=%{_unitdir} \
  -Ddbus_policy_dir=%{_datadir}/dbus-1/system.d \
  -Dvapi=true \
  -Dgtk_doc=true \
  -Dpolkit=permissive \
  -Dbash_completion=false
%meson_build

%install
%meson_install
find %{buildroot}%{_datadir}/gtk-doc | xargs touch --reference meson.build
%find_lang %{name}
mkdir -p %{buildroot}%{_datadir}/bash-completion/completions/
cp -a cli/mmcli-completion %{buildroot}%{_datadir}/bash-completion/completions/mmcli

%if %{with check}
%check
%meson_test
%endif

%post
%systemd_post ModemManager.service

%preun
%systemd_preun ModemManager.service

%postun
%systemd_postun ModemManager.service

%files -f %{name}.lang
%license COPYING
%doc README.md
%{_datadir}/dbus-1/system.d/org.freedesktop.ModemManager1.conf
%{_datadir}/dbus-1/system-services/org.freedesktop.ModemManager1.service
%attr(0755,root,root) %{_sbindir}/ModemManager
%attr(0755,root,root) %{_bindir}/mmcli
%dir %{_libdir}/%{name}
%attr(0755,root,root) %{_libdir}/%{name}/*.so*
%{_udevrulesdir}/*
%{_datadir}/polkit-1/actions/*.policy
%{_unitdir}/ModemManager.service
%{_datadir}/icons/hicolor/22x22/apps/*.png
%{_datadir}/bash-completion
%{_datadir}/ModemManager
%{_mandir}/man1/*
%{_mandir}/man8/*

%files devel
%{_includedir}/ModemManager/
%dir %{_datadir}/gtk-doc/html/%{name}
%{_datadir}/gtk-doc/html/%{name}/*
%{_libdir}/pkgconfig/%{name}.pc
%{_datadir}/dbus-1/interfaces/*.xml

%files glib
%license COPYING
%{_libdir}/libmm-glib.so.*
%{_libdir}/girepository-1.0/*.typelib

%files glib-devel
%{_libdir}/libmm-glib.so
%dir %{_includedir}/libmm-glib
%{_includedir}/libmm-glib/*.h
%{_libdir}/pkgconfig/mm-glib.pc
%dir %{_datadir}/gtk-doc/html/libmm-glib
%{_datadir}/gtk-doc/html/libmm-glib/*
%{_datadir}/gir-1.0/*.gir

%files vala
%{_datadir}/vala/vapi/libmm-glib.*

%changelog
* Fri Aug 28 2026 Project Luma <codex@project-luma.local> - 1.25.95-0.2.luma2
- Preserve netlink completion callbacks across transaction removal
- Decode each netlink error from its own message header

* Thu Aug 20 2026 Project Luma <codex@project-luma.local> - 1.25.95-0.1.luma1
- Add the bounded FP6 AFW-gated GNSS and position-report path
