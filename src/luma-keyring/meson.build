# SPDX-License-Identifier: MPL-2.0
project('luma-keyring', version: '0.1.0', meson_version: '>= 1.3.0', license: 'MPL-2.0')

python = import('python').find_installation('python3')
prefix = get_option('prefix')
datadir = prefix / get_option('datadir')
sysconfdir = get_option('sysconfdir')

python.install_sources(
  'luma_keyring/__init__.py',
  'luma_keyring/autounlock.py',
  'luma_keyring/cli.py',
  'luma_keyring/login.py',
  'luma_keyring/repair.py',
  'luma_keyring/secrets.py',
  'luma_keyring/service.py',
  'luma_keyring/state.py',
  'luma_keyring/window.py',
  subdir: 'luma_keyring',
)

install_data('bin/luma-keyring', install_dir: get_option('libexecdir'), install_mode: 'rwxr-xr-x')
# Adds or removes the password-change hook in the passwd PAM stack.
install_data('data/pam/luma-keyring-pam-setup', install_dir: get_option('libexecdir'),
             install_mode: 'rwxr-xr-x')
# Recovery that needs nothing but python3-cryptography, for an older system.
install_data('tools/luma-keyring-recover-standalone', install_dir: datadir / 'luma-keyring',
             install_mode: 'rwxr-xr-x')

install_data('data/systemd/luma-keyring.service', 'data/systemd/luma-keyring-autologin.service',
             install_dir: prefix / 'lib' / 'systemd' / 'user')
install_data('data/applications/org.projectluma.Keyring.desktop', install_dir: datadir / 'applications')
install_data('data/glib-2.0/org.projectluma.keyring.gschema.xml',
             install_dir: datadir / 'glib-2.0' / 'schemas')
# Checking a person's own login password needs a stack of its own.
install_data('data/pam/luma-keyring', install_dir: sysconfdir / 'pam.d')
