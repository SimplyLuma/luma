# SPDX-License-Identifier: Apache-2.0

%global use_source_date_epoch_as_buildtime 1

Name:           luma-mods
Version:        0.1.0
Release:        1.luma.24.creator20261007.1%{?dist}
Summary:        Project Luma Mod inspection and lifecycle foundation
License:        Apache-2.0
URL:            https://github.com/SimplyLuma/luma
BuildArch:      noarch

Source0:        luma-mods.tar.gz
Source1:        LICENSE.md

BuildRequires:  python3-devel
BuildRequires:  systemd-rpm-macros
BuildRequires:  python3-gobject-base
Requires:       python3
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       luma-developer-platform >= 0.1.0-1.luma.100
Requires:       polkit
Requires:       luma-application-installer >= 0.1.0-1.luma.55
Recommends:     python3-tuf >= 7
Recommends:     python3-sigstore

%description
Strict Luma Mod manifest inspection, capability planning, trust verification,
host inventory, crash-safe state, bounded preference lifecycle, adaptive review
UI, and a closed-by-default system-composition boundary. No privileged Mod
backend is enabled by this package.

%prep
%autosetup -n luma-mods/src/luma-mods
cp %{SOURCE1} LICENSE.md

%build

%install
install -d %{buildroot}%{python3_sitelib}/luma_mods
install -m 0644 luma_mods/*.py %{buildroot}%{python3_sitelib}/luma_mods/
install -D -m 0755 bin/luma-mod %{buildroot}%{_bindir}/luma-mod
install -D -m 0755 bin/luma-mods %{buildroot}%{_bindir}/luma-mods
install -D -m 0755 bin/luma-mod-author %{buildroot}%{_bindir}/luma-mod-author
install -D -m 0755 bin/luma-mod-catalog-author %{buildroot}%{_bindir}/luma-mod-catalog-author
install -D -m 0755 bin/luma-mod-catalog-ceremony %{buildroot}%{_bindir}/luma-mod-catalog-ceremony
install -D -m 0755 bin/luma-mod-catalog-accept %{buildroot}%{_bindir}/luma-mod-catalog-accept
install -D -m 0755 bin/luma-mod-review %{buildroot}%{_bindir}/luma-mod-review
install -D -m 0755 bin/luma-mod-recover %{buildroot}%{_bindir}/luma-mod-recover
install -D -m 0755 bin/luma-mod-system-health %{buildroot}%{_bindir}/luma-mod-system-health
install -D -m 0755 bin/luma-mod-transaction-service \
  %{buildroot}%{_libexecdir}/luma-mod-transaction-service
install -D -m 0644 README.md %{buildroot}%{_pkgdocdir}/README.md
install -D -m 0644 data/org.projectluma.Mods.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.Mods.desktop
install -d %{buildroot}%{_datadir}/luma/mods/catalog/manifests
install -d %{buildroot}%{_datadir}/luma/mods/catalog/profiles
install -m 0644 catalog/manifests/*.json \
  %{buildroot}%{_datadir}/luma/mods/catalog/manifests/
install -m 0644 catalog/profiles/*.json \
  %{buildroot}%{_datadir}/luma/mods/catalog/profiles/
install -D -m 0644 data/luma-mod-recover.service \
  %{buildroot}%{_userunitdir}/luma-mod-recover.service
install -D -m 0644 data/luma-mod-profiles.service \
  %{buildroot}%{_userunitdir}/luma-mod-profiles.service
install -D -m 0644 data/org.projectluma.ModProfiles1.service \
  %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.ModProfiles1.service
install -D -m 0644 data/luma-mod-transactions.service \
  %{buildroot}%{_unitdir}/luma-mod-transactions.service
for unit in luma-mod-boot-observe.service luma-mod-boot-promote.service \
  luma-mod-boot-watchdog.service luma-mod-boot-watchdog.timer; do
  install -D -m 0644 data/$unit %{buildroot}%{_unitdir}/$unit
done
install -D -m 0644 data/org.projectluma.ModTransactions1.service \
  %{buildroot}%{_datadir}/dbus-1/system-services/org.projectluma.ModTransactions1.service
install -D -m 0644 data/org.projectluma.ModTransactions1.conf \
  %{buildroot}%{_datadir}/dbus-1/system.d/org.projectluma.ModTransactions1.conf
install -D -m 0644 data/org.projectluma.ModTransactions1.xml \
  %{buildroot}%{_datadir}/dbus-1/interfaces/org.projectluma.ModTransactions1.xml
install -D -m 0644 data/org.projectluma.mods.policy \
  %{buildroot}%{_datadir}/polkit-1/actions/org.projectluma.mods.policy
install -d %{buildroot}%{_userunitdir}/graphical-session-pre.target.wants
ln -s ../luma-mod-recover.service \
  %{buildroot}%{_userunitdir}/graphical-session-pre.target.wants/luma-mod-recover.service
install -d %{buildroot}%{_unitdir}/multi-user.target.wants
ln -s ../luma-mod-boot-observe.service \
  %{buildroot}%{_unitdir}/multi-user.target.wants/luma-mod-boot-observe.service
install -d %{buildroot}%{_unitdir}/graphical.target.wants
ln -s ../luma-mod-boot-promote.service \
  %{buildroot}%{_unitdir}/graphical.target.wants/luma-mod-boot-promote.service
install -d %{buildroot}%{_unitdir}/timers.target.wants
ln -s ../luma-mod-boot-watchdog.timer \
  %{buildroot}%{_unitdir}/timers.target.wants/luma-mod-boot-watchdog.timer

%check
PYTHONPYCACHEPREFIX=%{_builddir}/pycache \
  %{python3} -m py_compile luma_mods/*.py
PYTHONPATH=$PWD %{python3} ../../tests/unit/test_luma_mods_profile_boundary.py
PYTHONPATH=$PWD %{python3} ../../tests/unit/test_luma_mods.py
PYTHONPATH=$PWD %{python3} ../../tests/unit/test_luma_mods_profile_client.py

%files
%license LICENSE.md
%doc %{_pkgdocdir}/README.md
%{_bindir}/luma-mod
%{_bindir}/luma-mods
%{_bindir}/luma-mod-author
%{_bindir}/luma-mod-catalog-author
%{_bindir}/luma-mod-catalog-ceremony
%{_bindir}/luma-mod-catalog-accept
%{_bindir}/luma-mod-review
%{_bindir}/luma-mod-recover
%{_bindir}/luma-mod-system-health
%{_libexecdir}/luma-mod-transaction-service
%{python3_sitelib}/luma_mods/
%{_datadir}/applications/org.projectluma.Mods.desktop
%{_datadir}/luma/mods/catalog/
%{_userunitdir}/luma-mod-recover.service
%{_userunitdir}/luma-mod-profiles.service
%{_datadir}/dbus-1/services/org.projectluma.ModProfiles1.service
%{_userunitdir}/graphical-session-pre.target.wants/luma-mod-recover.service
%{_unitdir}/luma-mod-transactions.service
%{_unitdir}/luma-mod-boot-observe.service
%{_unitdir}/luma-mod-boot-promote.service
%{_unitdir}/luma-mod-boot-watchdog.service
%{_unitdir}/luma-mod-boot-watchdog.timer
%{_unitdir}/multi-user.target.wants/luma-mod-boot-observe.service
%{_unitdir}/graphical.target.wants/luma-mod-boot-promote.service
%{_unitdir}/timers.target.wants/luma-mod-boot-watchdog.timer
%{_datadir}/dbus-1/system-services/org.projectluma.ModTransactions1.service
%{_datadir}/dbus-1/system.d/org.projectluma.ModTransactions1.conf
%{_datadir}/dbus-1/interfaces/org.projectluma.ModTransactions1.xml
%{_datadir}/polkit-1/actions/org.projectluma.mods.policy

%changelog
* Fri Sep 04 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.20
- The Mods gallery and review are the Application Kit's window

* Fri Aug 28 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.19
- Rebuild system Mod authority from the protected catalog inside the root service
- Derive hardware and kernel compatibility from protected host identity sources
- Admit TUF payloads into the content store with independent digest verification
- Replace client-authored deployment JSON with reviewed Mod and composition identities

* Fri Aug 28 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.18
- Connect the graphical Gallery to live TUF catalog review and installation
- Acquire preference profiles as digest-bound TUF evidence targets
- Revalidate the exact catalog composition immediately before each write

* Fri Aug 28 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.17
- Bind verified preference installs to a live TUF catalog admission
- Recompute catalog snapshot identities and enforce delegated target namespaces
- Bind declarative preference profiles to their reviewed manifest digest

* Fri Aug 28 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.16
- Bind kernel-facing Mods to exact hardware and kernel identities
- Add deterministic review/authorization locks for image-time Mod composition
- Permit recovery-gated Luma Verified hardware payloads at the kernel boundary

* Thu Aug 27 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.15
- Add explicit active-provider ownership and replacement planning
- Reject incomplete or undeclared desktop-experience provider transitions
- Surface deterministic old-to-new provider changes in CLI and graphical review

* Sat Aug 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.14
- Add role-bound catalog ceremony and signed-staging acceptance tooling
- Hold every automatic update outside its verified reviewed envelope
- Show exact dependency selections and add the Surface Pro 9 qualification gate

* Sat Aug 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.13
- Materialize verified same-inode .rpm names for rpm-ostree local-artifact parsing.

* Sat Aug 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.12
- Pass verified absolute RPM paths to rpm-ostree's supported install syntax.

* Sat Aug 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.11
- Unwrap and strictly validate Gio's nested PolicyKit method result

* Sat Aug 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.10
- Correct the typed PolicyKit CheckAuthorization GVariant tuple
- Return a closed D-Bus error for GLib/type construction failures

* Sat Aug 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.9
- Create the protected Mods state directory before boot-health isolation
- Bind recovery readiness to the health unit actually shipped by the package

* Sat Aug 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.8
- Add crash-safe known-good/candidate deployment journaling
- Add fixed boot observation, promotion, and rollback watchdog units
- Add deterministic catalog target assembly for offline TUF signing

* Sat Aug 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.7
- Fix adaptive gallery page expansion and mutually exclusive navigation
- Prevent the RPM builder container from inheriting and retaining the build lock

* Sat Aug 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.6
- Add deterministic Mod authoring init, lint, plan, and evidence commands

* Sat Aug 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.5
- Add the narrow polkit/D-Bus system transaction boundary
- Keep rpm-ostree activation closed until recovery readiness is proven

* Sat Aug 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4
- Add the adaptive Mods gallery and graphical lifecycle actions

* Sat Aug 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.3
- Add the complete bounded preference lifecycle to the CLI
- Ship the built-in Green Dock pilot and its supported Prairie dock API record

* Sat Aug 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- Recover interrupted preference transactions automatically before a session
- Keep the runtime-consumed preference-domain registry empty until an API lands

* Sat Aug 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- Add strict Mod inspection, planning, publisher verification, and host inventory
- Add crash-safe preference lifecycle with exact rollback and dependency ownership
- Keep privileged system composition closed until recovery and D-Bus gates pass
