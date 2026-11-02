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
  - The key is sealed with `systemd-creds --user --with-key=auto` as a
    user-scoped credential (`login.cred`). Only that user on that machine
    can open it.
  - Where there is a TPM2, the seal is bound to the TPM2 and the
    installation's host key; otherwise to the host key alone.
  - Where the system disk is encrypted (a volume is listed in
    `/etc/crypttab`), the seal is also bound to PCR 7, the Secure Boot state
    (`--tpm2-pcrs=7`). It then opens only under the same Secure Boot chain,
    and a Secure Boot database update needs the password once. Without
    full-disk encryption no PCR policy is used; the threat model below says
    what that means.
  - At the start of each session, `luma-keyring-autologin.service` (a user
    unit before `graphical-session-pre.target`) unseals the key. It then
    unlocks the keyring with gnome-keyring's `UnlockWithMasterPassword`, the
    same unlock `pam_gnome_keyring` performs. The unit is bounded to a few
    seconds and never fails the session.
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
  - The file is versioned JSON: `magic`, `version` (2), `kdf`, `kdf_params`,
    `aead`, `salt`, `nonce`, `password_changed`, `wrapped_at`, and
    `ciphertext`.
  - Every header field, canonically encoded, is the GCM associated data, so
    no field can be changed without the password.
  - `kdf_params` and the salt length are bounds-checked before any work
    (memory 19 MiB to 1 GiB, t 2 to 16, p 1 to 16). A tampered header can
    neither weaken the derivation nor exhaust the device.
- **The account password is never stored** in any form that can be turned
  back into it. Opening the seal yields the keyring key, not a password the
  person may use elsewhere.

### Crash consistency

Every change of key writes the new wrap and seal as `login.wrap.new` and
`login.cred.new` (each fsynced, then the directory), moves the keyring, and
then promotes the wrap before the seal (rename, then fsync the directory).
`ensure` tries `login.cred` and `login.cred.new` and promotes whichever opens
the keyring (a `*.new` left by a key change that never reached the keyring is
discarded); `recover` tries `login.wrap` and `login.wrap.new`, and falls back
to the password itself when the keyring never moved. So at every instant a
slot on disk opens the keyring. The unit tests kill the enrolment after each
step and check this.

### Why not the alternatives

- **A second unlock method inside gnome-keyring:** it holds one master
  password per keyring, so the slots live outside it.
- **A sealed copy of the account password:** opening the seal would then
  reveal that password. On a device without a TPM2, root or the whole disk
  is enough to open the seal.
- **An empty keyring password**, GNOME's long-standing autologin advice,
  stores the keyring unencrypted. Luma fails closed instead: if sealing
  fails, nothing changes, the keyring stays on the password, and Luma asks
  again later. Plain text is only an explicit opt-in
  (`luma-keyring --enable-auto-unlock --unencrypted`).
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

## Password changes

A line in the passwd PAM stack keeps the wrap in step, the way
`pam_gnome_keyring use_authtok` keeps the login keyring in step:

    -password   optional	pam_exec.so quiet expose_authtok /usr/libexec/luma-keyring --pam-rewrap

`luma-keyring-pam-setup` adds it after `pam_gnome_keyring` (package `%post`
and a trigger on shadow-utils, which owns `/etc/pam.d/passwd`) and removes it
on uninstall. pam_exec hands the new password on stdin after `pam_unix` has
set it; the helper unseals the key on the device (as the person, or as root
with `--uid` for an administrator's change), wraps it with the new password
and writes the slot as the account. It always exits 0, and the leading `-`
skips the line if pam_exec is missing, so it can never fail a password
change. `passwd` and Settings (which runs `passwd`) go through it.

Changes that bypass PAM (an administrator setting a hash through
AccountsService or `usermod -p`) are caught by a fallback: each wrap records
the password-change day (AccountsService) and when it was made; the session
service asks for the new password once when the change day is later than the
recorded one, or on or after the wrap's day while `/etc/shadow` was written
after the wrap (Luma is single-person, so that is the password changing).

## Storage hygiene

- Both files live in `~/.local/state/luma-keyring` (0700; the same path for
  the installer, PAM and the session). The files are 0600 and owned by the
  user.
- Every write goes to a temporary file created with `O_CREAT|O_EXCL|O_NOFOLLOW`
  (after removing anything there), is fsynced, renamed into place, and the
  directory fsynced. A linked or group/other-accessible directory is refused.
- Root callers (the installer's `--enrol`, an administrator's password
  change) seal as root but write in a child that has dropped to the
  account's uid and gid, so a planted link can only point where the account
  could write anyway.
- Secrets reach `systemd-creds` and the CLI on stdin only, never in argv or
  the environment. One secret is the whole input less one trailing newline
  or NUL, so a password may contain newlines. Two are each NUL-terminated.
  Secrets are never logged.
- Password-holding processes switch off core dumps and tracing
  (`RLIMIT_CORE=0`, `PR_SET_DUMPABLE=0`; `LimitCORE=0` in the units).
- Python cannot reliably wipe its own strings, so key material is held only
  for the length of one call.
- The installer's handoff file is written root-only (`superuser: "require"`)
  and removed by the `%post`, when the Account step fails, when automatic
  sign-in is turned off, and on the installer's error screen.

## Failure modes

| Situation | What happens |
| --- | --- |
| Seal unusable (new board, cleared TPM, lost host key, Secure Boot change with FDE) or corrupt | Keyring left intact and locked; one notification asks for the password, which unwraps the key; the key is sealed again |
| Sealing fails | Nothing changes; the keyring stays on the password; Luma asks again later |
| Crash during a key change | Some slot on disk always opens the keyring (see crash consistency) |
| Wrap corrupt or missing | Keyring left intact; the person is told plainly; "Start a new keyring" keeps the old file aside |
| Password changed | Re-wrapped at the change by the PAM hook; otherwise the fallback asks for the new password once |
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

**With a TPM2 and full-disk encryption:**
- Protected: a copied home folder or keyring, a removed disk, a backup
  restored on other hardware, and another boot chain on the same device
  (PCR 7). None of these opens the keyring without the account password.
- Offline guessing against `login.wrap` costs one Argon2id evaluation
  (64 MiB) per guess.

**With a TPM2 and no full-disk encryption:** no PCR policy is used, so the
TPM2 releases the seal to whatever boots. Someone with physical access can
boot another system from a USB stick, read the host key from the
unencrypted disk and unseal the key through the same TPM2. The TPM2 then
protects only against the disk being read on another machine. Full-disk
encryption is what closes this; with it, the seal is also bound to PCR 7.

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

**The recovery copy is an offline password verifier.** Anyone who has
`login.wrap` can guess at the account password, at one Argon2id run (64 MiB)
per guess. It is therefore marked `user.xdg.robots.backup=false` (freedesktop
Common Extended Attributes, honoured by backup tools that read it) and should
be excluded from any backup or sync that does not. A restore onto new
hardware without it loses saved passwords unless the old disk comes along.
When the password a person types is weak (libpwquality's `pwscore` below 50),
Luma says so and recommends a stronger one.

## Rolling back

A luma-keyring older than 0.2 does not know the slots. After a rollback the
keyring stays locked and the older "locked with an older password" window
appears. Two ways out:

- **Standalone script.** `luma-keyring-recover-standalone` is installed at
  `/usr/share/luma-keyring/` and kept in the source tree at
  `src/luma-keyring/tools/`. It needs only python3-cryptography,
  python3-gobject and systemd-creds. Copy it out of the newer deployment, or
  take it from the source tree, and run it in the person's session. It opens
  the key from the seal (or from the wrap with the password) and re-keys the
  login keyring to the login password.
- **Without the script.** `systemd-creds --user --name=luma-login-keyring
  decrypt ~/.local/state/luma-keyring/login.cred -` prints the key. Paste it
  as the "Older password" in the older window, which re-keys the keyring to
  the login password.

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
- XDG Base Directory Specification (`~/.local/state`):
  https://specifications.freedesktop.org/basedir-spec/latest/
- freedesktop Common Extended Attributes (`user.xdg.robots.backup`):
  https://www.freedesktop.org/wiki/CommonExtendedAttributes/
- pam_exec(8) (`expose_authtok`):
  https://man7.org/linux/man-pages/man8/pam_exec.8.html
- systemd.exec(5) (`LimitCORE=`), prctl(2) (`PR_SET_DUMPABLE`):
  https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html,
  https://man7.org/linux/man-pages/man2/prctl.2.html
- TPM PCR usage (PCR 7, Secure Boot state), UAPI Group:
  https://uapi-group.org/specifications/specs/linux_tpm_pcr_registry/
- pam_systemd_loadkey(8):
  https://www.freedesktop.org/software/systemd/man/latest/pam_systemd_loadkey.html
- pam_gdm and the LUKS passphrase at automatic login:
  https://bugzilla.gnome.org/show_bug.cgi?id=769950,
  https://bugzilla.redhat.com/show_bug.cgi?id=2150649
- GNOME Keyring and automatic login (the empty-password advice):
  https://wiki.archlinux.org/title/GNOME/Keyring
