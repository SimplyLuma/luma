# Shared Home widgets

One Weather widget and one Calendar widget, used by native Home hosts. The
model is extracted from Mobile's `PhoshLumaToday`, retaining GPL-3.0-or-later.
It reads the same owned Weather place/cache files and native CalendarServer
protocol. It preserves freshness, units, week-ahead events, file/timezone
monitoring, bounded reads and cancellation. It makes no forecast requests,
creates no app data or second event database. A bounded user timer refreshes the
saved widget city through Weather’s existing provider and atomic cache every
15 minutes, including while its window is closed. Fresh forecasts obey the
provider’s existing refresh policy (30 minutes for forecasts, 15 for shared
station observations); offline failures preserve the cache. The
widget advances cached forecast hours without network requests, and monitors
Weather’s shared atomic station-reading cache for immediate app updates. The
installed Weather launcher selects native or Flatpak storage/provider ownership.

`LumaWidgets.Model` provides definitions and display fields through
`dup_snapshot()` and a coalesced `changed` signal. Definitions own widget IDs,
launch IDs and integer cell spans. Native adapters only render those fields,
activate the declared app and participate in their host's grid.

- GNOME Desktop/Tablet/Mobile: the St host in `luma-shell-presentation/widgets.js`.
  Desktop hosting can use the same host and model; no Desktop-specific widget
  implementation is needed. A Desktop placement surface is separate future work.
- Existing Phosh Mobile: `libluma-home-widgets-gtk3` supplies the native view;
  patch0051 replaces the former in-shell implementation with a thin host.
  That integration requires the maintained Mobile source baseline with
  `luma-today.c` and the shared library's devel/GTK3 packages. It is not evidence
  of a rebuilt or qualified physical-phone image.

Build with Meson, GObject introspection, GIO and json-glib. GTK3 enables its
native view library. The model itself has no GTK dependency. Native hosts pass
their existing CalendarServer bus name; both use the same EDS event data and
model behavior. Keep layout, data semantics and validation in this component,
not copied into each host.
