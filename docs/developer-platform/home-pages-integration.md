# Paged Mobile Home integration

Home presents the visible graphical application catalog, retaining preferred
entries first and appending remaining apps in catalog order. Desktop folders
are flattened and duplicate entries suppressed. The native application monitor
updates installation and removal. Search settings is excluded from Home, as
are terminal-command launchers and entries hidden by desktop metadata. The
graphical Terminal app remains eligible. Search is available from the dock.

The native carousel sits above the persistent dock. Its custom GTK container
allocates a bounded viewport independently of page requisitions. Columns and
rows are computed from available logical dimensions, measured tile requisitions,
widget height and existing layout gaps. There is no fixed row count or device
name table. Geometry and style changes invalidate capacity; the model and
resize signals coalesce into one idle update. First-page widgets remain intact;
very short viewports can use a widgets-only first page. Page controls have
44px targets and appear only for multiple pages. Upward Library navigation is
disabled and the magnifier invokes Search.

Patch 0050 targets the Mobile shell baseline through 0043 with dock layout 0044
and widget bounds 0049 (PR 54). It replaces the earlier widget-gutter callback
with the pager layout. Dock surface 0047 and status alignment 0048 affect separate
paths and must be retained during integration. Public main does not yet carry
the owning baseline; this patch is not wired into package composition.

The upstream source commit is `0254669ff329754471a468d2674341d4871a92b3`.
Pre-patch SHA256 values after applying 0044 and 0049:

- `src/app-grid.c`: `16a46359c580d7ae8c99a3be0c5cc83a850098076d9a549d59c41fc91e50c661`
- `src/meson.build`: `590f5377fb11033eb372aea92cd957c091b0667a259d69d8b5eb814eb33923a5`
- `src/stylesheet/common.css`: `2cec85183ca69f4d77e55c689e34697a3efd9842035081b07c7037d8dfb106ee`

Native component tests cover catalog filtering, live app registration/removal,
unique icon coverage, compact/square/tall/wide viewports, dock containment,
page count changes and teardown. Complete shell build and hardware validation
remain separate gates. The guest adapter is a local preview harness; it is not
part of the production patch.

Standalone component checks after applying the patch:

```sh
cc $(pkg-config --cflags gtk+-3.0 libhandy-1 gio-unix-2.0) \
  tests/test-home-pages.c -o test-home-pages \
  $(pkg-config --libs gtk+-3.0 libhandy-1 gio-unix-2.0) -lm
xvfb-run -a ./test-home-pages
cc $(pkg-config --cflags gtk+-3.0 libhandy-1 gio-unix-2.0) \
  src/luma-home-pages.c tests/test-home-catalog.c -o test-home-catalog \
  $(pkg-config --libs gtk+-3.0 libhandy-1 gio-unix-2.0) -lm
./test-home-catalog
```

Before promotion, rebuild the complete shell and qualify hardware safe areas,
rotation, touch and keyboard paging, Search/OSK, app launch/Home return, large
text, RTL, live installs/removals and compact physical-keyboard devices.
