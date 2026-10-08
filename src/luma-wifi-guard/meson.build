project('luma-wifi-guard', version: '0.1.0', meson_version: '>= 1.3.0')
python = import('python').find_installation('python3')
python.install_sources('luma_wifi_guard/__init__.py', 'luma_wifi_guard/guard.py', 'luma_wifi_guard/policy.py',
                       subdir: 'luma_wifi_guard')
install_data('data/luma-wifi-guard.service', install_dir: get_option('prefix') / 'lib/systemd/system')
install_data('data/luma-wifi-guard.in', install_dir: get_option('libexecdir'), rename: 'luma-wifi-guard',
             install_mode: 'rwxr-xr-x')
