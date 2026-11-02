# Saved passwords on accounts that sign in automatically

Saved passwords (Wi-Fi passwords a person saves for themselves, app and
website sign-ins, Luma Connect tokens) live in the login keyring, which
gnome-keyring keeps encrypted and `pam_gnome_keyring` unlocks with the
password typed at login. An account that signs in automatically -- a TV, a
Raspberry Pi, any install where the owner turned on automatic sign-in --
types no password, so the keyring stays locked and the first thing that
stores a secret asks "Unlock keyring". On a TV there is nobody to ask.

## Policy

One mechanism for every device class, in `luma-keyring`:

- **Accounts that sign in with a password are unchanged.** Their keyring
  follows the login password through PAM, as before.
- **Accounts that sign in automatically** get a login keyring keyed to the
  device: a random 256-bit key sealed with `systemd-creds --user` as a
  credential only that user on that machine can open. `--with-key=auto`
  binds it to the TPM2 and the installation's host key where there is a TPM2,
  and to the host key otherwise. It is not bound to boot measurements
  (`--tpm2-pcrs=` empty), so firmware and Secure Boot database updates do
  not lock it out. Where no credential can be sealed at all, the keyring gets
  an empty password, as GNOME documents for automatic sign-in.
- "Signs in automatically" is read from the display manager: GDM
  `AutomaticLogin`/`TimedLogin` in `/etc/gdm/custom.conf`, greetd
  `[initial_session]`. The installer, Settings and image configs all write
  those, so no separate switch exists to drift.

`luma-keyring-autologin.service` (user unit, before
`graphical-session-pre.target`) runs at every session start, before anything
in the session can ask for a secret:

| Login keyring | Sealed key | Automatic sign-in | Action |
| --- | --- | --- | --- |
| none | -- | yes | create it, keyed to a new sealed key |
| empty password | none | yes | key it to a new sealed key (no password needed) |
| locked | present | either | open it with the sealed key |
| locked with a password | none | yes | leave it; offer the switch once |
| any | none | no | nothing |

A keyring that already has a password is never re-keyed without that
password. The session service (`luma-keyring.service`) offers the switch
once in a notification; the window asks for the keyring's current password
and re-keys it to the device key with everything in it kept. Over SSH:
`/usr/libexec/luma-keyring --switch-to-device-key` reads the password from
standard input inside the person's session. If the password is gone, "Start
a new keyring" keeps the old file aside.

When automatic sign-in is turned off again, the sealed key keeps opening the
keyring (nothing breaks), and the session service offers once to lock it with
the login password instead, which removes the sealed key.

Device-wide secrets such as a TV's Wi-Fi belong to the system
(NetworkManager system connections), not to anyone's keyring.

## Security trade-off

With automatic sign-in, saved passwords are protected by the device, not by
a password, as on other TVs and set-top boxes:

- Anyone who can use the signed-in account can use its saved passwords,
  exactly as they can use its files and open sessions.
- At rest, the keyring file is encrypted with the sealed key. Copied off
  the device on its own (a backup of the home folder, a synced folder) it
  cannot be opened. With a TPM2 it cannot be opened off the device's chip at
  all; without one, someone holding the whole disk holds the host key too,
  so disk encryption is what protects it.
- A cleared TPM, a reinstall that keeps the home folder, or a lost
  `/var/lib/systemd/credential.secret` makes the sealed key unusable. The
  keyring then reports locked, says so plainly, and offers a fresh keyring;
  its old contents are out of reach.

Someone who wants saved passwords behind a password should sign in with one.

## References

- systemd-creds(1), user-scoped credentials and `--with-key=auto`:
  https://www.freedesktop.org/software/systemd/man/latest/systemd-creds.html
- pam_systemd_loadkey(8), the upstream route for a LUKS passphrase typed at
  boot (GDM's `pam_gdm` does the same in `gdm-autologin`; a keyring already
  unlocked that way is left alone):
  https://www.freedesktop.org/software/systemd/man/latest/pam_systemd_loadkey.html
- GNOME Keyring and automatic login (empty login keyring password, stored
  unencrypted): https://wiki.archlinux.org/title/GNOME/Keyring
- pam_gdm passing the LUKS passphrase at automatic login:
  https://bugzilla.gnome.org/show_bug.cgi?id=769950,
  https://bugzilla.redhat.com/show_bug.cgi?id=2150649
- gkr-pam and empty keyring passwords:
  https://gitlab.gnome.org/GNOME/gnome-keyring/-/issues/129
