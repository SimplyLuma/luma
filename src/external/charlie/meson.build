project('charlie-luma', version: '0.2.14', meson_version: '>= 1.3.0')

python = import('python').find_installation('python3')

python.install_sources(
  'charlie_luma/__init__.py',
  'charlie_luma/__main__.py',
  'charlie_luma/accounts.py',
  'charlie_luma/account_ui.py',
  'charlie_luma/application.py',
  'charlie_luma/avatar.py',
  'charlie_luma/background.py',
  'charlie_luma/engine.py',
  'charlie_luma/fixtures.py',
  'charlie_luma/fixture_v71.py',
  'charlie_luma/html_reader.py',
  'charlie_luma/mail_agent.py',
  'charlie_luma/host_mail_wire.py',
  'charlie_luma/host_mail.py',
  'charlie_luma/host_mail_owner.py',
  'charlie_luma/account_lifecycle.py',
  'charlie_luma/host_mail_client.py',
  'charlie_luma/mime.py',
  'charlie_luma/model.py',
  'charlie_luma/oauth.py',
  'charlie_luma/secrets.py',
  'charlie_luma/search_provider.py',
  'charlie_luma/store.py',
  'charlie_luma/time_display.py',
  'charlie_luma/window.py',
  subdir: 'charlie_luma',
)

configure_file(
  input: 'data/org.projectluma.Charlie.in',
  output: 'org.projectluma.Charlie',
  copy: true,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

install_data('data/org.projectluma.Charlie.desktop', install_dir: get_option('datadir') / 'applications')
install_data('data/org.projectluma.Charlie.metainfo.xml', install_dir: get_option('datadir') / 'metainfo')
install_data('data/org.projectluma.Charlie.service', install_dir: get_option('datadir') / 'dbus-1/services')
if get_option('prefix') != '/app'
  install_data('data/org.projectluma.MailHost1.service', install_dir: get_option('datadir') / 'dbus-1/services')
endif
install_data('data/background/org.projectluma.Charlie.toml', install_dir: get_option('datadir') / 'luma/background')
install_data('data/org.projectluma.Charlie.Agent.desktop', install_dir: get_option('sysconfdir') / 'xdg/autostart')
install_data('data/org.projectluma.Charlie.search-provider.ini', install_dir: get_option('datadir') / 'gnome-shell/search-providers')
install_data('data/charlie.css', install_dir: get_option('datadir') / 'charlie')

# One source owns every exported size. PNGs are rendered during the normal
# build, avoiding an older raster provider winning over the canonical SVG.
icon_svg = files('data/icons/hicolor/scalable/apps/org.projectluma.Charlie.svg')
rsvg_converter = find_program('rsvg-convert')
foreach size : ['16x16', '24x24', '32x32', '48x48', '64x64', '128x128', '256x256', '512x512']
  subdir('data/icons/hicolor' / size / 'apps')
endforeach
install_data(icon_svg, install_dir: get_option('datadir') / 'icons/hicolor/scalable/apps')
