project('luma-home-widgets', 'c', version: '0.1.0', license: 'GPL-3.0-or-later',
  default_options: ['warning_level=2', 'werror=true'])
gnome = import('gnome')
gio = dependency('gio-2.0')
gio_unix = dependency('gio-unix-2.0')
json = dependency('json-glib-1.0')
math = meson.get_compiler('c').find_library('m')
resources = gnome.compile_resources('luma-home-widgets-resources', 'widgets.gresource.xml', source_dir: '.')
model = library('luma-home-widgets', 'luma-widgets-model.c', resources,
  dependencies: [gio, gio_unix, json, math], c_args: ['-DGETTEXT_PACKAGE="luma-home-widgets"'],
  version: '0.1.0', install: true)
install_headers('luma-widgets-model.h', subdir: 'luma-home-widgets')
gnome.generate_gir(model, sources: ['luma-widgets-model.c', 'luma-widgets-model.h'],
  namespace: 'LumaWidgets', nsversion: '1.0', identifier_prefix: 'LumaWidgets',
  symbol_prefix: 'luma_widgets', includes: ['GObject-2.0', 'Gio-2.0'], install: true)
pkg = import('pkgconfig')
pkg.generate(model, name: 'Luma Home Widgets', filebase: 'luma-home-widgets',
  description: 'Shared native Weather and Calendar widget model',
  subdirs: 'luma-home-widgets', requires: ['gio-2.0'])
gtk3 = dependency('gtk+-3.0', required: false)
if gtk3.found()
  view = library('luma-home-widgets-gtk3', 'luma-widget-gtk3.c',
    dependencies: [gtk3, json], link_with: model, version: '0.1.0', install: true)
  install_headers('luma-widget-gtk3.h', subdir: 'luma-home-widgets')
  pkg.generate(view, name: 'Luma Home Widgets GTK3', filebase: 'luma-home-widgets-gtk3',
    description: 'Native GTK3 host for shared Home widgets', subdirs: 'luma-home-widgets',
    requires: ['gtk+-3.0', 'luma-home-widgets'])
endif
python = find_program('python3')
bus = find_program('dbus-run-session')
test('shared-model', bus, args: ['--', python, files('tests/model.py')],
  env: ['GI_TYPELIB_PATH=' + meson.current_build_dir(),
        'LD_LIBRARY_PATH=' + meson.current_build_dir()])
if gtk3.found()
  gtk_test = executable('test-gtk3-widget', 'tests/gtk3.c', include_directories: include_directories('.'),
    dependencies: gtk3, link_with: [view, model])
  xvfb = find_program('xvfb-run', required: false)
  if xvfb.found()
    test('gtk3-view', bus, args: ['--', xvfb, '-a', gtk_test])
  endif
endif

# Weather owns provider requests and the atomic cache; this bounded user job
# keeps that same cache current even when its application window is closed.
install_data('weather-refresh.py', rename: 'luma-weather-refresh',
  install_dir: get_option('libexecdir'), install_mode: 'rwxr-xr-x')
user_units = get_option('prefix') / 'lib/systemd/user'
install_data('luma-weather-refresh.service', 'luma-weather-refresh.timer', install_dir: user_units)
install_symlink('luma-weather-refresh.timer', pointing_to: '../luma-weather-refresh.timer',
  install_dir: user_units / 'default.target.wants')
test('weather-refresh', python, args: files('tests/refresh.py'))
