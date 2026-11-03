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
  - Where the root file system itself is on dm-crypt (LUKS), the seal is
    also bound to PCR 7, the Secure Boot state (`--tpm2-pcrs=7`). The root
    is found from `/proc/self/mountinfo` (`/sysroot` on OSTree, else `/`)
    and its device followed through device-mapper in sysfs, so LVM on LUKS
    and btrfs are both seen; an encrypted data disk or swap alone does not
    count. A Secure Boot database update then needs the password once.
    Without an encrypted root no PCR policy is used; the threat model below
    says what each case protects.
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
  - The file is versioned JSON: `magic`, `version` (3), `kdf`, `kdf_params`,
    `aead`, `salt`, `nonce`, `password_changed`, `wrapped_at`,
    `password_id`, and `ciphertext`. Version 2 files (no `password_id`)
    still open.
  - `password_id` is HMAC-SHA256, under the keyring key, of the account's
    crypt(3) hash at wrap time. It tells the session whether the password
    changed since the wrap, and it is useless without the key, so it is no
    password verifier even for a weak legacy hash.
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
discarded); `recover` tries `login.wrap`, `login.wrap.new` and
`login.wrap.prev`, and falls back to the password itself when the keyring
never moved. So at every instant a
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
- **Devices set up earlier, or automatic sign-in turned on in Settings.** Luma
  asks once, in its own sheet, at the start of the session (see "First
  unlock" below).
  - The password is checked with PAM, and the keyring is re-keyed onto a new
    key that is sealed and wrapped. Its contents are kept.
  - The new seal and wrap are written before the keyring is re-keyed, and
    moved into place only after it succeeds. The keyring is never left on a
    key nothing holds.
  - Over SSH: `luma-keyring --enable-auto-unlock`, with the password on
    stdin, inside the person's session.

## First unlock

An automatic sign-in with no seal and a locked login keyring is the one
case nothing opens. Rather than let the first app that wants a secret raise
gnome-keyring's own prompt, `luma-keyring.service` asks first:

- The unit is ordered after `gnome-session-initialized.target` (the shell
  is up) and before `graphical-session.target`, so autostarted apps start
  after it. It is `Type=notify`: it decides before loading GTK, says
  `READY=1` at once when there is nothing to ask, as soon as the sheet is up
  when there is, and never later than 8 seconds. The session never waits on
  a person.
- The sheet: "Unlock your saved passwords", one line, a password field (the
  TV keyboard on a TV) and "Unlock automatically on this TV" (LumaUI's
  device noun), on by default. The password is checked by unlocking the
  keyring with it (`UnlockWithMasterPassword`); a wrong one stays on the
  sheet. With the switch on, the enrolment above follows (PAM check, seal,
  wrap, re-key; nothing changes if any step fails, and the keyring stays
  unlocked for the session). With it off, only this session is unlocked.
  Cancel leaves it locked; apps then treat it as locked, not signed out.
- While the sheet is up the service holds the system prompter's bus name
  (`org.gnome.keyring.SystemPrompter`; GNOME Shell allows it to be replaced
  and queues for it again) and declines every prompt, which gnome-keyring
  reports to the app as dismissed at once. gnome-keyring decides to prompt
  before it waits for the prompter, so a prompt merely queued behind the
  sheet would still appear afterwards, asking for a password that no longer
  opens a re-keyed keyring. The name is released when the sheet closes, and
  by the bus if the process dies. Where the prompter cannot be replaced
  (gcr-prompter while it shows a prompt) the sheet waits up to 5 seconds and
  then shows anyway.
- Opened by hand, the same window shows the state and the switch. Turning
  automatic unlock on or off needs the password once: off moves the keyring
  back onto the login password and removes both slots, since removing only
  the seal would leave it on a key that only the recovery copy opens.

## Password changes

A line in the passwd PAM stack keeps the wrap in step, the way
`pam_gnome_keyring use_authtok` keeps the login keyring in step:

    -password   optional	pam_luma_keyring.so

`luma-keyring-pam-setup` adds it after `pam_gnome_keyring` (package `%post`
with `Requires(post): passwd`, and a trigger on `passwd` and `shadow-utils`,
either of which owns `/etc/pam.d/passwd` depending on the Fedora release)
and removes it on uninstall. It replaces the `pam_exec` line of earlier
builds.

`pam_luma_keyring.so` is a small native module (C, built with meson,
`/usr/lib64/security`). Optional lines still run after a failing substack,
so it trusts nothing the stack says and checks the outcome itself:

- In the update phase only, it reads `PAM_AUTHTOK` with `pam_get_item`. It
  never starts a conversation, so it cannot add a "Password:" prompt (which
  `pam_exec expose_authtok` does when a module before it cleared the token,
  and which confuses GNOME Settings driving `passwd`). No token is
  `PAM_IGNORE`.
- Accounts without a seal are left alone without starting anything.
- The new password must verify against the account's own hash in
  `/etc/shadow` (`crypt_rn` from libxcrypt, compared in constant time).
  Otherwise the change did not happen (`pam_unix` failed after
  `pam_pwquality` set the token) and nothing is written.
- Only then does it run `/usr/libexec/luma-keyring --pam-rewrap` with a
  clean environment and the password on a socket, bounded to 20 seconds.
  No file descriptor of the caller reaches the helper (`close_range`).
  It always returns `PAM_IGNORE`, so it can neither fail nor pass a
  password change. The leading `-` skips the line if the module is missing
  (after a rollback).

The helper does everything that touches the account's files as the
account: it reads each seal itself (no links, FIFOs or devices), opens it
with `systemd-creds --user` on standard input, wraps the key with the new
password, and writes the slot. The last confirmed wrap is kept as
`login.wrap.prev` until the session confirms the change; a second change
before that keeps it rather than replacing it, so an `/etc` rolled back to
the confirmed password still finds its wrap. Each pending seal
keeps its own wrap. `passwd`, Settings (which runs `passwd`) and an
administrator's `passwd USER` go through it.

At each session start of an automatic sign-in, the session compares the
account's own hash, which systemd-userdbd gives to the account itself, with
the wrap's `password_id`:

- Equal: the change is confirmed and `login.wrap.prev` is removed.
- Equal to the previous wrap's: the change did not stick (`/etc` rolled
  back), so the previous wrap is put back.
- Neither: the password changed outside PAM (AccountsService,
  `usermod -p`, `chpasswd`); the person is asked for the new password once.

Without userdbd the change day (AccountsService) is compared instead, and
only a later day counts. Other writes to `/etc/shadow` never raise a prompt.

## Storage hygiene

- The slots live in `~/.local/state/luma-keyring` (0700; the same path for
  the installer, PAM and the session). The files are 0600 and owned by the
  user. A copy of the standalone recovery script sits next to them (0700).
- Every write goes to a temporary file created with `O_CREAT|O_EXCL|O_NOFOLLOW`
  (after removing anything there), is fsynced, renamed into place, and the
  directory fsynced. A linked or group/other-accessible directory is refused.
- The installer's `--enrol` seals as root (`--uid`) and writes in a child
  that has dropped to the account's uid and gid. The password-change hook
  does all of its work in such a child. A planted link, FIFO or device is
  never opened with root's rights: slots are read with
  `O_NOFOLLOW|O_NONBLOCK` and must be regular files, and are handed to
  `systemd-creds` on standard input rather than by path.
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
| Password changed | Re-wrapped at the change by pam_luma_keyring.so; the previous wrap is kept until the session confirms it; otherwise the session asks for the new password once |
| Password change fails | The new password does not verify against /etc/shadow; nothing is written; no extra prompt |
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

**With a TPM2 and an encrypted root:**
- Protected: a copied home folder or keyring, a removed disk, a backup
  restored on other hardware, and booting the device with Secure Boot off
  or under other keys (PCR 7). None of these opens the keyring without the
  account password.
- PCR 7 does not tell apart other media signed under the same Secure Boot
  chain, such as a distribution's live image. Against those, the seal rests
  on the host key, which is on the encrypted root: the protection is as
  strong as the disk's unlock. If the disk itself unlocks from the TPM2
  with PCR 7 alone, such media can unlock it too; a disk passphrase or a
  TPM2 PIN closes that.
- Offline guessing against `login.wrap` costs one Argon2id evaluation
  (64 MiB) per guess.

**With a TPM2 and no encrypted root:** no PCR policy is used, so the
TPM2 releases the seal to whatever boots. Someone with physical access can
boot another system from a USB stick, read the host key from the
unencrypted disk and unseal the key through the same TPM2. The TPM2 then
protects only against the disk being read on another machine. Encrypting
the root is what closes this; with it, the seal is also bound to PCR 7.

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
`login.wrap` (or `login.wrap.prev`) can guess at the account password, at
one Argon2id run (64 MiB) per guess. The state folder therefore holds a
`.deja-dup-ignore` file, which Deja Dup (42.1 and later), GNOME's backup
tool, honours by leaving the folder out. The files are also marked
`user.xdg.robots.backup=false` (freedesktop Common Extended Attributes), but
few tools read that; any other backup or sync should exclude
`~/.local/state/luma-keyring`. `CACHEDIR.TAG` is not used: the folder is not
a cache, and cleaners delete tagged folders. A restore onto new
hardware without it loses saved passwords unless the old disk comes along.
When the password a person types is weak (libpwquality's `pwscore` below 50),
Luma says so and recommends a stronger one.

## Rolling back

A luma-keyring older than 0.2 does not know the slots. After a rollback the
keyring stays locked and the older "locked with an older password" window
appears. Two ways out:

- **Standalone script.** `luma-keyring-recover-standalone` is installed at
  `/usr/share/luma-keyring/`, which a rollback takes away, so luma-keyring
  also keeps a copy at `~/.local/state/luma-keyring/luma-keyring-recover`
  (written at enrolment, refreshed at session start). It is kept in the
  source tree at `src/luma-keyring/tools/`. It needs only
  python3-cryptography, python3-gobject and systemd-creds; run it in the
  person's session. It tries every seal (`login.cred`, `login.cred.new`),
  and a seal that opens but holds a key the keyring refuses counts as not
  opening. It then asks for the password and tries every wrap
  (`login.wrap`, `login.wrap.new`, `login.wrap.prev`, versions 2 and 3).
  The first key the keyring takes re-keys it to the login password.
- **Without the script.** `systemd-creds --user --name=luma-login-keyring
  decrypt - - < ~/.local/state/luma-keyring/login.cred` prints the key. Paste it
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
- The Linux-PAM Module Writers' Guide (`pam_sm_chauthtok`,
  `PAM_UPDATE_AUTHTOK`, `pam_get_item`):
  https://www.linux-pam.org/Linux-PAM-html/Linux-PAM_MWG.html
- crypt(3) and `crypt_rn`, libxcrypt:
  https://man7.org/linux/man-pages/man3/crypt.3.html
- JSON User Records, the privileged section (`hashedPassword`), and
  userdbctl(1): https://systemd.io/USER_RECORD/,
  https://www.freedesktop.org/software/systemd/man/latest/userdbctl.html
- Deja Dup, folders holding `.deja-dup-ignore` are left out:
  https://gitlab.gnome.org/World/deja-dup/-/issues/112
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
