ui_headers = files(
  'luma-reorder-hint.h',
  'luma-welcome-view.h',
  'luma-empty-state.h',
  'luma-application-icon.h',
  'luma-action-bar.h',
  'luma-adaptive-scaffold.h',
  'luma-application-window.h',
  'luma-pane.h',
  'luma-media-fader.h',
  'luma-audio-meter.h',
  'luma-segmented-control.h',
  'luma-inspector-section.h',
  'luma-histogram.h',
  'luma-curve-editor.h',
  'luma-color-well.h',
  'luma-context.h',
  'luma-surface-backdrop.h',
  'luma-save-sheet.h',
  'luma-ui.h',
)

# LumaUI parts (ADR-052). A part's header joins the introspected sources with
# its implementation, in ui_part_sources.
ui_part_headers = files(
  'luma-ui-kit.h',
  'luma-layer-host.h',
  'luma-mode-switch.h',
  'luma-corner-pill.h',
  'luma-sidebar-foot.h',
  'luma-sidebar-toggle.h',
  'luma-badges.h',
  'luma-toast.h',
  'luma-destructive-dialog.h',
  'luma-stacked-button.h',
  'luma-controls.h',
  'luma-choice-list.h',
  'luma-progress-line.h',
  'luma-details-pane.h',
  'luma-table-header.h',
  'luma-action-center.h',
  'luma-cards.h',
  'luma-file-card.h',
  'luma-selection-bubble.h',
  'luma-navigation-trail-bar.h',
  'luma-menu-drawer.h',
  'luma-contact.h',
  'luma-text-field.h',
  'luma-place-search.h',
  'luma-message-bubble.h',
  'luma-type-label.h',
  'luma-island.h',
  'luma-title-island.h',
  'luma-list-first.h',
  'luma-tab-bar.h',
  'luma-navigation-sidebar.h',
  'luma-media-transport-lcd.h',
  'luma-creative-workspace.h',
  'luma-creative-tools.h',
  'luma-creative-layers.h',
  'luma-creative-properties.h',
  'luma-timeline.h',
)

wayland_scanner = find_program('wayland-scanner')
background_effect_protocol = files('../protocols/ext-background-effect-v1.xml')
background_effect_client_header = custom_target(
  'ext-background-effect-v1-client-header',
  input: background_effect_protocol,
  output: 'ext-background-effect-v1-client-protocol.h',
  command: [wayland_scanner, 'client-header', '@INPUT@', '@OUTPUT@'],
)
background_effect_client_code = custom_target(
  'ext-background-effect-v1-client-code',
  input: background_effect_protocol,
  output: 'ext-background-effect-v1-client-protocol.c',
  command: [wayland_scanner, 'private-code', '@INPUT@', '@OUTPUT@'],
)

ui_sources = files(
  'luma-reorder-hint.c',
  'luma-welcome-view.c',
  'luma-empty-state.c',
  'luma-application-icon.c',
  'luma-action-bar.c',
  'luma-adaptive-scaffold.c',
  'luma-application-window.c',
  'luma-native-app-updates.c',
  'luma-pane.c',
  'luma-media-fader.c',
  'luma-audio-meter.c',
  'luma-segmented-control.c',
  'luma-inspector-section.c',
  'luma-histogram.c',
  'luma-curve-editor.c',
  'luma-color-well.c',
  'luma-context.c',
  'luma-surface-backdrop.c',
  'luma-save-sheet.c',
  'luma-ui-init.c',
)

# A part joins the library, and its header the introspected sources, once its
# implementation (same stem, .c) exists; until then only the header installs.
fs = import('fs')
ui_part_sources = []
ui_part_gir_headers = []
foreach header : ui_part_headers
  stem = fs.stem(header)
  if fs.exists(stem + '.c')
    ui_part_sources += files(stem + '.c')
    ui_part_gir_headers += header
  endif
endforeach
ui_sources += ui_part_sources

ui_resources = gnome.compile_resources(
  'luma-ui-resources',
  'luma-ui.gresource.xml',
  source_dir: '.',
  c_name: 'luma_ui',
)

ui_inc = include_directories('.', '../semantics')
ui_lib = library(
  'luma-ui-1',
  ui_sources,
  background_effect_client_header,
  background_effect_client_code,
  ui_resources,
  version: '0.1.0',
  soversion: '0',
  dependencies: [glib_dep, gobject_dep, gio_dep, dependency('gio-unix-2.0'), gtk_dep, adw_dep, wayland_client_dep,
                 semantics_dep, appearance_dep,
                 meson.get_compiler('c').find_library('m', required: false)],
  include_directories: ui_inc,
  install: true,
)

ui_dep = declare_dependency(
  link_with: ui_lib,
  include_directories: ui_inc,
  dependencies: [gtk_dep, adw_dep, semantics_dep],
)

install_headers(ui_headers, ui_part_headers, subdir: 'luma-1/luma-ui')

pkg.generate(
  ui_lib,
  name: 'Luma UI',
  description: 'Adaptive GTK and libadwaita application framework for Luma',
  filebase: 'luma-ui-1',
  subdirs: 'luma-1/luma-ui',
  requires: ['gtk4', 'libadwaita-1', 'luma-semantics-1'],
)

ui_gir = gnome.generate_gir(
  ui_lib,
  sources: ui_sources + ui_headers + ui_part_gir_headers,
  namespace: 'LumaUI',
  nsversion: '1',
  identifier_prefix: 'Luma',
  symbol_prefix: 'luma',
  includes: ['Gtk-4.0', 'Adw-1', semantics_gir[0]],
  header: 'luma-ui.h',
  dependencies: semantics_dep,
  install: true,
)
