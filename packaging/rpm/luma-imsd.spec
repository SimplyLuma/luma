# SPDX-License-Identifier: GPL-3.0-only AND Apache-2.0

Name:           luma-imsd
Version:        1.1.0
Release:        1.luma.1%{?dist}
Summary:        Native Linux IMS and VoLTE service for Project Luma
License:        GPL-3.0-only AND Apache-2.0
URL:            https://forgejo.catcrafts.net/Catcrafts/imsd
Source0:        luma-imsd-%{version}.tar.gz
Source1:        luma-fp6-imsd.service
Source2:        luma-fp6-ims-pdn-up.sh
Source3:        luma-fp6-ims-state-guard
Source4:        net.catcrafts.IMS1-luma.conf
Source5:        LICENSE.md

# glibc's Clang-specific FORTIFY overloads currently cannot be exported from
# libc++'s C++26 std module. Keep one explicit, ABI-stable flag set across the
# precompiled module and every importing translation unit while retaining the
# remaining compiler/linker hardening supported by this module pipeline.
%global imsd_cxxflags -g -fstack-protector-strong -mbranch-protection=standard -fstack-clash-protection -fno-omit-frame-pointer
%global imsd_ldflags -Wl,-z,relro -Wl,-z,now -Wl,--as-needed -Wl,--build-id=sha1

ExclusiveArch:  aarch64
BuildRequires:  clang
BuildRequires:  glib2-devel
BuildRequires:  libcxx-devel
BuildRequires:  lld
BuildRequires:  make
BuildRequires:  pkgconf-pkg-config
BuildRequires:  systemd-rpm-macros
Requires:       ModemManager
Requires:       bash
Requires:       glib2
Requires:       iproute
Requires:       libcxx
Requires:       libqmi-utils
Requires:       nftables
Requires:       opencore-amr
Requires:       pipewire-utils
Requires:       polkit
Requires:       systemd
Requires:       vo-amrwbenc
Provides:       imsd = %{version}-%{release}

%description
Project Luma's native Linux IMS service owns SIP registration, VoLTE call
control, RTP media, and the protected Fairphone 6 IMS bearer. It has no runtime
dependency on Android telephony or Waydroid.

%prep
%autosetup -n luma-imsd-%{version}
cp %{SOURCE5} LICENSE.luma

%build
%make_build CXXFLAGS="%{imsd_cxxflags}" LDFLAGS="%{imsd_ldflags}"

%install
install -D -m 0755 build/make/imsd %{buildroot}%{_sbindir}/imsd
install -D -m 0755 build/make/imsd-media %{buildroot}%{_libexecdir}/imsd-media
install -D -m 0755 build/make/imsd-pcscf %{buildroot}%{_libexecdir}/imsd-pcscf
install -D -m 0755 build/make/imsd-dialerd %{buildroot}%{_bindir}/imsd-dialerd
install -D -m 0644 packaging/imsd-dialerd.desktop \
  %{buildroot}%{_sysconfdir}/xdg/autostart/imsd-dialerd.desktop
install -D -m 0644 %{SOURCE1} \
  %{buildroot}%{_unitdir}/luma-fp6-imsd.service
install -D -m 0755 %{SOURCE2} \
  %{buildroot}%{_libexecdir}/luma-fp6-ims-pdn-up
install -D -m 0755 %{SOURCE3} \
  %{buildroot}%{_libexecdir}/luma-fp6-ims-state-guard
install -D -m 0644 %{SOURCE4} \
  %{buildroot}%{_datadir}/dbus-1/system.d/net.catcrafts.IMS1.conf
install -D -m 0644 packaging/net.catcrafts.IMS1.policy \
  %{buildroot}%{_datadir}/polkit-1/actions/net.catcrafts.IMS1.policy

%check
%make_build check CXXFLAGS="%{imsd_cxxflags}" LDFLAGS="%{imsd_ldflags}"

%post
%systemd_post luma-fp6-imsd.service

%preun
%systemd_preun luma-fp6-imsd.service

%postun
%systemd_postun_with_restart luma-fp6-imsd.service

%files
%license LICENSE LICENSE.luma
%doc README.md LUMA-PROVENANCE.md
%{_sbindir}/imsd
%{_bindir}/imsd-dialerd
%{_libexecdir}/imsd-media
%{_libexecdir}/imsd-pcscf
%{_libexecdir}/luma-fp6-ims-pdn-up
%{_libexecdir}/luma-fp6-ims-state-guard
%{_unitdir}/luma-fp6-imsd.service
%{_datadir}/dbus-1/system.d/net.catcrafts.IMS1.conf
%{_datadir}/polkit-1/actions/net.catcrafts.IMS1.policy
%config(noreplace) %{_sysconfdir}/xdg/autostart/imsd-dialerd.desktop

%changelog
* Thu Nov 12 2026 Project Luma <maintainers@projectluma.org> - 1.1.0-1.luma.1
- Rebase on imsd 1.1.0, carrying the Luma registration, terminating-call,
  ISIM identity, emergency and media changes
- Discover the P-CSCF from the IMS PDN's PCO with imsd-pcscf when PCSCF= is
  not configured, and hand imsd the PDN's own address
- Install the polkit voice action; the emergency ABI stays ungated
- Keep the reg-event subscription opt-in (REG_EVENT=1)
- Send DTMF as RFC 4733 events; play AMR-NB and G.711 calls

* Tue Sep 01 2026 Project Luma <maintainers@projectluma.org> - 0.3.0-1.luma.6
- Gate IMS bearer creation on completed ModemManager registration and a live
  Messaging.List boundary so boot cannot skip native SMS initialization

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.3.0-1.luma.5
- Add a number- and call-provenance-checked pre-session emergency call ABI

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.3.0-1.luma.4
- Preserve the original INVITE transaction response channel across PRACK
- Trace terminating-call response timing, transport path, and socket result

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.3.0-1.luma.3
- Advertise TCP explicitly on the protected registration Contact
- Preserve terminating reachability when the carrier declines SIP outbound

* Fri Aug 28 2026 Project Luma <maintainers@projectluma.org> - 0.3.0-1.luma.2
- Keep registration-event subscription opt-in across refresh and reconnect
- Prevent carrier-closed optional subscriptions from stranding IMS offline

* Fri Aug 28 2026 Project Luma <maintainers@projectluma.org> - 0.3.0-1.luma.1
- Package the native IMS implementation from canonical Luma source
- Declare mandatory IR.92 audio capability on registration and voice dialogs
- Install the hardened FP6 bearer, state guard, service, and D-Bus policy
