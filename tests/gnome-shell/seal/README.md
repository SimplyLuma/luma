# Seal evidence harness

Evidence for GNOME Shell patch 0146 (Seal, the administrator prompt). Runs a
headless Shell in a logind session inside a systemd test container
(`luma-seal-it` on the build server) against real polkitd, the real polkit
agent helper, Fedora's `authselect local with-fingerprint` PAM stack and
fprintd with libfprint's virtual reader.

- `mechanism.py` runs as root and stands in for udisks, flatpak-system-helper
  and timedated: it asks polkitd about a requesting process with the message
  and details that mechanism passes, and drives the virtual reader.
- `requester.py` stands in for an app, launched by `eval.js` in the app's own
  systemd scope (`app-gnome-ID-N.scope`), as the shell launches apps.
- `session.sh` (inside the session) and `go.sh TAG THEME [K=V ...]` (on the
  host). `SEAL_ONLY` picks cases: `usb`, `wrong`, `fingerprint`, `miss`,
  `pkexec`, `race`, `escape`, `keyboard`, `unknown`, `noreader`, `reduced`
  (with `SEAL_ANIM=false`). `SEAL_SCALE=2` with `SEAL_MONITOR=2880x1800`.
- The container needs: systemd-pam, polkit and accounts-daemon without
  namespace sandboxing, a `seal-session` PAM service (pam_systemd), a
  `systemd-user` PAM file with pam_loginuid optional, fprintd with
  `FP_VIRTUAL_DEVICE`, an enrolled `nick-right-index` print, a bubblewrap
  stand-in for glycin and a fake `/sys/class/block/sda/size`. None of this is
  shipped; the polkit-1 PAM stack is Fedora's, unmodified.
