# Dash to Dock patch set

Project Luma carries a narrow downstream patch over Fedora's exact Dash to
Dock 105 source RPM. The patch keeps the extension's identity, license, and
upstream history intact while making Luma's dock material, apps glyph,
divider, geometry, and activity indicators first-class source components.

The four-dot Show Applications glyph is a source-owned SVG installed into the
extension's own `media/` directory by the RPM. The build rejects a package that
does not contain it, preventing an invisible-but-clickable launcher after a
cold boot.

- Upstream: <https://github.com/micheleg/dash-to-dock>
- Fedora source RPM: `gnome-shell-extension-dash-to-dock-105-1.fc44.src.rpm`
- License: GPL-2.0-or-later
- Luma patches: GPL-2.0-or-later, to remain compatible with upstream

`0000-luma-fedora-spec.patch` gives the downstream package a deterministic
release and registers the source patch. `0001-luma-dock-material.patch`
contains the functional and visual changes. It also removes the slide
container's explicit viewport only when the dock is fixed. Sliding docks keep
the upstream clip; the fixed Luma dock needs its surrounding paint space so a
rounded CSS shadow is not cut into a rectangle.

The Trash location remains Dash to Dock's native GIO-backed `trash:///`
surface. Its icon actor is explicitly regular because Prairie supplies
full-colour `user-trash` and `user-trash-full` Places tiles; GIO also advertises
`-symbolic` alternatives, which are appropriate in Filer's sidebar but must
not displace the app-style tile in the dock.

The same native Trash icon is a removal target for icons already pinned in the
dock. It accepts only Dash to Dock's real favorite application actors and calls
GNOME Shell's `AppFavorites.removeFavorite()` API. Dropping there unpins the
application without uninstalling it, touching its data, or moving anything
into the filesystem Trash. Files, devices, running-only icons, and the Trash
location itself are not accepted by this target.

During a valid favorite drag, Trash becomes a distinct native drop target: its
actor scales slightly, receives a soft accent wash, and the dock clears its
insertion placeholder. A successful removal uses a short scale/fade clone over
Trash as acknowledgement. Favorite reorders commit the `AppFavorites` model
inside the accepted drop transaction rather than on the following redraw; this
prevents the source actor from briefly returning to its old slot.

GNOME Shell has no CSS `backdrop-filter`, and its native `Shell.BlurEffect`
clips to a rectangular actor rather than the dock's 16px radius. That produces
a visible rectangular cutoff, so Luma deliberately uses the documented
fallback: `rgba(250,250,251,.90)` without backdrop blur. The full-width panel
can use native compositor blur because its geometry is rectangular.
