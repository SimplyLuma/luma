semantics_headers = files(
  'luma-action.h',
  'luma-background-agent.h',
  'luma-enums.h',
  'luma-live-extension.h',
  'luma-semantic-object.h',
  'luma-semantics.h',
)

semantics_sources = files(
  'luma-action.c',
  'luma-background-agent.c',
  'luma-enums.c',
  'luma-live-extension.c',
  'luma-semantic-object.c',
)

semantics_inc = include_directories('.')
semantics_lib = library(
  'luma-semantics-1',
  semantics_sources,
  version: '0.1.0',
  soversion: '0',
  dependencies: [glib_dep, gobject_dep, gio_dep],
  include_directories: semantics_inc,
  install: true,
)

semantics_dep = declare_dependency(
  link_with: semantics_lib,
  include_directories: semantics_inc,
  dependencies: [glib_dep, gobject_dep, gio_dep],
)

install_headers(semantics_headers, subdir: 'luma-1/luma-semantics')

pkg.generate(
  semantics_lib,
  name: 'Luma Semantics',
  description: 'Semantic application and Live Extension contracts for Luma',
  filebase: 'luma-semantics-1',
  subdirs: 'luma-1/luma-semantics',
  requires: ['glib-2.0', 'gobject-2.0', 'gio-2.0'],
)

semantics_gir = gnome.generate_gir(
  semantics_lib,
  sources: semantics_sources + semantics_headers,
  namespace: 'LumaSemantics',
  nsversion: '1',
  identifier_prefix: 'Luma',
  symbol_prefix: 'luma',
  includes: ['GObject-2.0', 'Gio-2.0'],
  header: 'luma-semantics.h',
  install: true,
)
