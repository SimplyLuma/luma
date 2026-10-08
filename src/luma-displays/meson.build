project('luma-displays', version: '0.1.0', meson_version: '>= 1.3.0')

python = import('python').find_installation('python3')

python.install_sources(
  'luma_displays/__init__.py',
  'luma_displays/__main__.py',
  'luma_displays/application.py',
  'luma_displays/canvas.py',
  'luma_displays/displayconfig.py',
  'luma_displays/model.py',
  'luma_displays/names.py',
  'luma_displays/settings.py',
  subdir: 'luma_displays',
)

install_data('data/org.projectluma.Displays.desktop', install_dir: get_option('datadir') / 'applications')
install_data('data/org.projectluma.Displays.service', install_dir: get_option('datadir') / 'dbus-1' / 'services')
install_data('data/org.projectluma.Displays.metainfo.xml', install_dir: get_option('datadir') / 'metainfo')
install_data('data/org.projectluma.Displays.svg', install_dir: get_option('datadir') / 'icons/hicolor/scalable/apps')

configure_file(
  input: 'data/org.projectluma.Displays.in',
  output: 'org.projectluma.Displays',
  copy: true,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)
