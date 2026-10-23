%global _lto_cflags -flto=2 -ffat-lto-objects
%global build_cflags %{build_cflags} -DLUMA_NATIVE_ESIM_PRODUCTION_OWNER
# SPDX-License-Identifier: Apache-2.0
Name:           luma-esim-broker
Version:        0.1.0
Release:        15.luma1%{?dist}
Summary:        Native eSIM profile management for Luma Settings
License:        Apache-2.0 AND GPL-2.0-or-later
Source0:        luma-esim-broker-%{version}.tar.gz
ExclusiveArch:  x86_64 aarch64
BuildRequires:  gcc
BuildRequires:  meson
BuildRequires:  ninja-build
BuildRequires:  pkgconfig(gio-unix-2.0) >= 2.80
BuildRequires:  pkgconfig(json-glib-1.0) >= 1.8
BuildRequires:  dbus-daemon
BuildRequires:  systemd-rpm-macros
Requires:       polkit
# The policy module lets dbus-broker pass ModemManager's eSIM transport.
Requires(post): policycoreutils
Requires(postun): policycoreutils
Requires:       luma-lpac-protected-profile-selectors = 1
Requires:       luma-lpac-owned-mm-fd = 1
%{?systemd_requires}

%description
Native eSIM profile-management service for Luma Settings. The broker coordinates
modem-owned profile transport with IMS reservations and current SIM-slot and
operation-epoch evidence, using the maintained LPAC provider.

%prep
%autosetup -n luma-esim-broker-%{version}

%build
%meson
%meson_build

%install
%meson_install

%check
%ninja_build -C %{_vpath_builddir} meson-test-prereq
G_DEBUG=fatal-warnings %meson_test --no-rebuild

%post
# Completed OS image roots keep no module store; their compose step compiles
# luma_esim into the policy (scripts/mobile/compile-fp6-selinux-policy.py).
if [ -e %{_sharedstatedir}/selinux/targeted/active/modules/100/base/cil ]; then
  /usr/sbin/semodule -i %{_datadir}/selinux/packages/luma_esim.cil
fi
%systemd_post luma-esim.service

%preun
%systemd_preun luma-esim.service

%postun
if [ "$1" -eq 0 ] && [ -e %{_sharedstatedir}/selinux/targeted/active/modules/400/luma_esim ]; then
  /usr/sbin/semodule -r luma_esim
fi
%systemd_postun_with_restart luma-esim.service

%files
%license LICENSE.md
%doc README.md
%{_libexecdir}/luma-esim-service
%{_datadir}/luma-esim/org.projectluma.ESim1.xml
%{_datadir}/polkit-1/actions/org.projectluma.esim.policy
%{_datadir}/dbus-1/system.d/org.projectluma.ESim1.conf
%{_datadir}/dbus-1/system-services/org.projectluma.ESim1.service
%{_unitdir}/luma-esim.service
%{_datadir}/selinux/packages/luma_esim.cil

%changelog
* Thu Oct 22 2026 Project Luma - 15.luma1
- Refuse an already-cancelled check before constructing policy arguments.

* Thu Oct 22 2026 Project Luma - 14.luma1
- Cancel outstanding polkit challenges when their native operation ends.

* Thu Oct 22 2026 Project Luma - 13.luma1
- Deliver queued carrier notifications after verified authorized profile operations.

* Thu Oct 22 2026 Project Luma - 12.luma1
- Prepare Titan profile switches through the native owner before card mutation.

* Thu Oct 22 2026 Project Luma - 11.luma1
- Allow bounded Titan subscription handover during native reservation release.

* Thu Oct 22 2026 Project Luma - 10.luma1
- Require the owned native APDU descriptor provider used by both phone adapters.

* Thu Oct 22 2026 Project Luma - 9.luma1
- Add the native Titan modem owner while preserving bound Settings operations.
- Remove direct unowned AT access.

* Tue Oct 20 2026 Project Luma - 8.luma1
- A device can select lpac's AT backend in /usr/lib/luma-esim-broker/lpac.env
  (the Unihertz Titan 2 Elite); bound operations still need the ModemManager
  transport.

* Fri Oct 16 2026 Project Luma - 7.luma1
- Report org.projectluma.ESim1.NotReady while phone ownership is not yet
  available, so clients wait instead of showing a failure.
* Thu Oct 15 2026 Project Luma - 6.luma1
- Ship an SELinux module so dbus-broker can pass ModemManager's eSIM
  transport; without it ModemManager lost its bus name and exited.
* Tue Oct 13 2026 Project Luma - 5.luma1
- Preserve native reservation ownership and bounded terminal release.
- Add optional non-authoritative display suffix with privacy controls.
* Mon Oct 12 2026 Project Luma - 3.luma1
- Integrate the qualified native normal profile-management source cohort.
* Fri Oct 09 2026 Luma Engineering <engineering@projectluma.org> - 0.1.0-1.experimental
- Package the shared native eSIM Settings broker and private-bus checks
