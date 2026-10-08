# GNOME Calendar downstream patch

Project Luma carries two isolated patches against Fedora 44's exact
`gnome-calendar-50.0-1.fc44` source package. Its SHA-256 is
`63b0ec56f2340b538dc7248385ae721d28e6ef9bf144e70f5dd39562c0746ba6`.

`0001-luma-calendar-aesthetic-v1.patch` retains Calendar's application ID,
Evolution Data Server integration, user-owned calendar colors, actions,
shortcuts, Month and Week views, search, event creation and editing, accounts,
menus, schemas, and adaptive behavior. It changes only application composition
and styling:

- a 34-pixel identity title bar above Calendar's existing native header;
- Calendar's existing date chooser and live calendar list presented as a
  210–232-pixel default-open sidebar on wide windows, extending directly from
  the shared title bar to the bottom edge without a redundant local header;
- the original bottom navigation, view, event, and calendar controls moved into
  the single native action header directly below the title bar;
- one compact Month/Week menu bound to the existing typed view action plus a
  standardized 30-pixel square treatment for applicable navigation and action
  icons;
- a combined month/year mini-calendar navigator anchored at the bottom of the
  sidebar, a semantic separator below the source list, and a continuous
  content-divider hairline matching the action header edge;
- separately styled month and year title inks without changing the date chooser;
- white Month and Week surfaces with neutral hairline grids and typography;
- a connected native view switcher and consistent bordered toolbar controls;
- solid event-color chips that continue to consume each calendar's stored color;
  and
- a source-owned two-pixel current-time line with an eight-pixel round marker.

No sidebar is reimplemented: the visible component is Calendar's existing
`AdwOverlaySplitView` sidebar, `GcalDateChooser`, and `GcalCalendarList`. It
still collapses at Calendar's adaptive breakpoint and remains toggleable from
the existing control, now located in the content action header and still wired
to F9. The action header reuses the original widgets and actions rather than
duplicating them.

`0002-luma-title-label-metrics.patch` applies the shared semantic
`luma-title-label` class to Calendar's existing centered title. The actual
Figtree line-box correction is owned once by the downstream libadwaita package,
so Calendar does not duplicate global title metrics.

Calendar does not install a user stylesheet, theme override, extension,
startup hook, or injected CSS provider. It remains GNOME Calendar under
GPL-3.0-or-later and CC0-1.0, with its upstream identity, metadata, links, and
attribution intact.
