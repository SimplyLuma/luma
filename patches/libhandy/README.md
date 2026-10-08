# libhandy downstream patches

Project Luma retains libhandy's upstream name, SONAME, ABI, APIs, metadata and
LGPL-2.1-or-later license. This narrow Fedora-derived downstream carries the
same compatibility contract as the GTK 3 downstream through libhandy's own
window and header-bar widgets.

- `0000-luma-fedora-spec.patch` gives the package a deterministic Luma release
  and registers the maintained source patches.
- `0001-luma-generic-application-identity.patch` derives one application
  identity per application-owned `HdyWindow`/`HdyApplicationWindow` header bar
  and marks the native work surface, mirroring the GTK 3 patches.
- `0002-luma-generic-command-row.patch` hosts the application's packed header
  actions on the Luma command row below the title row when the header owns the
  automatic identity; `luma-no-command-row` opts a header out.
- `0003-luma-title-slot-and-leaflet-islands.patch` carries the title-slot rule
  into `HdyHeaderBar` and marks an `HdyLeaflet` on the work surface as a split
  so its pages become sibling islands with flat pane headers.
- `0004-luma-identity-title-repeat.patch` keeps one name per window: an
  `HdyHeaderBar` title that only repeats the identity is not shown beside it;
  a different title or a subtitle stays.
- `0005-luma-one-title-row-per-window.patch` lets `HdyWindowMixin` provide the
  title row (identity and controls) when the window's header bars live inside
  the pages of a leaflet work surface; the first page's header becomes a pane
  toolbar and the others flat pane headers, with no identity or controls of
  their own; the roles lift while the leaflet is folded.

The installed Luma GTK 3 theme owns the presentation of the identity, the
command surface and the connected command groups.

Upstream: <https://gitlab.gnome.org/GNOME/libhandy>
