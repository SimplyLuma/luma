appearance_sources = files('luma-appearance.c')
appearance_headers = files('luma-appearance.h')
appearance_lib = library('luma-appearance-1', appearance_sources,
  version: '0.1.0', soversion: '0',
  dependencies: [glib_dep, gobject_dep, gio_dep], install: true)
appearance_dep = declare_dependency(link_with: appearance_lib,
  include_directories: include_directories('.'),
  dependencies: [glib_dep, gobject_dep, gio_dep])
install_headers(appearance_headers, subdir: 'luma-1/luma-appearance')
pkg.generate(appearance_lib, name: 'Luma Appearance',
  description: 'Shared native surface tokens and accessibility/capability policy',
  filebase: 'luma-appearance-1', subdirs: 'luma-1/luma-appearance',
  requires: ['glib-2.0', 'gobject-2.0', 'gio-2.0'])
appearance_gir = gnome.generate_gir(appearance_lib,
  sources: appearance_sources + appearance_headers,
  namespace: 'LumaAppearance', nsversion: '1', identifier_prefix: 'Luma',
  symbol_prefix: 'luma', includes: ['GObject-2.0', 'Gio-2.0'],
  header: 'luma-appearance.h', install: true)
