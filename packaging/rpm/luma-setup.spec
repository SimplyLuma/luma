# SPDX-License-Identifier: Apache-2.0

Name:           luma-setup
Version:        0.1.0
Release:        1.luma.1%{?dist}
Summary:        Set up this Luma: language, network, your account and the device's name
License:        Apache-2.0
URL:            https://projectluma.org/setup
Source0:        luma-setup.tar.gz
BuildArch:      noarch

BuildRequires:  dconf
BuildRequires:  desktop-file-utils
BuildRequires:  meson >= 1.3
BuildRequires:  ninja-build
BuildRequires:  python3-devel >= 3.11
BuildRequires:  systemd-rpm-macros
Requires:       python3 >= 3.11
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
# LumaUI (luma_appkit), and its TV layer where the device is a TV.
Requires:       luma-developer-platform
# GDM runs the setup in its initial-setup session; it signs the new account in.
Requires:       gdm
Requires:       gnome-session
Requires:       gnome-shell
Requires:       polkit
# The system services Finish applies through, and what the pages read.
Requires:       accountsservice
Requires:       systemd
Requires:       shadow-utils
Requires:       NetworkManager-libnm
Requires:       gnome-desktop4
Requires:       libgweather4
Requires:       geoclue2-libs
# Saved passwords unlock with the account password from the first sign-in.
Requires:       luma-keyring >= 0.2.0
# GNOME Initial Setup never runs on Luma: this package owns its session.
Obsoletes:      gnome-initial-setup < 999
Conflicts:      gnome-initial-setup

%description
Luma's first-boot setup. A device with no account yet (a flashed card, a
phone, a TV box, a computer after the installer) boots into one sheet over
the softened wallpaper: Hello (language and keyboard), Get online (only
without a cable), About you (name, username, password; the owner, an
administrator) and Name this Luma (device name and time zone, automatic once
online). Finish creates the account, sets the locale, keyboard, time zone and
host names, enrols the saved passwords with the same password, and signs the
person straight in. Desktop, phone and TV are presentations of the same
pages. GNOME Initial Setup is replaced.

%prep
%autosetup -n luma-setup

%build
%meson
%meson_build

%install
%meson_install

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/org.projectluma.Setup.desktop
desktop-file-validate %{buildroot}%{_datadir}/gdm/greeter/wayland-sessions/gnome-initial-setup.desktop
test -s %{buildroot}%{_datadir}/luma-setup/dconf-defaults
grep -Fqx "Requires=luma-setup.service" \
  %{buildroot}%{_userunitdir}/gnome-session@gnome-initial-setup.target.d/luma-setup.conf
# No password is ever logged or put on a command line.
! grep -rnE "(log\.(info|warning|error)|print)\(.*password" %{buildroot}%{python3_sitelib}/luma_setup/
cd tests && PYTHONPATH=$PWD/.. LUMA_SETUP_FIXTURE=1 %{python3} -m unittest discover -p "test_*.py" -v

%files
%license LICENSES/Apache-2.0.txt
%{_libexecdir}/luma-setup
%{_libexecdir}/luma-setup-apply
%{python3_sitelib}/luma_setup/
%{_datadir}/gnome-session/sessions/gnome-initial-setup.session
%{_datadir}/gdm/greeter/wayland-sessions/gnome-initial-setup.desktop
%{_userunitdir}/gnome-session@gnome-initial-setup.target.d/luma-setup.conf
%{_userunitdir}/luma-setup.service
%{_datadir}/polkit-1/actions/org.projectluma.setup.policy
%{_datadir}/polkit-1/rules.d/20-luma-setup.rules
%{_sysusersdir}/luma-setup.conf
%{_datadir}/dconf/profile/gnome-initial-setup
%{_datadir}/luma-setup/
%config(noreplace) %{_sysconfdir}/geoclue/conf.d/90-luma-setup.conf
%{_datadir}/applications/org.projectluma.Setup.desktop

%changelog
* Tue Nov 03 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- First-boot setup in GDM's initial-setup session: language and keyboard,
  Wi-Fi, the account, the device name and time zone, then a direct sign-in
- Desktop, phone and TV presentations of the same pages
- Replaces GNOME Initial Setup
