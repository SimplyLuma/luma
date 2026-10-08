# GTK 4 downstream patches

Project Luma retains GTK's name, SONAME, APIs, ABI, metadata, license, and
upstream identity. These patches are a narrow Fedora-derived downstream used to
own distro-wide window material at its canonical toolkit source.

- `0000-luma-fedora-spec.patch` gives the Fedora 44 package a deterministic
  Luma release and registers the source patch.
- `0001-luma-calm-window-shadow.patch` changes only ordinary light-mode CSD
  active/backdrop shadows as the narrow first downstream step.
- `0002-luma-native-window-elevation.patch` completes the source-owned Luma
  contract for ordinary GTK CSD windows with the shared 15px frame and layered
  active/backdrop elevation in Light and Dark. Popups, message dialogs,
  tiled/maximized/fullscreen windows, and server-side decoration remain
  upstream-owned.
- `0003-luma-native-headerbar-chrome.patch` makes the 42px Figtree title row
  and compact connected native window controls the GTK default, so non-
  libadwaita GTK applications retain their actions while matching Luma.
- `0004-luma-generic-application-identity.patch` derives one compatibility
  identity per application-owned `GtkWindow` from the real GtkApplication
  metadata, GMenuModel, and GActions, including registered windows that do not
  use the narrower `GtkApplicationWindow` subclass. Explicit AppKit identities
  and the documented escape class remain authoritative. It observes late
  `GtkWindow:application` assignment so builder-created header bars follow the
  same toolkit contract.
- `0005-luma-application-identity-presentation.patch` gives that native identity
  the same AppKit geometry in maintained SCSS and generated release CSS.
- `0006-luma-native-application-surfaces.patch` marks ordinary
  `GtkApplicationWindow` content as the shared Luma work surface.
- `0007-luma-generic-application-frame.patch` completes the generic frame:
  identity resolution through `GDesktopAppInfo` with ordered icon fallbacks and
  a clipped 6px icon tile, suppression of a synthesized title that repeats the
  identity, the generic command row hosted by the window's `GtkHeaderBar`
  (real widgets re-hosted below the 42px title row, `pack`/`remove` routed,
  `luma-no-command-row` opt-out), the work surface's rounded clip and 9px
  rhythm, the 24px connected control pill, and stable backdrop chrome, in both
  the maintained SCSS and the generated release CSS.

The theme patch updates both `_common.scss` and the generated
`Default-light.css` and `Default-dark.css` carried by GTK's release tarball.
Keep those declarations synchronized when rebasing.

Upstream: <https://gitlab.gnome.org/GNOME/gtk/>
- `0008-luma-title-slot-and-paned-islands.patch` hosts non-text title widgets
  on the command row and presents a `GtkPaned` work surface as two clipped
  islands separated by the 9px handle.
- `0009-luma-split-island-shadows.patch` keeps a pane island's deep shadow off
  the neighbouring pane (negative spread of at least half the blur), in the
  SCSS and the generated release CSS.
- `0013-gsk-restore-the-clip-when-a-first-node-is-rejected.patch` is an upstream
  GSK bug fix, not a Luma change: when a clip node is rejected as a render
  pass's first node, the GPU node processor restored the scissor but copied the
  narrowed clip back over the saved one, so later nodes in the pass were
  clipped away and GSK warned that clipping is broken. Prepared for upstream
  submission; drop it once GTK carries the fix.
- `0014-gsk-cover-hinted-glyph-outlines.patch` is an upstream GSK bug fix, not
  a Luma change: text node bounds and the GPU glyph cache used unhinted glyph
  extents while hinted fonts draw edges moved onto the pixel grid, so the
  hinted rows above them (the top bar of a 7, the dot of an i, the hook of an
  f, accents) were left out of redraw regions and atlas boxes and went missing
  from typed text. Both now also cover the grid-fitted extents. Present in
  4.22.4 and on main (regression of #6568/#6577); prepared for upstream
  submission; drop it once GTK carries the fix.
- `0017-luma-maximized-shadow-extents.patch` allocates the actual CSS outer
  shadow for maximized CSD windows in Luma's inset work area. Maximized styles
  with no outer shadow still have zero margins; fullscreen remains zero and
  only floating windows receive resize-handle minimums. The frame rectangle,
  work-area padding, restore state and unrelated client layout are unchanged.
  `tests/toolkit-native/maximized-shadow.py` exercises the native library's
  state transitions through the standard X11 WM interface; compositor visual
  evidence is a separate gate.
