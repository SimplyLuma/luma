# SPDX-License-Identifier: Apache-2.0

Name:           luma-release
Version:        1
Release:        1.luma.2%{?dist}
Summary:        Luma release files: os-release, system-release and system defaults
# Luma's identity files: Apache-2.0. Presets, rpm macros, dnf5 defaults, issue
# and polkit rules carried unchanged from Fedora's fedora-release 44-18: MIT.
License:        Apache-2.0 AND MIT
URL:            https://simplyluma.com
Source0:        luma-release.tar.gz
BuildArch:      noarch
BuildRequires:  python3

# Luma replaces Fedora's release packages the way a Fedora remix's release
# package does (generic-release): it provides what they provide, so setup
# (system-release) and fedora-repos (system-release(44)) stay satisfied, and it
# obsoletes and conflicts with them so Fedora's identity cannot come back.
Provides:       system-release
Provides:       system-release(%{fedora})
Provides:       fedora-release = %{fedora}
Provides:       fedora-release-variant = %{fedora}
Provides:       fedora-release-identity = %{fedora}
# libdnf (rpm-ostree, PackageKit) and dnf5 read $releasever from this provide
# before os-release VERSION_ID, which names Luma's own release line.
Provides:       system-release(releasever) = %{fedora}
Provides:       system-release(releasever_major) = %{fedora}
Obsoletes:      fedora-release-common < 999
Obsoletes:      fedora-release-silverblue < 999
Obsoletes:      fedora-release-identity-silverblue < 999
Obsoletes:      fedora-release-ostree-desktop < 999
Conflicts:      fedora-release-common
Conflicts:      fedora-release-identity-silverblue
Conflicts:      generic-release
Requires:       fedora-repos(%{fedora})

%description
Names the system Luma: os-release ("Luma (Prairie, Beta 0)", LOGO=luma-logo,
VERSION_CODENAME=prairie), /etc/system-release and the console issue. An image
build replaces os-release with the build's own name ("Luma (Prairie, Beta 0,
Nightly 20260916)"). It also carries the Fedora 44 base's systemd presets, rpm
macros and package manager defaults unchanged, so replacing Fedora's release
packages changes nothing else.

%prep
%setup -q -c -n luma-release

%build
python3 scripts/os/lib/release_identity.py package-os-release --contract config/os/release.env >os-release
python3 scripts/os/lib/release_identity.py package-os-release --contract config/os/release.env --system-release >luma-release
sed -n 's/^CPE_NAME=//p' os-release >system-release-cpe

%install
f=src/luma-release/fedora-44-18
cp -a $f/usr %{buildroot}/
install -D -m 0644 os-release %{buildroot}%{_prefix}/lib/os-release
install -D -m 0644 luma-release %{buildroot}%{_prefix}/lib/luma-release
install -D -m 0644 system-release-cpe %{buildroot}%{_prefix}/lib/system-release-cpe
install -d -m 0755 %{buildroot}%{_sysconfdir}/issue.d
ln -s ../usr/lib/os-release %{buildroot}%{_sysconfdir}/os-release
ln -s ../usr/lib/luma-release %{buildroot}%{_sysconfdir}/system-release
ln -s ../usr/lib/system-release-cpe %{buildroot}%{_sysconfdir}/system-release-cpe
# Tools that ask for Fedora's release by name keep Fedora's text (ADR-040).
ln -s ../usr/lib/fedora-release %{buildroot}%{_sysconfdir}/fedora-release
ln -s fedora-release %{buildroot}%{_sysconfdir}/redhat-release
ln -s ../usr/lib/issue %{buildroot}%{_sysconfdir}/issue
ln -s ../usr/lib/issue.net %{buildroot}%{_sysconfdir}/issue.net
mv %{buildroot}%{_datadir}/licenses/fedora-release-common %{buildroot}%{_datadir}/licenses/luma-release

%check
. %{buildroot}%{_prefix}/lib/os-release
test "$NAME" = Luma && test "$ID" = luma && test "$VERSION_ID" = 1 && test "$VERSION_CODENAME" = prairie
test "$PRETTY_NAME" = "Luma ($VERSION)" && test "$LOGO" = luma-logo && test "$PLATFORM_ID" = platform:f%{fedora}
case "$PRETTY_NAME" in *1.0*|*Fedora*) exit 1 ;; esac
grep -q '^Luma release 1 (' %{buildroot}%{_prefix}/lib/luma-release
grep -qx '%%fedora              %{fedora}' %{buildroot}%{_prefix}/lib/rpm/macros.d/macros.dist
test -s %{buildroot}%{_prefix}/lib/systemd/system-preset/90-default.preset
! grep -rqi 'background-logo' %{buildroot}

%files
%license %{_datadir}/licenses/luma-release/LICENSE
%license %{_datadir}/licenses/luma-release/Fedora-Legal-README.txt
%{_prefix}/lib/os-release
%{_prefix}/lib/luma-release
%{_prefix}/lib/fedora-release
%{_prefix}/lib/system-release-cpe
%{_prefix}/lib/issue
%{_prefix}/lib/issue.net
%{_sysconfdir}/os-release
%{_sysconfdir}/system-release
%{_sysconfdir}/system-release-cpe
%{_sysconfdir}/fedora-release
%{_sysconfdir}/redhat-release
%config(noreplace) %{_sysconfdir}/issue
%config(noreplace) %{_sysconfdir}/issue.net
%dir %{_sysconfdir}/issue.d
%{_prefix}/lib/rpm/macros.d/macros.dist
%{_prefix}/lib/systemd/system-preset/80-workstation.preset
%{_prefix}/lib/systemd/system-preset/81-atomic-desktop.preset
%{_prefix}/lib/systemd/system-preset/81-desktop.preset
%{_prefix}/lib/systemd/system-preset/85-display-manager.preset
%{_prefix}/lib/systemd/system-preset/90-default.preset
%{_prefix}/lib/systemd/system-preset/99-default-disable.preset
%{_prefix}/lib/systemd/user-preset/90-default-user.preset
%{_prefix}/lib/systemd/user-preset/99-default-disable.preset
%{_datadir}/dnf5/libdnf.conf.d/20-fedora-defaults.conf
%{_datadir}/polkit-1/rules.d/org.projectatomic.rpmostree1.rules

%changelog
* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 1-1.luma.2
- Replace Fedora's release packages: os-release names Luma (Prairie, Beta 0)
  with LOGO=luma-logo, /etc/system-release reads "Luma release 1 (...)",
  Fedora 44's presets, rpm macros and dnf5 defaults are carried unchanged.

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 1-1.luma.1
- Provide system-release(releasever) so rpm-ostree, dnf5 and PackageKit resolve
  $releasever to the Fedora base release while os-release VERSION_ID is Luma's.
