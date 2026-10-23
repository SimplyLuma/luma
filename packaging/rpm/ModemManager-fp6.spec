%global _lto_cflags -flto=2 -ffat-lto-objects
%global build_cflags %{build_cflags} -DLUMA_NATIVE_ESIM_PRODUCTION_OWNER
%bcond check 1
# Keep LTO workers within the reviewed two-CPU normal producer.

%global glib2_version %(pkg-config --modversion glib-2.0 2>/dev/null || echo bad)
%global qmi_version %(pkg-config --modversion qmi-glib 2>/dev/null || echo bad)
%global mbim_version %(pkg-config --modversion mbim-glib 2>/dev/null || echo bad)
%global qrtr_version %(pkg-config --modversion qrtr-glib 2>/dev/null || echo bad)

Name: ModemManager
Version: 1.25.95
Release: 0.14.luma14%{?dist}
Summary: Mobile broadband manager with native eSIM profile operations and FP6 GNSS
License: GPL-2.0-or-later
URL: https://gitlab.freedesktop.org/mobile-broadband/ModemManager
Source0: ModemManager-d776ea38d29ca472a12323c1d45002ee19a66f57.tar.gz
Source1: luma-meson.build
Source2: luma-src-mm-base-modem.c
Source3: luma-src-mm-base-modem.h
Source4: luma-src-mm-sim-qmi.c
Source5: luma-src-luma-owned-apdu-wire.h
Source6: luma-src-luma-profile-terminal-registry-DRAFT.h
Source7: luma-introspection-org.freedesktop.ModemManager1.Sim.xml
Source8: luma-src-mm-sim-qmi.h
Source9: luma-src-mm-iface-modem.c
Source10: luma-src-mm-iface-modem.h
Source11: luma-src-mm-broadband-modem-qmi.c
Source12: luma-src-mm-uim-deactivation-recovery.inc
Source13: luma-src-luma-native-candidate-evidence.h
Source14: luma-src-luma-readonly-es10-guard.h
Source15: luma-src-luma-native-operation-epoch.h
Source18: luma-src-mm-iface-modem-voice.c
Source17: luma-src-mm-base-call.c
Source16: luma-src-mm-shared-qmi.c
Source19: luma-test-native-recovery-controls.py
Source20: luma-test-native-recovery-controls.c.in
Patch0: 0001-shared-qmi-unlock-AFW-gated-GNSS-engines-at-LOC-start.patch
Patch1: 0002-shared-qmi-derive-location-from-Position-Report-indications.patch
Patch2: 0003-netlink-preserve-completion-callback-before-transaction-removal.patch
Patch3: 0004-broadband-modem-detect-5GNR-capability-from-C5GREG.patch
Patch4: 0005-iface-modem-voice-optional-idle-call-list-polling.patch
Patch5: 0006-broadband-bearer-external-packet-data-through-an-external-service.patch
Patch6: 0007-modem-helpers-accept-MediaTek-IMS-voice-calls-in-CLCC.patch
Patch7: 0008-broadband-modem-optional-CFUN-power-down.patch
Patch8: 0009-broadband-modem-ignore-access-technology-from-unregistered-domains.patch
Patch9: 0010-broadband-modem-enable-5GS-registration-reports.patch
Patch10: 0011-broadband-modem-initial-EPS-bearer-settings-through-the-external-data-service.patch
Patch11: 0012-iface-modem-voice-keep-calls-not-yet-started-on-call-list-reports.patch

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
ModemManager built from a pinned upstream commit with maintained native
SIM-slot discovery, modem-owned serialized eSIM profile operations and
operation epochs. The same package retains the SM7635 GNSS support and
netlink transaction-lifetime repair for FP6 qmap transitions.

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
%autosetup -N -n ModemManager-d776ea38d29ca472a12323c1d45002ee19a66f57

# Exact maintained native owner source overlay.
install -m 0644 %{SOURCE1} meson.build
install -m 0644 %{SOURCE2} src/mm-base-modem.c
install -m 0644 %{SOURCE3} src/mm-base-modem.h
install -m 0644 %{SOURCE4} src/mm-sim-qmi.c
install -m 0644 %{SOURCE5} src/luma-owned-apdu-wire.h
install -m 0644 %{SOURCE6} src/luma-profile-terminal-registry-DRAFT.h
install -m 0644 %{SOURCE7} introspection/org.freedesktop.ModemManager1.Sim.xml
install -m 0644 %{SOURCE8} src/mm-sim-qmi.h
install -m 0644 %{SOURCE9} src/mm-iface-modem.c
install -m 0644 %{SOURCE10} src/mm-iface-modem.h
install -m 0644 %{SOURCE11} src/mm-broadband-modem-qmi.c
install -m 0644 %{SOURCE12} src/mm-uim-deactivation-recovery.inc
install -m 0644 %{SOURCE13} src/luma-native-candidate-evidence.h
install -m 0644 %{SOURCE14} src/luma-readonly-es10-guard.h
install -m 0644 %{SOURCE15} src/luma-native-operation-epoch.h
install -m 0644 %{SOURCE18} src/mm-iface-modem-voice.c
install -m 0644 %{SOURCE17} src/mm-base-call.c
install -m 0644 %{SOURCE16} src/mm-shared-qmi.c

# Apply maintained GNSS and netlink patches after the exact native source overlay.
%autopatch -p1
printf '%s\n' 'f88626c13b4732fa9aeb553bf4d268f4c97dc0a97fa3e8d4dba6bc9211d597fc  meson.build' '1cfb4fa1151c66fda3316c47f2bf2cc5e237cff876396ec0f3709318028cb190  src/mm-shared-qmi.c' '7ede19190c237982a1704a5fa239da92e96912673cf7c037121bdc51e9c4c478  src/mm-sim-qmi.c' 'f96d4c54e7c6453eb8691c14eaeefe8aadabf8beace09d64293a59732f08c71e  src/mm-sim-qmi.h' 'f9a6675b6ea1007d38b323a34712ed3c2fa1e04774ff61c97e9bf317de3e7d51  src/mm-netlink.c' | sha256sum -c

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
%{__python3} %{SOURCE19} src/mm-uim-deactivation-recovery.inc %{SOURCE20}
%ninja_build -C %{_vpath_builddir} meson-test-prereq
%meson_test --no-rebuild
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
* Fri Oct 23 2026 Project Luma - 0.14.luma14
- Keep calls created but not yet started when the call list is reported, so
  call list polling no longer terminates a call before it is dialled.

* Fri Oct 23 2026 Project Luma - 0.13.luma13
- External initial EPS bearer settings are bearer properties dictionaries,
  kept by the service as given, so the settings read back match.

* Fri Oct 23 2026 Project Luma - 0.12.luma12
- Enable 5GS registration reports (+C5GREG=2), so a modem on 5G SA reports
  its access technology and cell.
- Load and set the initial EPS bearer settings through the external data
  service.

* Fri Oct 23 2026 Project Luma - 0.11.luma11
- Ignore the access technology of registration replies for unregistered
  domains, so a modem on 5G SA no longer reports GSM.
- External data bearer: pass on the name servers the data service reports.

* Fri Oct 23 2026 Project Luma - 0.10.luma10
- External data bearer: find the returned net port among all modem ports.

* Fri Oct 23 2026 Project Luma - 0.9.luma9
- Poll the call list while idle on ports tagged
  ID_MM_AT_VOICE_IDLE_CALL_LIST_POLL, for modems that report incoming calls
  only to another host channel (MediaTek IMS).
- Bearers through an external packet data service on ports tagged
  ID_MM_AT_DATA_SERVICE (MediaTek CCCI modems).
- Accept MediaTek IMS voice calls (mode 20) in +CLCC.
- Low power state through +CFUN on ports tagged ID_MM_AT_CFUN_POWER_DOWN.

* Thu Oct 22 2026 Project Luma - 0.8.luma8
- Detect the 5GNR capability from +C5GREG=? when +WS46=? lists no NG-RAN
  mode, so 5G SA registration is read (MediaTek modems).

* Tue Oct 13 2026 Project Luma - 0.7.luma7
- Restore exact owned SIM power once after deactivation proof becomes stale.
- Retain fail-closed lease and readiness proof requirements.

* Tue Oct 13 2026 Project Luma - 0.6.luma6
- Cancel finished retained native owners while preserving exact release authority.
- Preserve existing GNSS/netlink patches and all normal package checks.
* Mon Oct 12 2026 Project Luma - 0.5.luma5
- Integrate the qualified native normal profile-management source cohort.
* Mon Oct 12 2026 Luma Engineering <engineering@projectluma.org> - 1.25.95-0.4.luma4
- Candidate: authorize exact fenced native SIM recovery through existing Reset
- Keep default production gates closed; explicit nonshipping candidate only

* Mon Oct 12 2026 Luma Engineering <engineering@projectluma.org> - 1.25.95-0.3.luma3
- Package authenticated owned-FD admission and native channel terminal bookkeeping
- Keep public capability gates closed pending physical qualification

* Fri Aug 28 2026 Project Luma <codex@project-luma.local> - 1.25.95-0.2.luma2
- Preserve netlink completion callbacks across transaction removal
- Decode each netlink error from its own message header

* Thu Aug 20 2026 Project Luma <codex@project-luma.local> - 1.25.95-0.1.luma1
- Add the bounded FP6 AFW-gated GNSS and position-report path
