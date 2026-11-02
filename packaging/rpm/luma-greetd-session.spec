# SPDX-License-Identifier: GPL-3.0-only
Name: luma-greetd-session
Version: 0.10.3
Release: 1.luma.3%{?dist}
Summary: Persistent-compositor session lifecycle for Luma greetd
License: GPL-3.0-only AND Apache-2.0 AND MIT
Source0: luma-greetd
Source1: 40-luma-persistent-greetd.conf
Source2: luma_greetd.cil
Requires: greetd >= 0.10.3
Requires: systemd
Requires(post): policycoreutils
Requires(post): selinux-policy-targeted
Requires(postun): policycoreutils

%description
Luma's source-built greetd lifecycle extension. It preserves the display-owning
pre-authentication compositor while starting a separately authenticated client
session against the same Wayland display.

%install
install -D -m 0755 %{SOURCE0} %{buildroot}%{_libexecdir}/luma-greetd
install -D -m 0644 %{SOURCE1} \
  %{buildroot}%{_unitdir}/greetd.service.d/40-luma-persistent-greetd.conf
install -D -m 0644 %{SOURCE2} %{buildroot}%{_datadir}/selinux/packages/luma_greetd.cil

%check
%{buildroot}%{_libexecdir}/luma-greetd --help >/dev/null

%post
/usr/sbin/semodule -i %{_datadir}/selinux/packages/luma_greetd.cil
/usr/sbin/restorecon %{_libexecdir}/luma-greetd || :

%postun
if [ "$1" -eq 0 ]; then /usr/sbin/semodule -r luma_greetd; fi

%files
%{_libexecdir}/luma-greetd
%{_unitdir}/greetd.service.d/40-luma-persistent-greetd.conf
%{_datadir}/selinux/packages/luma_greetd.cil

%changelog
* Mon Nov 02 2026 Project Luma <maintainers@projectluma.org> - 0.10.3-1.luma.3
- Label luma-greetd xdm_exec_t through a policy module so OTA deployments keep it

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.10.3-1.luma.1
- Add explicit persistent-compositor authenticated session ownership
