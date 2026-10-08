# gnome-settings-daemon downstream

Project Luma builds Fedora 44's `gnome-settings-daemon-50.1-1.fc44` source
package with ordered downstream patches (`scripts/packages/build-gnome-settings-daemon.sh`).
gnome-settings-daemon remains GPL-2.0-or-later AND LGPL-2.1-or-later and keeps
its upstream name and identity.

`0000-luma-fedora-spec.patch` sets the Luma release and adds the patches below.

`0001-luma-media-keys-no-volume-change-sound.patch` stops the media-keys
plugin playing `audio-volume-change` when a volume key changes the volume, for
every key variant (normal, quiet and precise). The volume still changes and
the level is still shown through GNOME Shell's `ShowOSD`, which Luma presents
as Beam. Other event sounds are unaffected. Luma removes the same sound from
the Shell's volume slider (GNOME Shell patch 0133) and disables the event in
its sound theme (`src/luma-sound-theme`). Dropping this patch restores GNOME's
key press sound.

`0002-luma-housekeeping-named-memory-notice.patch` rewrites the notice the
housekeeping plugin shows when systemd-oomd stops a unit. Upstream said
"Application Stopped … An application was using a lot of memory" whenever
the unit's app ID did not parse to an installed desktop entry, posted it as
gnome-settings-daemon, and showed a new critical banner for every
`PropertiesChanged` carrying `Result=oom-kill`. Luma resolves the unit to the
app (`app[-launcher]-<id>-<n>.scope`, `app-<id>[@n].service`,
`dbus-<bus>-<id>@n.service`, `\xNN` escapes decoded); an app ID with no
desktop entry still gives its last component ("Chromium"), and anything that
is not an app is described plainly ("A background task", "A terminal tab").
The notice reads "Memory Full — Stage was stopped because memory was full.",
comes from Monitor (`io.luma.Monitor`) with the stopped app's icon, and stops
within 10 seconds update that one notice ("Stage and 1 other app were
stopped…"); a repeated signal for the same unit is ignored. The logic is in
`gsd-oom-notice.c`, tested by `gsd-oom-notice-test` in the spec's `%check`
(meson suite `luma`), modelled on the two kills of 2026-09-22 (a Chromium
scope and a `luma-dev-env-*` service). Dropping this patch restores GNOME's
anonymous notice.
