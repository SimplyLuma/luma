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
