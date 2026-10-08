# Sticky Notes provenance

Sticky Notes' implementation, application icon and style sheet were created
for Project Luma and are licensed under Apache-2.0. No third-party
application source, sample note content, or Design Center asset is copied
into this tree.

Runtime dependencies are used through their supported system interfaces:

- GTK 4, libadwaita, GLib/GIO, Pango/PangoCairo and GObject Introspection:
  LGPL-2.1-or-later.
- Luma Application Kit, Luma UI and Luma Appearance: Project Luma
  Apache-2.0 components.
- `gtk4-layer-shell` (LGPL-3.0-or-later) is imported optionally at runtime on
  sessions that provide it, exactly as `prairie-core`'s phone daemon does. It
  is not vendored, not linked, and not a package dependency; a session without
  it runs the dock as an ordinary window.

The five paper colours are original values chosen for this application. They
are document data stored with each note, not palette tokens; if they are ever
adopted as system colours they belong in `config/shared/design-tokens.json`.

No handwriting typeface is bundled. The body face is requested by name with a
fallback chain ending in the kit's own sans; see the branch report.
