project('luma-tide', version: '0.1.0', meson_version: '>= 1.3.0')

python = import('python').find_installation('python3')

python.install_sources(
  'luma_tide/__init__.py',
  'luma_tide/_navigation.py',
  'luma_tide/__main__.py',
  'luma_tide/application.py',
  'luma_tide/identity.py',
  'luma_tide/resources.py',
  'luma_tide/preview.py',
  'luma_tide/presentation.py',
  'luma_tide/fixture.py',
  'luma_tide/fixture_application.py',
  'luma_tide/live.py',
  'luma_tide/recency.py',
  'luma_tide/deferred.py',
  'luma_tide/ui.py',
  'luma_tide/ui_parts.py',
  'luma_tide/ui_phone.py',
  'luma_tide/credentials.py',
  'luma_tide/credit_markup.py',
  'luma_tide/indexer.py',
  'luma_tide/metadata.py',
  'luma_tide/model.py',
  'luma_tide/mpris.py',
  'luma_tide/playback.py',
  'luma_tide/preview_import.py',
  'luma_tide/remote.py',
  'luma_tide/retire_overrides.py',
  'luma_tide/retire_overrides_main.py',
  'luma_tide/startup.py',
  'luma_tide/subsonic.py',
  subdir: 'luma_tide',
)

install_data(
  'data/org.projectluma.Tide.desktop',
  install_dir: get_option('datadir') / 'applications',
)
# The activation file names the installed binary, so it follows the prefix:
# /usr for the operating system's package, /app in a Flatpak.
configure_file(
  input: 'data/org.projectluma.Tide.service.in',
  output: 'org.projectluma.Tide.service',
  configuration: {'bindir': get_option('prefix') / get_option('bindir')},
  install: true,
  install_dir: get_option('datadir') / 'dbus-1' / 'services',
)
install_data(
  'data/org.projectluma.Tide.metainfo.xml',
  install_dir: get_option('datadir') / 'metainfo',
)
install_data(
  'data/org.projectluma.Tide.svg',
  install_dir: get_option('datadir') / 'icons/hicolor/scalable/apps',
)
install_data(
  'data/tide.css',
  install_dir: get_option('datadir') / 'luma-tide',
)

configure_file(
  input: 'data/org.projectluma.Tide.in',
  output: 'org.projectluma.Tide',
  copy: true,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

# Retires a stale dev-preview launch override (see retire_overrides.py's
# docstring) before the session bus or GNOME Shell can read it.
install_data(
  'bin/luma-tide-retire-preview-overrides',
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)
userunitdir = get_option('prefix') / 'lib' / 'systemd' / 'user'
install_data(
  'data/luma-tide-retire-preview-overrides.service',
  install_dir: userunitdir,
)
install_symlink(
  'luma-tide-retire-preview-overrides.service',
  pointing_to: '../luma-tide-retire-preview-overrides.service',
  install_dir: userunitdir / 'graphical-session-pre.target.wants',
)
