# SPDX-License-Identifier: Apache-2.0
project('luma-setup', version: '0.2.0', meson_version: '>= 1.3.0', license: 'Apache-2.0')

python = import('python').find_installation('python3')
prefix = get_option('prefix')
datadir = prefix / get_option('datadir')
libexecdir = prefix / get_option('libexecdir')
sysconfdir = get_option('sysconfdir')
userunitdir = prefix / 'lib' / 'systemd' / 'user'

python.install_sources(
  'luma_setup/__init__.py',
  'luma_setup/__main__.py',
  'luma_setup/app.py',
  'luma_setup/apply.py',
  'luma_setup/catalog.py',
  'luma_setup/connect.py',
  'luma_setup/handover.py',
  'luma_setup/model.py',
  'luma_setup/session.py',
  'luma_setup/sheet.py',
  'luma_setup/sheet_tv.py',
  'luma_setup/system.py',
  'luma_setup/welcome.py',
  'luma_setup/setup.css',
  subdir: 'luma_setup',
)

install_data('bin/luma-setup', 'bin/luma-setup-apply', install_dir: get_option('libexecdir'),
             install_mode: 'rwxr-xr-x')

# GDM's initial-setup session: GDM looks for these names and runs Luma's setup in it.
# GDM's own gnome-initial-setup.desktop starts `gnome-session --session gnome-initial-setup`; gnome-session
# reads /etc/xdg before /usr/share, so this session (and the dconf profile GDM names) are Luma's
# without shipping a file gdm or gnome-initial-setup owns.
install_data('data/session/gnome-initial-setup.session', install_dir: sysconfdir / 'xdg' / 'gnome-session' / 'sessions')
install_data('data/systemd/luma-setup.conf',
             install_dir: userunitdir / 'gnome-session@gnome-initial-setup.target.d')
install_data('data/systemd/luma-setup.service', 'data/systemd/luma-setup-first-session.service',
             'data/systemd/luma-setup-first-desktop.service', 'data/systemd/luma-setup-cover.service',
             install_dir: userunitdir)

# The session's rights, and the root helper's.
install_data('data/polkit/org.projectluma.setup.policy', install_dir: datadir / 'polkit-1' / 'actions')
install_data('data/polkit/20-luma-setup.rules', install_dir: datadir / 'polkit-1' / 'rules.d')
install_data('data/sysusers.conf', rename: 'luma-setup.conf', install_dir: prefix / 'lib' / 'sysusers.d')

# The session's settings: a dconf profile over Luma's defaults.
install_data('data/dconf/gnome-initial-setup', install_dir: sysconfdir / 'dconf' / 'profile')
dconf = find_program('dconf')
custom_target('dconf-defaults',
  output: 'dconf-defaults',
  input: 'data/dconf/defaults.d/00-luma-setup',
  command: [dconf, 'compile', '@OUTPUT@', meson.current_source_dir() / 'data' / 'dconf' / 'defaults.d'],
  install: true,
  install_dir: datadir / 'luma-setup')

install_data('data/geoclue/90-luma-setup.conf', install_dir: sysconfdir / 'geoclue' / 'conf.d')
install_data('data/org.projectluma.Setup.desktop', install_dir: datadir / 'applications')
