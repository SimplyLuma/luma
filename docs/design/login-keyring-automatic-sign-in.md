# Saved passwords on accounts that sign in automatically

Saved passwords live in the login keyring: Wi-Fi passwords a person saves for
themselves, app and website sign-ins, Luma Connect tokens. gnome-keyring keeps
the keyring encrypted, and `pam_gnome_keyring` unlocks it with the password
typed at login.

An account that signs in automatically types no password, so the keyring
stays locked. The first thing that stores a secret then asks "Unlock
keyring". On a TV there is nobody to ask.

The mechanism lives entirely in `luma-keyring`. gnome-keyring, its file
formats, PAM stacks and the display manager are unchanged.

## Design: envelope encryption with two key slots

This follows the pattern of LUKS2 keyslots and of FileVault and BitLocker
with a TPM protector plus a recovery key. One random data key is wrapped
once per unlock method, and any one slot opens it.

- **Data key.** Each automatic-sign-in account gets a keyring key: 256 bits
  from the OS CSPRNG (`secrets.token_bytes`), base64-encoded. The login
  keyring is locked with it through gnome-keyring's own master-password
  interface, so the keyring file stays in gnome-keyring's encrypted format.
- **Slot 1, the device seal (automatic unlock).**
  - The key is sealed with `systemd-creds --user --with-key=auto --tpm2-pcrs=`
    as a user-scoped credential (`login.cred`). Only that user on that
    machine can open it.
  - Where there is a TPM2, the seal is bound to the TPM2 and the
    installation's host key; otherwise to the host key alone.
  - There is no PCR policy, so firmware and Secure Boot database updates do
    not lock it out.
  - At the start of each session, `luma-keyring-autologin.service` (a user
    unit before `graphical-session-pre.target`) unseals the key. It then
    unlocks the keyring with gnome-keyring's `UnlockWithMasterPassword`, the
    same unlock `pam_gnome_keyring` performs.
- **Slot 2, the password wrap (recovery).**
  - The key is encrypted with AES-256-GCM under a key derived from the
    account password with Argon2id. The result is stored in `login.wrap`.
  - Argon2id uses RFC 9106's second recommended profile: 64 MiB, t=3, p=4.
    That is above the OWASP Password Storage Cheat Sheet minimum and takes
    about 0.25 s on a Raspberry Pi 5.
  - The salt is 16 random bytes and the GCM nonce 12 random bytes, both fresh
    on every wrap.
  - Both primitives come from python-cryptography, which wraps OpenSSL and
    is in Fedora. There is no custom construction and no unauthenticated
    mode.
  - The file is versioned JSON: `magic`, `version`, `kdf`, `kdf_params`,
    `aead`, `salt`, `nonce`, `ciphertext`, and `password_changed`. The KDF
    parameters are stored so they can be raised later. The magic and version
    are bound into the GCM associated data, so the header cannot be swapped.
- **The account password is never stored** in any form that can be turned
  back into it. Opening the seal yields the keyring key, not a password the
  person may use elsewhere.

### Why not the alternatives

- **A second unlock method inside gnome-keyring:** it holds one master
  password per keyring, so the slots live outside it.
- **A sealed copy of the account password:** opening the seal would then
  reveal that password. On a device without a TPM2, root or the whole disk
  is enough to open the seal.
- **An empty keyring password**, GNOME's long-standing autologin advice,
  stores the keyring unencrypted. It is used only where `systemd-creds`
  cannot seal anything at all, after the person types their password, and
  the window says so.
- **The PAM slot** (`pam_systemd_loadkey`, or GDM's `pam_gdm` in
  `gdm-autologin`): these read a LUKS passphrase from root's kernel keyring.
  Feeding them would put a per-user secret where every root process can read
  it, need a change to `gdm.service`, and displace `pam_gdm`'s existing LUKS
  use. A keyring that `pam_gdm` already unlocked is left alone.

## Enrolment

Each account is enrolled at a point where its password is already in hand.

- **Installer.** When "Sign me in automatically" is chosen, the Atlas
  Account step hands the password to the kickstart `%post`:
  - It travels through the installer's memory only, in
    `/run/luma-atlas/keyring-enrol`. The file is written from stdin, is
    root-only from its first byte, and is never on a command line.
  - The `%post` runs `luma-keyring --enrol USER` inside the installed system,
    which generates, seals and wraps the key.
  - The intent file is shredded either way.
  - At the first sign-in, the session's keyring daemon makes the login
    keyring with the key.
  - New installs are never prompted.
- **Devices set up earlier, or automatic sign-in turned on in Settings.** A
  one-time notification asks for the password.
  - The password is checked with PAM, and the keyring is re-keyed onto a new
    key that is sealed and wrapped. Its contents are kept.
  - The new seal and wrap are written before the keyring is re-keyed, and
    moved into place only after it succeeds. The keyring is never left on a
    key nothing holds.
  - Over SSH: `luma-keyring --enable-auto-unlock`, with the password on
    stdin, inside the person's session.

## Storage hygiene

- Both files live in the user's private state directory,
  `$XDG_STATE_HOME/luma-keyring` (0700). The files are 0600 and owned by the
  user.
- Every write goes to a temporary file, is fsynced, then renamed into place.
- Secrets reach `systemd-creds` and the CLI on stdin only, never in argv or
  the environment. They are never logged.
- Python cannot reliably wipe its own strings, so key material is held only
  for the length of one call.

## Failure modes

| Situation | What happens |
| --- | --- |
| Seal unusable (new board, cleared TPM, lost host key) or corrupt | Keyring left intact and locked; one notification asks for the password, which unwraps the key; the key is sealed again |
| Wrap corrupt or missing | Keyring left intact; the person is told plainly; "Start a new keyring" keeps the old file aside |
| Password changed | Each wrap records when the password last changed (AccountsService, to the day). After a change, one notification asks for the new password; the key, unsealed on the device, is wrapped again. `luma-keyring --rewrap` (old and new password on stdin) does it directly |
| Automatic sign-in turned off | The seal keeps unlocking (nothing breaks); one notification offers to move the keyring back onto the login password, which deletes both slots |
| Wrong password at any prompt | Nothing changes |

No path deletes a keyring. Starting again moves the old file aside with the
date in its name.

## Threat model

Automatic sign-in trades user presence for convenience. Whoever can use the
signed-in device can use the account's saved passwords, the same as its files
and open sessions. This is the model of TVs and set-top boxes. Someone who
wants a typed password to stand between the device and their secrets should
not turn on automatic sign-in.

**With a TPM2:**
- Protected: a copied home folder or keyring, a removed disk, or a backup
  restored on other hardware. None of these opens the keyring without the
  account password.
- Offline guessing against `login.wrap` costs one Argon2id evaluation
  (64 MiB) per guess.

**Without a TPM2 (for example a Raspberry Pi 5):**
- Protected: the keyring, credential or wrap copied on their own, such as a
  home-folder backup, a synced folder or a single stolen file.
- **Not protected: someone holding root on the device or the whole disk.**
  They hold the host key in `/var/lib/systemd/credential.secret` too, so
  they can unseal the keyring key and read the saved passwords. Only disk
  encryption covers that case.
- They do not learn the account password. It is not stored, and the wrap
  costs Argon2id per guess.

**Never protected:** a running, signed-in session, and root on a running
device.

## References

- LUKS2 on-disk format (keyslots wrapping one volume key):
  https://gitlab.com/cryptsetup/LUKS2-docs
- Apple Platform Security, FileVault (volume key, recovery key):
  https://support.apple.com/guide/security/volume-encryption-with-filevault-sec4c6dc1b6e/web
- BitLocker key protectors (TPM plus recovery password):
  https://learn.microsoft.com/windows/security/operating-system-security/data-protection/bitlocker/
- RFC 9106, Argon2 (section 4, parameter choice):
  https://www.rfc-editor.org/rfc/rfc9106
- OWASP Password Storage Cheat Sheet (Argon2id parameters):
  https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- NIST SP 800-38D, GCM: https://csrc.nist.gov/pubs/sp/800/38/d/final
- python-cryptography, Argon2id and AESGCM:
  https://cryptography.io/en/latest/hazmat/primitives/key-derivation-functions/#argon2id
- systemd-creds(1), user-scoped credentials, `--with-key=auto`,
  `--tpm2-pcrs=`:
  https://www.freedesktop.org/software/systemd/man/latest/systemd-creds.html
- XDG Base Directory Specification (`XDG_STATE_HOME`):
  https://specifications.freedesktop.org/basedir-spec/latest/
- pam_systemd_loadkey(8):
  https://www.freedesktop.org/software/systemd/man/latest/pam_systemd_loadkey.html
- pam_gdm and the LUKS passphrase at automatic login:
  https://bugzilla.gnome.org/show_bug.cgi?id=769950,
  https://bugzilla.redhat.com/show_bug.cgi?id=2150649
- GNOME Keyring and automatic login (the empty-password advice):
  https://wiki.archlinux.org/title/GNOME/Keyring
