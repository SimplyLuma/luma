project('luma-media-bridge', version: '0.1.0', meson_version: '>= 1.3.0')
python = import('python').find_installation('python3')
python.install_sources('luma_media_bridge/__init__.py', 'luma_media_bridge/kodi.py', 'luma_media_bridge/mpris.py',
                       'luma_media_bridge/service.py', subdir: 'luma_media_bridge')
install_data('data/luma-media-bridge.service', install_dir: get_option('prefix') / 'lib/systemd/user')
install_data('data/luma-media-bridge.in', install_dir: get_option('libexecdir'), rename: 'luma-media-bridge',
             install_mode: 'rwxr-xr-x')
