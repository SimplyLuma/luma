# Luma shell state

This package exposes the form-factor-neutral Luma shell contract on the user
session bus. It deliberately reuses standard GNOME settings as the canonical
storage for favorites, wallpaper, appearance, idle blanking, and the on-screen
keyboard. The Luma schema stores only product state with no standard home.

Schema version 2 adds the renderer-independent **Shelf** contract. It records
edge, anchoring, island relationship, visibility, work-area, overflow,
monitor, and material policy without exposing GNOME Shell actor names. Dark is
the default material; Light, Glass, and Frost remain selectable treatments.
Automatic hiding is intentionally rejected in v1. This boundary lets a future
trusted Mod replace the Shelf capability through declared ownership rather
than runtime monkey-patching, extensions, polling, or startup scripts.

Phosh/Phoc and Luma GNOME/Mutter are renderers of this state. Neither renderer
may introduce a second settings database or a second application catalog.
Phosh's legacy `favorites` key is treated strictly as a bidirectional renderer
projection of `org.gnome.shell favorite-apps`, so reordering in either posture
updates the same canonical order.

The packaged `Luma` and `Luma-dark` GTK 3 themes use GTK's supported system
theme interface for legacy applications and native Phosh widget types. The
session service projects an explicitly chosen `surface-treatment` onto those
Luma-owned theme names, otherwise following `color-scheme`. Dark selects
`Luma-dark`; Light and unsupported Frost/Glass use opaque `Luma`. GTK 3
predates the modern appearance setting;
it never replaces a deliberately selected third-party theme. The themes do
not use a user stylesheet, application wrapper, or second shell surface.
`luma-shell-statectl get` prints the effective, non-sensitive state for
diagnostics. Mutating operations validate desktop IDs and local wallpaper URIs
before changing the canonical settings.

Dash spacing uses `shelf-padding` (integer, 4–24 logical pixels, default 14).
It controls content insets, floating screen-edge gaps and island separation;
inter-icon spacing remains independent. The additive snapshot field is `shelf.padding`.

Home Screen preferences apply to both Mobile and Tablet Home. `home-columns`
and `home-rows` use zero for automatic density; manual counts are bounded by
available space. `home-icon-size` changes artwork within usable touch targets,
and `home-show-labels` controls visual app names without removing accessible
names. Grid positions remain in `home-layouts`, keyed by presentation and grid
size, so switching density can return to a saved arrangement. Resetting Home
preferences never clears saved layouts. Desktop presentation does not consume
these keys.

## Posture (TV Mode)

`posture` (`automatic` or `tv`, default `automatic`) is the experience above
the presentation. It never changes the presentation: `presentation.effective`
stays desktop, tablet or mobile, so every existing reader is unaffected, and
the presentation choice underneath returns unchanged when TV is turned off.

The resolved posture is published three ways, all from the same rule
(`posture_state`):

- the snapshot's additive `posture` object: `selected`, `effective` (one of
  `desktop`, `tablet`, `mobile` or `tv`), `reason` (`user`, `device` or
  `presentation`) and `tv`;
- the `Posture` D-Bus property beside `Presentation`, with the same
  `PropertiesChanged` notification, which LumaUI's session context follows
  live;
- the `posture` key itself, for readers without the session bus.

Automatic resolves to TV when the device declares itself a television:
`tv` in `/run/luma/experience` or `/etc/luma-device-class` (the install-time
default). Its presentation underneath is Desktop.

Ways in and out: `luma-shell-statectl set posture tv|auto` (or
`luma-shell-statectl posture tv|automatic`), the `SetPosture` method, and the
TV choice in the shell's Experience options. The switch is live: the shell
and LumaUI follow the property change without a log-out.

`tv-idle-delay` (seconds, default `-1`) is the person's choice of when a TV
app shows its idle view (Settings › Idle & Sleep). `-1` leaves it to each app
(LumaUI `TvIdle`'s own delay), `0` never shows it; any other value overrides
every app's delay and is followed live.

`tv-screen-edges` (0–10, default 0) is the share of the screen given back at
each edge in TV posture, for a TV that overscans (Settings › Display › Fit ›
Screen edges). The shell's TV presentation scales its UI group, which holds
the TV home and every window, about the centre, live.
