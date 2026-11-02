# SPDX-License-Identifier: MPL-2.0

Name:           luma-keyring
Version:        0.2.0
Release:        1.luma.1%{?dist}
Summary:        Saved passwords that unlock when you log in, or say why they cannot
License:        MPL-2.0
URL:            https://projectluma.org/saved-passwords
Source0:        luma-keyring.tar.gz

BuildRequires:  binutils
BuildRequires:  desktop-file-utils
BuildRequires:  gcc
BuildRequires:  pkgconfig(pam)
BuildRequires:  pkgconfig(libxcrypt)
BuildRequires:  glib2
BuildRequires:  meson >= 1.3
BuildRequires:  ninja-build
BuildRequires:  python3-devel >= 3.11
BuildRequires:  python3-gobject-base
BuildRequires:  python3-cryptography >= 44
BuildRequires:  systemd-rpm-macros
Requires:       python3 >= 3.11
Requires:       python3-gobject
Requires:       libadwaita
# The keyring this looks after, and the PAM module that unlocks it at login.
Requires:       gnome-keyring
Requires:       pam
# Seals the keyring key of an account that signs in automatically, and wraps
# it with Argon2id and AES-256-GCM for recovery.
Requires:       systemd >= 256
Requires:       python3-cryptography >= 44
# pwscore, to say when the password guarding the recovery copy is weak.
Requires:       libpwquality
# systemd also carries userdbctl and systemd-userdbd: the session compares
# the account's own password hash with the one the recovery copy was made for.
# /etc/pam.d/passwd, where %%post adds the password-change hook. The file
# belongs to passwd (shadow-utils provides passwd from Fedora 42); both are
# triggers below, so an update that replaces it gets the hook back.
Requires(post): passwd
Requires(post): coreutils gawk grep
Requires(preun): coreutils grep

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

An account that signs in automatically types no password, so nothing could
unlock its login keyring. Its keyring is locked with a random keyring key in
two slots: sealed with systemd-creds for the user and the machine (TPM2 and
host key where there is a TPM2, PCR 7 with an encrypted root; host key
otherwise), and wrapped with Argon2id and AES-256-GCM under the account
password for recovery. The password itself is never stored. The installer
enrols the account when automatic sign-in is chosen; pam_luma_keyring.so in
the passwd stack re-wraps the key when the password change really happened
(the new password verifies against /etc/shadow); devices set up earlier are
asked for the password once.

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
install -d %{buildroot}%{_userunitdir}/graphical-session-pre.target.wants
ln -s ../luma-keyring-autologin.service \
  %{buildroot}%{_userunitdir}/graphical-session-pre.target.wants/luma-keyring-autologin.service

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
PYTHONPATH=%{buildroot}%{python3_sitelib} %{python3} -c "import luma_keyring.state, luma_keyring.repair, luma_keyring.secrets, luma_keyring.autounlock"
# The PAM module exports pam_sm_chauthtok and nothing else.
test "$(nm -D --defined-only %{buildroot}%{_libdir}/security/pam_luma_keyring.so | awk '{print $3}')" = pam_sm_chauthtok
grep -Fq %{_libexecdir}/luma-keyring %{buildroot}%{_libdir}/security/pam_luma_keyring.so
cd tests && PYTHONPATH=$PWD/.. %{python3} -m unittest discover -p "test_*.py" -v

%post
%{_libexecdir}/luma-keyring-pam-setup add || :

%triggerin -- passwd, shadow-utils
# Whichever owns /etc/pam.d/passwd may replace it on update.
%{_libexecdir}/luma-keyring-pam-setup add || :

%preun
if [ "$1" -eq 0 ]; then
    %{_libexecdir}/luma-keyring-pam-setup remove || :
fi

%files
%license LICENSES/MPL-2.0.txt
%{_libexecdir}/luma-keyring
%{_libexecdir}/luma-keyring-pam-setup
%{_libdir}/security/pam_luma_keyring.so
%{_datadir}/luma-keyring/
%{python3_sitelib}/luma_keyring/
%{_userunitdir}/luma-keyring.service
%{_userunitdir}/graphical-session.target.wants/luma-keyring.service
%{_userunitdir}/luma-keyring-autologin.service
%{_userunitdir}/graphical-session-pre.target.wants/luma-keyring-autologin.service
%{_datadir}/applications/org.projectluma.Keyring.desktop
%{_datadir}/glib-2.0/schemas/org.projectluma.keyring.gschema.xml
%config(noreplace) %{_sysconfdir}/pam.d/luma-keyring

%changelog
* Sun Nov 01 2026 Project Luma <maintainers@projectluma.org> - 0.2.0-1.luma.1
- Lock the login keyring of an automatic sign-in with a random key, sealed for
  the device and wrapped with the account password for recovery
- Crash-safe key changes; fail closed when nothing can be sealed
- luma-keyring --enrol for the installer; ask once on devices set up earlier
- Re-wrap at password changes with pam_luma_keyring.so, only once the new
  password verifies against /etc/shadow; keep the previous wrap until the
  session confirms the change
- Standalone recovery script for older systems, kept next to the slots
- PCR 7 only when the root file system itself is encrypted

* Sun Sep 20 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- Say once, in plain words, when the login keyring cannot be unlocked at login
- Re-key it to the current login password, keeping everything in it
- Offer a fresh keyring when the older password is gone, keeping the old file
- luma-keyring --check reports the state for the capability checks
