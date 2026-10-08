# SPDX-License-Identifier: MPL-2.0
Name:           luma-install-commands
Version:        0.1.0
Release:        1.luma.6%{?dist}
Summary:        dnf, yum, apt and Flathub on Luma, without hurdles
License:        MPL-2.0
URL:            https://projectluma.org
BuildArch:      noarch
Source0:        %{name}.tar.gz

BuildRequires:  python3-devel
BuildRequires:  systemd-rpm-macros
Requires:       python3
Requires:       dnf5
Requires:       dnf5-plugins
Requires:       rpm-ostree
Requires:       util-linux-core
Requires:       polkit
Requires:       flatpak
Requires:       snapd
%{?systemd_requires}

%description
Guides say "sudo dnf install htop", "sudo apt install firefox" and
"flatpak install flathub org.mozilla.firefox". On Luma each of those works the
first time it is typed.

dnf and yum keep dnf5's resolution, table and questions, and make the change
the way an image-based system can: added packages go into this computer's
system image with rpm-ostree, are applied to the running system at once, and
stay through Luma updates and rollbacks. Launchers of added apps appear in the
running session straight away. "dnf upgrade" shows Luma's update and prepares
it only when the person says yes; nothing restarts by itself. Read-only
commands are dnf5 unchanged, dnf5 itself stays reachable, and toolbox and
distrobox containers keep their own dnf.

apt and apt-get translate Debian commands and package names to dnf and say
so in one line. Flathub is configured as an unfiltered system remote.
Every change is recorded in the journal for Luma Vitals.

%prep
%setup -q -n %{name}

%build

%install
install -d %{buildroot}%{python3_sitelib}/luma_install_commands
install -m 0644 luma_install_commands/*.py %{buildroot}%{python3_sitelib}/luma_install_commands/
install -D -m 0755 bin/dnf %{buildroot}%{_libexecdir}/%{name}/dnf
install -D -m 0755 bin/rpm %{buildroot}%{_libexecdir}/%{name}/rpm
install -D -m 0644 data/luma-rpm-command.conf %{buildroot}%{_tmpfilesdir}/luma-rpm-command.conf
install -D -m 0755 bin/luma-remove-added-package %{buildroot}%{_libexecdir}/%{name}/luma-remove-added-package
install -D -m 0755 bin/apt %{buildroot}%{_bindir}/apt
ln -s apt %{buildroot}%{_bindir}/apt-get
install -D -m 0755 data/61-luma-live-exports %{buildroot}%{_prefix}/lib/systemd/user-environment-generators/61-luma-live-exports
install -D -m 0644 data/luma-live-exports.conf %{buildroot}%{_tmpfilesdir}/luma-live-exports.conf
install -D -m 0644 data/org.projectluma.install-commands.policy %{buildroot}%{_datadir}/polkit-1/actions/org.projectluma.install-commands.policy
install -D -m 0644 data/luma-install-commands.catalog %{buildroot}%{_prefix}/lib/systemd/catalog/luma-install-commands.catalog
install -D -m 0644 data/flathub.flatpakrepo %{buildroot}%{_datadir}/flatpak/remotes.d/flathub.flatpakrepo
# dnf copr picks a project's chroot from os-release, which names Luma; Luma's
# packages come from Fedora %{fedora}, so COPR projects resolve as Fedora's.
install -d %{buildroot}%{_datadir}/dnf/plugins
printf '# SPDX-License-Identifier: MPL-2.0\n# Luma is built on Fedora %{fedora}: dnf copr enable uses Fedora chroots (ADR-038).\n[main]\ndistribution = fedora\nreleasever = %{fedora}\n' \
  > %{buildroot}%{_datadir}/dnf/plugins/copr.vendor.conf
# Snap seeds at boot, so the first snap command never meets an unseeded snapd.
install -D -m 0644 data/60-luma-snapd-seeded.preset %{buildroot}%{_presetdir}/60-luma-snapd-seeded.preset
install -D -m 0644 README.md %{buildroot}%{_docdir}/%{name}/README.md
install -D -m 0644 038-no-install-hurdles.md %{buildroot}%{_docdir}/%{name}/038-no-install-hurdles.md
%py_byte_compile %{python3} %{buildroot}%{python3_sitelib}/luma_install_commands

%check
python3 -m unittest discover -s tests -p 'test_*.py'
sh -n %{buildroot}%{_prefix}/lib/systemd/user-environment-generators/61-luma-live-exports
sh -n %{buildroot}%{_libexecdir}/%{name}/rpm
grep -qx 'L /var/usrlocal/bin/rpm - - - - /usr/libexec/luma-install-commands/rpm' %{buildroot}%{_tmpfilesdir}/luma-rpm-command.conf
test "$(XDG_DATA_DIRS=/usr/share %{buildroot}%{_prefix}/lib/systemd/user-environment-generators/61-luma-live-exports)" = \
  "XDG_DATA_DIRS=/run/luma/live-exports/share:/usr/share"
grep -q '^-- a08437fa35fc4db9a55f26f08c3e9847$' %{buildroot}%{_prefix}/lib/systemd/catalog/luma-install-commands.catalog
grep -qx 'releasever = %{fedora}' %{buildroot}%{_datadir}/dnf/plugins/copr.vendor.conf
grep -q '^Url=https://dl.flathub.org/repo/$' %{buildroot}%{_datadir}/flatpak/remotes.d/flathub.flatpakrepo
! grep -q '^Filter=' %{buildroot}%{_datadir}/flatpak/remotes.d/flathub.flatpakrepo
PYTHONPATH=%{buildroot}%{python3_sitelib} python3 -c 'import luma_install_commands.dnf, luma_install_commands.apt'

# dnf5 owns /usr/bin/dnf and /usr/bin/yum as links to itself. On Luma they
# lead to this front end instead; dnf5 stays at /usr/bin/dnf5. A later dnf5
# update rewrites the links and the trigger points them back; removing this
# package restores dnf5's own links.
%post
%systemd_post snapd.seeded.service
%tmpfiles_create luma-rpm-command.conf

%posttrans
for cmd in dnf yum; do
  ln -sfn ../libexec/%{name}/dnf %{_bindir}/$cmd
done
%journal_catalog_update

%triggerin -- dnf5
for cmd in dnf yum; do
  ln -sfn ../libexec/%{name}/dnf %{_bindir}/$cmd
done

%postun
if [ "$1" -eq 0 ] && [ -x %{_bindir}/dnf5 ]; then
  for cmd in dnf yum; do
    ln -sfn dnf5 %{_bindir}/$cmd
  done
fi

%files
%license LICENSES/MPL-2.0.txt
%doc %{_docdir}/%{name}/README.md
%doc %{_docdir}/%{name}/038-no-install-hurdles.md
%{python3_sitelib}/luma_install_commands/
%dir %{_libexecdir}/%{name}
%{_libexecdir}/%{name}/dnf
%{_libexecdir}/%{name}/luma-remove-added-package
%{_libexecdir}/%{name}/rpm
%{_tmpfilesdir}/luma-rpm-command.conf
%{_bindir}/apt
%{_bindir}/apt-get
%{_prefix}/lib/systemd/user-environment-generators/61-luma-live-exports
%{_tmpfilesdir}/luma-live-exports.conf
%{_datadir}/polkit-1/actions/org.projectluma.install-commands.policy
%{_prefix}/lib/systemd/catalog/luma-install-commands.catalog
%dir %{_datadir}/flatpak/remotes.d
%{_datadir}/flatpak/remotes.d/flathub.flatpakrepo
%dir %{_datadir}/dnf
%dir %{_datadir}/dnf/plugins
%{_datadir}/dnf/plugins/copr.vendor.conf
%{_presetdir}/60-luma-snapd-seeded.preset

%changelog
* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.6
- A package name typed where the command goes ("apt-get Vivaldi", "apt
  Vivaldi", "dnf vivaldi", with no install/get verb at all) is understood as
  install, with one line saying so, the same way apt's "get" already is.
- A truly unrecognized word says plainly that it isn't a Luma command, and
  suggests apt install and Depot, instead of pointing at dnf --help.

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.5
- apt, apt-get and dnf resolve a name Fedora doesn't package (a browser like
  Vivaldi, say) against Depot's own application catalogue and install it the
  way Depot would -- usually a Flatpak from Flathub, needing no restart --
  instead of just saying the name wasn't found.
- "apt get PACKAGE" (apt-get's own verb, typed after apt) is understood as
  apt install, said once, plainly.
- Package names are matched without regard to case.
- More than one catalogue match for a name is a short numbered list, never a
  silent failure or a silent pick.

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4
- Luma's own packages (/usr/share/luma/luma-owned-packages.txt) are never
  replaced through dnf, yum, apt or rpm: installs, upgrades, downgrades,
  reinstalls and swaps that would replace one from any other repository or
  file are refused with one line; a missing list warns and protects nothing.
- rpm -i, -U and -F of package files install into the system image live,
  through dnf; other rpm use is unchanged. Unsupported rpm options explain
  that /usr is read-only and that /opt and /usr/local are writable and kept.
- A newer package file replaces the one added earlier from a file.

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.3
- dnf upgrade no longer calls the staged deployment of a live install an
  update ready to install.

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- First release (ADR-038): sudo dnf install adds packages to the system image
  and applies them live; dnf remove, upgrade (through luma-update, prompting),
  yum; apt and apt-get translating to dnf with Debian package names; launchers
  of added apps appear at once; Flathub as an unfiltered system remote; every
  change journaled for Luma Vitals; Depot removes added tools through polkit;
  dnf copr enable resolves Fedora chroots; snapd seeds at boot.
