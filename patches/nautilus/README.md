# Nautilus downstream patch

Project Luma carries a narrow interface patch set against Fedora 44's
`nautilus-50.2.2-2.fc44` source package.

Nautilus normally exposes **Search Everywhere** in the sidebar header and
**Search Current Folder** in the content toolbar. Luma removes the sidebar
button and makes the existing toolbar button invoke Nautilus's existing
`slot.search-global` action. Its tooltip becomes **Search** and it uses the
standard find icon. The search engine, results, keyboard shortcuts, schemas,
and file-management behavior are otherwise unchanged.

The primary patch removes the redundant **Files** label, sets the
fresh-profile sidebar width to 178 pixels with libadwaita's native 25% preferred
split ratio, and adds a transparent
native GTK drag target at its content edge. Pointer movement is measured in stable surface
coordinates and the supported libadwaita width range remains valid throughout
each update, avoiding feedback and intermediate relayouts. The chosen width is
clamped to 120–480 pixels, saved in Nautilus's existing `window-state` schema,
restored for later windows, mirrored for right-to-left layouts, and hidden when
the adaptive sidebar is collapsed. A second, deliberately tiny interface patch
installs an empty title widget; simply deleting the title widget made
libadwaita fall back to Nautilus's current location title, such as **Home**.

The source RPM SHA-256 is
`89d839199f7585b11a7df957ab455eae4b7adf6092831b726bce77b68374a1c7`.

The v1 aesthetic patch gives the normal light Files window two native rows: a
dedicated full-width title `AdwHeaderBar` and the existing functional Nautilus
toolbar directly below it. The toolbar keeps the original actions and receives
the existing application menu through a typed `GMenuModel` property. A single
packaged application stylesheet defines Luma's neutral palette and accent
tokens once, then applies component geometry without a user stylesheet,
runtime injection, `!important`, or icon replacement. Dark mode is outside the
v1 scope and remains upstream.

The fourth patch establishes **Filer** as Project Luma's user-facing file
manager identity. It deliberately retains the `nautilus` executable,
`org.gnome.Nautilus` application ID, D-Bus interfaces, schemas, extension ABI,
upstream icon, and internal symbol names for compatibility. The About dialog
names Project Luma as the downstream developer and prominently identifies
GNOME Files/Nautilus as the source, while retaining the GNOME contributors,
links, and GPL/LGPL licensing.

That patch also gives the main-menu action the same native neutral button
treatment as the history and view controls. Its storage section reuses
Nautilus's existing `GVolumeMonitor`-backed drive, volume, mount, hotplug,
mount, unmount, and eject implementation. A real `file:///` **System Disk**
row fills the one gap left by backends that do not expose the root filesystem
as a `GMount`; the existing sidebar section sorter supplies its divider.

The fifth patch adopts libadwaita's native split-header pattern. Filer's
existing Back/Forward controls live in a sidebar header, while the existing
breadcrumb, search, view, and menu controls remain in the content header. The
real sidebar therefore begins directly beneath the shared title bar, and its
right-hand divider uses the same 10% neutral hairline as the action toolbar's
bottom edge for the full window height (mirrored for RTL). This is a native
composition change, not a decorative overlay; all existing toolbar actions,
sidebar bindings, resizing, and adaptive behavior remain.
The sidebar header owns an explicit empty title widget so libadwaita never
falls back to duplicating the active location beside Back/Forward.

The sixth patch explicitly registers the history and view-control widget types
that the window template now owns directly. This is the supported GTK template
contract: every custom type is registered before `gtk_widget_init_template()`,
so clean Blueprint output cannot depend on incidental construction order.

The seventh patch removes Filer's fixed breadcrumb width and lets its native
pathbar request the natural width of the displayed path. The existing current-
folder menu remains part of `NautilusPathBar`, but receives an explicit semantic
class and a full-size attached segment: the pathbar owns the single outer
stroke, while one hairline separates the breadcrumb and menu. Their touching
corners are square, outer corners remain rounded, and the treatment mirrors for
right-to-left layouts. No path, overflow, scrolling, menu, or action behavior is
reimplemented. The same patch changes the icon-view schema default from native
`medium` to the immediately preceding native `small-plus` zoom level. It does
not hard-code a CSS icon size, alter the zoom range, or affect list view.

The eighth patch corrects the GTK measurement contract exposed by the natural-
width change: `GtkScrolledWindow` does not propagate its child's natural width
unless explicitly asked. Filer enables that native property, prepends the
existing menu, gives the breadcrumb equal 8px start/end content margins, and
gives the menu a 30px width request; its height continues to fill the pathbar's
30px allocation, making the segment square without duplicating the menu.

The ninth patch removes Filer's redundant hover tooltips at their native
registration points. Back, Forward, Search, Grid, List, View Options, Main
Menu, the current-folder menu, breadcrumb segments, and sidebar place rows no
longer create GTK tooltips. Explicit accessible labels replace tooltip-derived
names for icon-only controls, breadcrumb segments, and sidebar rows. Their
icons, actions, keyboard focus, context menus, and storage eject tooltips remain
intact; there is no CSS suppression, global tooltip-delay change, or runtime
filter.

The tenth patch restyles only Nautilus's ordinary empty-directory branch. It
keeps the existing `AdwStatusPage`, overlay lifecycle, directory model, and
`0 items` status calculation, but gives that branch a semantic class, the
Prairie 82px outline folder resource, quiet sentence-case copy, exact 15px
cluster gap, and 26px optical bottom padding. Search, Trash, Starred, Recent,
and Network empty states retain their distinct upstream content and treatment.

The eleventh patch originally applied the shared semantic `luma-title-label`
class to Filer's centered identity title. Patch eighteen removes that obsolete
center title after the running simulator established the top-left identity as
the sole title-row label.

The twelfth patch establishes Filer as the reference implementation of Luma's
responsive contract. It replaces upstream's 682sp narrow mode with independent
640px compact and 1024px medium app-surface breakpoints, preserves the custom
window title at every windowed width, and keeps the native
`AdwOverlaySplitView` as the sidebar's pinned/overlay engine. Compact mode uses
one native toolbar with sidebar, back, root-to-current compact breadcrumb,
search, and a
composed overflow containing the live view/sort/options and application menus.
The persistent narrow bottom action bar is disabled. `NautilusPathBar` owns the
real compact path summary and navigable root-first ancestor model; wider allocations
keep the current segment visible and expose leading ancestors when the path
overflows. No duplicate path state, scrolling toolbar, or CSS-hidden action is
introduced.

The thirteenth patch adds **Applications** as a first-class, read-only Filer
location. A native `NautilusDirectory` provider enumerates the desktop's
`GAppInfo` registry, preserves localized names and real application icons,
publishes source dates where the desktop entry provides them, refreshes through
`GAppInfoMonitor`, and feeds Filer's existing grid/list, zoom, sort, caption,
selection, and status machinery. Activation launches the registered
application directly; the item menu contains **Open**, **Show Application File**, and **Uninstall…**.
Uninstall sends the desktop identity to the unprivileged Install review broker;
Filer does not delete launcher files or run a package manager. The directory, sidebar row, keyboard
actions, and inbound drop path reject mutation. Outbound single-application
drags retain the real desktop-entry file list while adding Mutter's standard
root-window MIME and a narrowly authorized Shell application-ID handshake.
This lets Shell's native Dash pin the registered application and lets the
desktop create an explicit user-owned launcher link; Filer itself never copies
or rewrites a launcher, and no user service or boot-time population step exists.

The fourteenth patch adds a shared `personal-storage-only` capability setting,
defaulting off so the desktop experience remains unchanged. Handheld policy
enables it to suppress the synthetic System Disk row, internal fixed-device
volumes, and native system mounts while retaining the user's normal places,
network locations, and removable/ejectable storage. This is presentation
policy only: it neither unmounts nor changes permissions on any filesystem.
Direct navigation to `/` remains possible, but first presents the Prairie
system-root warning with a safe return action and an explicit one-time
continue action. The setting, warning, and filter live in the same Filer
binary shipped on desktop and mobile; there is no handheld Filer fork.

The fifteenth patch connects package installation to Applications without
making that virtual registry writable. Exactly one recognized local package
dropped on the Applications background advertises a copy/install action and is
sent as a URI to `org.projectluma.ApplicationInstaller2`. All validation,
confirmation, staging, and package-manager work remains in the separately
packaged Luma installer. Filer never parses the package, invokes a compatibility
runtime, copies package bytes, or acquires installation privilege; every other
inbound drop remains rejected.

The sixteenth patch completes Filer's production application composition
without creating a second file-manager path. The real Nautilus application
menu moves into the shared top-left Luma identity trigger, standard toolkit
window controls retain their actions, and
the duplicate toolbar menu disappears. The native Search action becomes a
compact icon control, Grid/List remain real connected view actions, and View
Options remains the existing Nautilus menu beside them. Shared libadwaita
application-surface tokens provide light and dark materials; Filer's own CSS
is limited to its joined sidebar/content composition and native Grid/List row
geometry. The patch also applies Figtree roles, tabular metadata, quiet
hover/selection/focus states, and localized uppercase list headings without
altering models, thumbnails, sorting, persistence, D-Bus, portals, or ABI.

The shared libadwaita patch owns the row geometry for Nautilus's native
`GtkPlacesSidebar`. Its animation revealer—not an app-specific CSS shim—carries
the same 7-pixel vertical and 11-pixel horizontal padding as other navigation
sidebars, restoring consistent spacing around every Filer row and selection.

The seventeenth patch adopts the simulator's Atlas island composition and adds
Columns as a real third Nautilus view. The Places sidebar and active browser
surface are peer native islands with the shared 9px body inset and gap; the
toolbar moves inside the browser surface without duplicating any actions or
models. `NautilusColumnView` subclasses `NautilusListBase`, consumes the same
live `NautilusViewModel`, file objects, selection, sorting, operations, DnD,
rename, activation, monitor, and slot history as Grid and List, and contributes
a monitored parent directory plus real selection metadata. Grid, List, and
Columns share one stateful action, three connected controls, real menu entries,
and Ctrl+1/Ctrl+2/Ctrl+3 shortcuts. The shared island geometry and elevation
are supplied by downstream libadwaita; Filer CSS retains only file-manager
composition and view-specific row/detail styling.

The eighteenth patch uses the running Luma simulator as an executable geometry
oracle and tightens that native implementation to its production contract. It
sets the fresh-profile Places island to 178px, adds genuine section headings at
the Nautilus sidebar model boundary, and preserves bookmarks as part of Places
while exposing Cloud and Devices only when their real sections contain rows.
Columns uses the simulator's 9px workspace rhythm, 40px headers, 32px rows,
14px icons, measured 11px metadata typography, and a 190px Details inspector.
The referenced simulator revision kept the breadcrumb as the only location
authority. Details now
publishes real created/modified/location metadata, real folder item counts,
and persisted Nautilus emblem tags when present; empty tag chrome is never
fabricated. The two browsing panes adapt at 640px and the inspector at 520px,
without adding a parallel file model or presentation-only data store.

The nineteenth patch applies the later authoritative title-row contract:
Filer's real active-slot title is centered as `Location — Filer`, while the
leading identity trigger remains the one application-menu host. Native
`GtkWindowControls` remain functional and consume the connected control-island
treatment from shared libadwaita instead of carrying Filer-specific shapes.

`0039-luma-menu-surface-ownership.patch` removes generic viewport background
selectors that leaked Filer island paint into GtkPopoverMenu. The toolkit remains
the menu owner; no application-local menu appearance overrides are added.

`0040-luma-shared-empty-panes.patch` keeps Nautilus's directory, filter,
selection, portal and save state machines while presenting their empty and
filtered-empty states through the shared Developer Platform adapter. Open and
Save remain the existing native portal chooser, including overwrite handling
and application-supplied filters.

`0042-luma-native-picker-chrome.patch` is staged directly after 0040 for the
picker-only Filer 50 release. Open omits the Save-only filename/purpose island;
Save retains its real filename entry and validation. The native header uses the
shared automatic application identity, close-only dialog controls and the
existing application-supplied choices menu. Cancel, Select and the file filter
consume the shared compact button/dropdown roles from Developer Platform 54.
The chooser keeps its existing portal actions, GAction/GMenuModel choices,
keyboard path, localization and accessibility relationships. It adds no
application-local context-menu, checkbox or radio styling.

`0044-luma-initialize-shared-surface.patch` follows 0042 in the combined Filer
50 package. It initializes the linked Developer Platform immediately after
native application startup and before the first Filer window is created, so
Frost and Glass backdrop binding is available on ordinary non-empty folders as
well as chooser and empty-state paths. The 0044 identity deliberately avoids
the reserved paused 0041 filename treatment and 0043 image-preview treatment;
neither paused feature is included in Filer 50.

`0045-luma-root-toolbar-material-ownership.patch` marks only Filer's outer
`AdwToolbarView` with the shared `luma-window-toolbar-view` structural role.
This lets Developer Platform clear libadwaita's generated top-bar paint in
Frost and Glass while preserving the nested sidebar and content toolbar views
as independently painted islands.
