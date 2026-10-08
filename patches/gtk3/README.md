# GTK 3 downstream patches

Project Luma retains GTK's upstream name, SONAME, ABI, APIs, metadata, and
license. This narrow Fedora-derived downstream owns compatibility-tier Luma
application identity at the real GTK 3 header-bar boundary.

- `0000-luma-fedora-spec.patch` gives the package a deterministic Luma release
  and registers the maintained source patch.
- `0001-luma-generic-application-identity.patch` derives exactly one application
  identity per ordinary application-owned `GtkWindow` from the existing
  application metadata, `GMenuModel`, and `GAction` state. This includes
  applications that correctly register a plain `GtkWindow` instead of using
  the narrower `GtkApplicationWindow` subclass. It does not rewrite application
  content or dialogs, and explicit AppKit identity remains authoritative. A
  late `GtkWindow:application` assignment is handled by GTK itself rather than
  by a launcher or application-specific patch.

- `0003-luma-generic-command-row.patch` lets the header bar that owns the
  automatic identity present the application's own packed actions on a
  secondary command surface below the clean 42px title row. The widgets stay
  the application's real buttons and menus (signals, tooltips, accessibility,
  later `pack_start`/`pack_end` calls all keep working); only their parent box
  changes, and they return to the header when the identity leaves. Transient
  windows, header bars without the automatic identity, and header bars carrying
  the documented `luma-no-command-row` class are untouched. Hosted actions are
  no longer enumerable through `gtk_container_get_children()` on the header
  bar itself, matching the libadwaita behaviour.

- `0004-luma-title-slot-and-paned-islands.patch` hosts a non-text custom title
  (stack switchers, buttons, entries) at the center of the command row and marks
  a `GtkPaned` on the work surface as a split so the theme presents its panes
  as islands.
- `0005-luma-identity-title-and-window-gutter.patch` keeps one name per window
  (a built-in title that only repeats the identity is not shown beside it; a
  different title or a subtitle stays, marked `luma-repeats-identity` while
  suppressed) and one gutter per window (`GtkApplicationWindow` folds a legacy
  container border width into the Luma gutter unless the window opts out with
  `luma-no-native-surfaces`).

The installed Luma GTK 3 theme owns presentation through GTK's supported system
theme interface. No per-user stylesheet, launch wrapper, extension, or runtime
mutation is used.

Upstream: <https://gitlab.gnome.org/GNOME/gtk/>

- `0006-luma-frame-child-clipping.patch` adds the opt-in
  `luma-clip-children` GtkFrame styling contract. The frame clips child drawing
  using GTK's existing rounded-box implementation and the frame's computed CSS
  radius. No new radius value, public ABI, helper or polling process is added.
  Unmarked frames retain ordinary rendering. Quick View opts in; the shared
  Luma theme owns radius/padding. Pixel regression coverage is in
  `tests/gtk3/frame-child-clip.py`; packaged/Wayland verification is a release gate.
