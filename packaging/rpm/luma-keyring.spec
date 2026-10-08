# SPDX-License-Identifier: MPL-2.0

Name:           luma-keyring
Version:        0.1.0
Release:        1.luma.1%{?dist}
Summary:        Saved passwords that unlock when you log in, or say why they cannot
License:        MPL-2.0
URL:            https://projectluma.org/saved-passwords
Source0:        luma-keyring.tar.gz

BuildArch:      noarch
BuildRequires:  desktop-file-utils
BuildRequires:  glib2
BuildRequires:  meson >= 1.3
BuildRequires:  ninja-build
BuildRequires:  python3-devel >= 3.11
BuildRequires:  python3-gobject-base
BuildRequires:  systemd-rpm-macros
Requires:       python3 >= 3.11
Requires:       python3-gobject
Requires:       libadwaita
# The keyring this looks after, and the PAM module that unlocks it at login.
Requires:       gnome-keyring
Requires:       pam

%description
A login keyring whose password is no longer the account password cannot be
unlocked at login, so the person is asked for it at every login, forever, by a
prompt that says nothing about what is wrong or how to put it right.

Luma says what happened in plain words, once, and offers to put it right: the
person types the password the keyring was locked with and their current login
password, and the keyring is re-keyed to the login password with everything in
it kept. If they do not know the older password, Luma says plainly that nobody
can read the contents without it and offers to start a fresh keyring, keeping
the old file in case they remember later. Doing nothing keeps the old
behaviour.

luma-keyring --check prints the state for the capability and first-boot
checks: a login keyring that cannot be unlocked automatically is a broken
state worth reporting.

%prep
%autosetup -n luma-keyring

%build
%meson
%meson_build

%install
%meson_install
# Enabled by the package itself: a preset applies only on first install.
install -d %{buildroot}%{_userunitdir}/graphical-session.target.wants
ln -s ../luma-keyring.service \
  %{buildroot}%{_userunitdir}/graphical-session.target.wants/luma-keyring.service

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/org.projectluma.Keyring.desktop
# A session service, never listed as an app, with a name for its notifications.
for key in NoDisplay=true DBusActivatable=true Name=Saved\ Passwords; do
  grep -Fqx "$key" %{buildroot}%{_datadir}/applications/org.projectluma.Keyring.desktop
done
glib-compile-schemas --strict --dry-run %{buildroot}%{_datadir}/glib-2.0/schemas
# The repair asks PAM about the person's own password; the stack must be there.
grep -Fq "auth       include      system-auth" %{buildroot}%{_sysconfdir}/pam.d/luma-keyring
# No password may be written anywhere: they are arguments to a D-Bus call.
! grep -rn "log\.\(info\|warning\|error\)(.*password[^s]" %{buildroot}%{python3_sitelib}/luma_keyring/
PYTHONPATH=%{buildroot}%{python3_sitelib} %{python3} -c "import luma_keyring.state, luma_keyring.repair, luma_keyring.secrets"
cd tests && PYTHONPATH=$PWD/.. %{python3} -m unittest discover -p "test_*.py" -v

%files
%license LICENSES/MPL-2.0.txt
%{_libexecdir}/luma-keyring
%{python3_sitelib}/luma_keyring/
%{_userunitdir}/luma-keyring.service
%{_userunitdir}/graphical-session.target.wants/luma-keyring.service
%{_datadir}/applications/org.projectluma.Keyring.desktop
%{_datadir}/glib-2.0/schemas/org.projectluma.keyring.gschema.xml
%config(noreplace) %{_sysconfdir}/pam.d/luma-keyring

%changelog
* Sun Sep 20 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- Say once, in plain words, when the login keyring cannot be unlocked at login
- Re-key it to the current login password, keeping everything in it
- Offer a fresh keyring when the older password is gone, keeping the old file
- luma-keyring --check reports the state for the capability checks
