# Luma sound theme

Luma's event sound theme, following the freedesktop.org Sound Theme
Specification. It inherits every sound from `freedesktop` and disables two
events: `audio-volume-change`, the "pop" GNOME plays when the volume changes,
and `bell`, the system bell's sound. Luma shows a volume change on screen
(Beam) and makes no sound for it; the error bell (Backspace with nothing to
delete, and refused actions like it) is silent outright, with no replacement.
Every other event sound, including other dialog sounds, still plays.

- `index.theme` names the theme and inherits `freedesktop`.
- `stereo/audio-volume-change.disabled` and `stereo/bell.disabled` disable
  their events. libcanberra checks for a `.disabled` entry before any sound
  file and returns `CA_ERROR_DISABLED`, so no player, sample cache or fallback
  theme plays it.
- `10_luma-sound-theme.gschema.override` makes `luma` the default
  `org.gnome.desktop.sound theme-name`. It does not touch `event-sounds`:
  every other event sound still plays.

This is the backstop, in case anything ever asks the sound theme for `bell` or
`audio-volume-change` directly. The volume sound is also removed where it is
requested: GNOME Shell patch `0133-luma-silent-volume-change.patch` (the Quick
Options slider) and gnome-settings-daemon patch
`0001-luma-media-keys-no-volume-change-sound.patch` (the volume keys). The
error bell is removed at its own source: GTK never asks for a beep, because
`gtk-error-bell` is off by default in the image
(`config/desktop/gtk-3.0/settings.ini`, `config/desktop/gtk-4.0/settings.ini`),
and the window manager's own bell is also silent by default
(`org.gnome.desktop.wm.preferences audible-bell` in
`config/desktop/dconf/db/luma.d/00-luma-desktop`), so this theme entry is only
ever reached by a bell request that bypasses both.

Removing the package restores the `freedesktop` default theme and its sounds.
