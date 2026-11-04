# gdm patches

Applied by `0000-luma-fedora-spec.patch` (to Fedora's 50.3-1 spec, release
`1.luma.N`) on top of `gdm-50.3.tar.xz`; built by `scripts/packages/build-gdm.sh`
(desktop-components group `gdm`). The package also ships
`/usr/share/gdm/luma-setup-handover`, which tells luma-setup that this gdm waits.

| Patch | Origin | Why |
|---|---|---|
| 0001 | Luma, 2026-11-03; Luma-specific | Luma's first-boot setup signs the person in from GDM's initial-setup session with its last frame on screen, its last step still turning. Stock gdm switches to the new session's VT before starting it, so the setup's display stops presenting at once and the screen holds a still frame for the whole session start (about 15 s on a Raspberry Pi 5). While `/run/luma-setup/greeter` exists (made by `luma-setup-apply` just before the sign-in, at most 120 s old), the session worker starts the session on its VT without switching and switches when the setup says go (`/run/luma-setup/greeter/go`, written once the session's Shell is up with the setup's frame as its cover and the setup has held its frame), or after 30 s. Mutter starts on the inactive session with its devices paused and resumes on the switch. |
