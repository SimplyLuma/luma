# SPDX-License-Identifier: GPL-3.0-only
Name: luma-greetd-session
Version: 0.10.3
Release: 1.luma.2%{?dist}
Summary: Persistent-compositor session lifecycle for Luma greetd
License: GPL-3.0-only AND Apache-2.0 AND MIT
Source0: luma-greetd
Source1: 40-luma-persistent-greetd.conf
Requires: greetd >= 0.10.3
Requires: systemd

%description
Luma's source-built greetd lifecycle extension. It preserves the display-owning
pre-authentication compositor while starting a separately authenticated client
session against the same Wayland display.

%install
install -D -m 0755 %{SOURCE0} %{buildroot}%{_libexecdir}/luma-greetd
install -D -m 0644 %{SOURCE1} \
  %{buildroot}%{_unitdir}/greetd.service.d/40-luma-persistent-greetd.conf

%check
%{buildroot}%{_libexecdir}/luma-greetd --help >/dev/null

%files
%{_libexecdir}/luma-greetd
%{_unitdir}/greetd.service.d/40-luma-persistent-greetd.conf

%changelog
* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.10.3-1.luma.1
- Add explicit persistent-compositor authenticated session ownership
