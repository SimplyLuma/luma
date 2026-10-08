# SPDX-License-Identifier: Apache-2.0
Name:           luma-homebrew
Version:        0.1.0
Release:        1.luma.1%{?dist}
Summary:        Homebrew, ready the first time brew is typed
License:        Apache-2.0
URL:            https://projectluma.org
BuildArch:      noarch
Source0:        %{name}.tar.gz
Source1:        LICENSE.md
BuildRequires:  systemd-rpm-macros
Requires:       git-core
Requires:       curl
Requires:       file
Requires:       procps-ng
Requires:       util-linux-core
Requires:       polkit
Requires:       policycoreutils
%{?systemd_requires}

%description
Instructions on the web say "brew install". On Luma that works the first time
it is typed: a boot service creates Homebrew's folder for the primary account,
brew sets Homebrew up from its official repository on first use, and every
shell finds what it installs after the system's own commands. Homebrew's
analytics are off unless the person turns them on.

%prep
%setup -q -n %{name}
cp %{SOURCE1} LICENSE.md

%build

%install
install -D -m 0755 bin/brew %{buildroot}%{_bindir}/brew
install -D -m 0755 libexec/luma-homebrew-prefix %{buildroot}%{_libexecdir}/luma-homebrew-prefix
install -D -m 0644 data/luma-homebrew.sh %{buildroot}%{_sysconfdir}/profile.d/luma-homebrew.sh
install -D -m 0644 data/luma-homebrew-prefix.service %{buildroot}%{_unitdir}/luma-homebrew-prefix.service
install -D -m 0644 data/org.projectluma.homebrew.policy %{buildroot}%{_datadir}/polkit-1/actions/org.projectluma.homebrew.policy
install -D -m 0644 README.md %{buildroot}%{_docdir}/%{name}/README.md
install -d %{buildroot}%{_presetdir}
printf 'enable luma-homebrew-prefix.service\n' > %{buildroot}%{_presetdir}/60-luma-homebrew.preset

%check
sh -n %{buildroot}%{_bindir}/brew
sh -n %{buildroot}%{_libexecdir}/luma-homebrew-prefix
sh -n %{buildroot}%{_sysconfdir}/profile.d/luma-homebrew.sh

%post
%systemd_post luma-homebrew-prefix.service

%preun
%systemd_preun luma-homebrew-prefix.service

%files
%license LICENSE.md
%doc %{_docdir}/%{name}/README.md
%{_bindir}/brew
%{_libexecdir}/luma-homebrew-prefix
%config(noreplace) %{_sysconfdir}/profile.d/luma-homebrew.sh
%{_unitdir}/luma-homebrew-prefix.service
%{_presetdir}/60-luma-homebrew.preset
%{_datadir}/polkit-1/actions/org.projectluma.homebrew.policy
