# Saved passwords on accounts that sign in automatically

Saved passwords (Wi-Fi passwords a person saves for themselves, app and
website sign-ins, Luma Connect tokens) live in the login keyring. gnome-keyring
keeps it encrypted, and `pam_gnome_keyring` unlocks it with the password typed
at login. An account that signs in automatically types no password, so the
keyring stays locked and the first thing that stores a secret asks "Unlock
keyring". On a TV there is nobody to ask.

## Policy

Every Luma account has a password, set during setup.

- **The login keyring is always locked with the account password**, as
  `pam_gnome_keyring` makes it. That password is the way in after a board
  swap, a restore onto new hardware, or anything else that stops the device
  from unlocking the keyring.
- **On an account that signs in automatically, the device keeps a sealed copy
  of that password** and unlocks the keyring with it at every sign-in. The
  copy is a user-scoped credential sealed with `systemd-creds --user`
  (`--with-key=auto`, `--tpm2-pcrs=` empty), so only that user on that
  machine can open it. Where there is a TPM2, it is bound to the TPM2 and the
  installation's host key; otherwise to the host key. It is not bound to boot
  measurements, so firmware and Secure Boot database updates do not lock it
  out.
- **Accounts that sign in with a password are unchanged.**
- "Signs in automatically" comes from the display manager: GDM
  `AutomaticLogin`/`TimedLogin` in `/etc/gdm/custom.conf`, or greetd
  `[initial_session]`. The installer, Settings and image configs all write
  those settings, so there is no second switch to drift out of step.

### Why a sealed password and not a second unlock method

gnome-keyring keeps one master password per keyring file and has no slot for
a second key. Re-keying the keyring to a device-only key would lose the
password as a recovery path. So the password-protected keyring stays the
source of truth, and the device only holds the password, as a user's own
credential.

The upstream route for automatic sign-in is to hand PAM a password:
`pam_systemd_loadkey`, or GDM's `pam_gdm` in `gdm-autologin`, reads the LUKS
passphrase that `systemd-cryptsetup` left in root's kernel keyring.
Feeding that path would need a boot service to decrypt each user's secret
into root's kernel keyring, where every root process can read it, and a
change to `gdm.service`'s keyring mode. It would also displace `pam_gdm`,
which already uses that slot for LUKS.

Luma instead unseals the copy in the user's own session and passes it to
gnome-keyring's own unlock call, `UnlockWithMasterPassword`, the same
unlock `pam_gnome_keyring` performs. This changes no PAM stack and no
display manager. A keyring that `pam_gdm` has already unlocked with a LUKS
passphrase is left alone.

### Where the password is sealed

The password is sealed only at moments when it is already in hand.

- **Installer.** When "Sign me in automatically" is chosen, the Account step
  hands the password to the kickstart's `%post`, through the installer's
  memory only (`/run/luma-atlas/keyring-enrol`, mode 0600). The `%post` runs
  `luma-keyring --enrol USER` in the installed system and removes the file
  either way. The installed system's own keyring daemon makes the login
  keyring with the password at the first sign-in, exactly as
  `pam_gnome_keyring` would at a first password login. A new install never
  asks again.
- **Existing devices, and automatic sign-in turned on later in Settings.**
  The session service asks once: "Let your saved passwords unlock by
  themselves". The person types their password, the keyring confirms it, and
  the copy is sealed. Over SSH, `/usr/libexec/luma-keyring --enable-auto-unlock`
  reads it from standard input inside the person's session.
- **The sealed copy stops working** (new hardware, cleared TPM, a changed
  password). The same one-time question comes back, and the typed password
  is sealed again. If the old keyring password is gone, "Start a new keyring"
  makes one with the current password and keeps the old file aside.
- **Automatic sign-in turned off.** PAM unlocks the keyring at a password
  login again, and the sealed copy is deleted.

`luma-keyring-autologin.service` (a user unit that runs before
`graphical-session-pre.target`) runs before anything in the session can ask
for a secret:

| Automatic sign-in | Sealed password | Login keyring | Action |
| --- | --- | --- | --- |
| yes | present | none | make it with the password |
| yes | present | locked | unlock it with the password |
| yes | present, does not open it | locked | leave it; ask once |
| yes | none | none or locked | leave it; ask once |
| yes | none | empty password | open it (no prompt); offer to lock it with the password |
| no | present | any | unlock if needed, delete the sealed copy |
| no | none | any | nothing |

### No empty passwords, except when nothing can be sealed

GNOME's long-standing advice for automatic login is a login keyring with an
empty password, which stores it unencrypted. Luma does not do that.
The only exception is a device where `systemd-creds` cannot seal anything,
for example with no persistent `/var/lib/systemd`. Even then it happens only
after the person has typed their password in the one-time question, and the
window says the keyring is now stored without encryption.

Device-wide secrets such as a TV's Wi-Fi belong to the system
(NetworkManager system connections), not to anyone's keyring.

## What the device seal protects

With automatic sign-in, the device unlocks saved passwords, as on other TVs
and set-top boxes. Anyone who can use the signed-in account can use them, the
same as its files. The seal decides who else can get at them.

- **With a TPM2:** the sealed copy opens only on this device's TPM2 with this
  installation's host key. A copied home folder or keyring, a removed disk,
  or a backup restored elsewhere gets nothing without the account password.
- **Without a TPM2 (for example a Raspberry Pi):** the sealed copy is bound
  to the host key in `/var/lib/systemd/credential.secret`. The seal protects
  a keyring and credential copied on their own, such as a home-folder backup,
  a synced folder or a single stolen file. **It does not protect against
  someone holding the whole disk or root on the device.** They hold the host
  key too, so they can recover the account password and the keyring. Only
  disk encryption protects against that.
- What is sealed is the account password itself. Anyone able to unseal it
  learns a password the person may use elsewhere, so a device without a TPM2
  and without disk encryption should not share its password with other
  accounts.

Someone who wants saved passwords to be protected by a typed password
should not turn on automatic sign-in.

## References

- systemd-creds(1): user-scoped credentials (systemd 256), `--with-key=auto`
  and `--uid=`:
  https://www.freedesktop.org/software/systemd/man/latest/systemd-creds.html
- pam_systemd_loadkey(8): the upstream route for a LUKS passphrase at
  automatic login:
  https://www.freedesktop.org/software/systemd/man/latest/pam_systemd_loadkey.html
- pam_gdm passing the LUKS passphrase at automatic login, and how it fails:
  https://bugzilla.gnome.org/show_bug.cgi?id=769950,
  https://bugzilla.redhat.com/show_bug.cgi?id=2150649
- GNOME Keyring and automatic login (the empty-password advice):
  https://wiki.archlinux.org/title/GNOME/Keyring
- gkr-pam and empty keyring passwords:
  https://gitlab.gnome.org/GNOME/gnome-keyring/-/issues/129
