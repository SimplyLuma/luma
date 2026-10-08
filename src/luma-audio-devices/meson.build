# SPDX-License-Identifier: MPL-2.0
project('luma-audio-devices', version: '0.1.0', meson_version: '>= 1.3.0', license: 'MPL-2.0')

python = import('python').find_installation('python3')
prefix = get_option('prefix')
datadir = prefix / get_option('datadir')

python.install_sources(
  'luma_audio_devices/__init__.py',
  'luma_audio_devices/airplay.py',
  'luma_audio_devices/avahi.py',
  'luma_audio_devices/classify.py',
  'luma_audio_devices/cli.py',
  'luma_audio_devices/graph.py',
  'luma_audio_devices/migration.py',
  'luma_audio_devices/pipewire.py',
  'luma_audio_devices/raop.py',
  'luma_audio_devices/routing.py',
  'luma_audio_devices/secrets.py',
  'luma_audio_devices/service.py',
  'luma_audio_devices/state.py',
  subdir: 'luma_audio_devices',
)

install_data('bin/luma-audio-devices', install_dir: get_option('libexecdir'), install_mode: 'rwxr-xr-x')

# System policy: PipeWire and WirePlumber fragments (vendor directories, so
# /etc and ~/.config keep precedence).
install_data('data/pipewire/40-luma-network-audio-opt-in.conf',
             install_dir: datadir / 'pipewire' / 'pipewire.conf.d')
install_data('data/wireplumber/60-luma-audio-policy.conf',
             install_dir: datadir / 'wireplumber' / 'wireplumber.conf.d')
install_data('data/wireplumber/scripts/lib/luma-output-policy.lua',
             install_dir: datadir / 'wireplumber' / 'scripts' / 'lib')
install_data('data/wireplumber/scripts/luma/output-policy.lua',
             install_dir: datadir / 'wireplumber' / 'scripts' / 'luma')

# Session service
install_data('data/systemd/luma-audio-devices.service', install_dir: prefix / 'lib' / 'systemd' / 'user')
install_data('data/dbus/org.projectluma.AudioDevices.service', install_dir: datadir / 'dbus-1' / 'services')
install_data('data/dbus/org.projectluma.AudioDevices1.xml', install_dir: datadir / 'dbus-1' / 'interfaces')
install_data('data/applications/org.projectluma.AudioDevices.desktop', install_dir: datadir / 'applications')
install_data('data/glib-2.0/org.projectluma.audio-devices.gschema.xml',
             install_dir: datadir / 'glib-2.0' / 'schemas')
install_data('data/icons/luma-network-speaker-symbolic.svg',
             install_dir: datadir / 'icons' / 'hicolor' / 'scalable' / 'status')
