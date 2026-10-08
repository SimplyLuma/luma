project('luma-ari', version: '0.1.0', meson_version: '>= 1.3.0')

python = import('python').find_installation('python3')

python.install_sources(
  'ari/__init__.py', 'ari/agent.py', 'ari/audit.py', 'ari/cli.py', 'ari/cloud.py', 'ari/hardware.py', 'ari/intents.py',
  'ari/mcp.py', 'ari/models.py', 'ari/paths.py', 'ari/persona.py', 'ari/policy.py', 'ari/providers.py',
  'ari/host_identity.py', 'ari/host_limits.py', 'ari/router.py', 'ari/persistence.py', 'ari/runtime.py', 'ari/service.py', 'ari/store.py',
  subdir: 'ari',
)
python.install_sources('ari/tools/__init__.py', 'ari/tools/answers.py', 'ari/tools/desktop.py',
                        'ari/tools/preferences.py', 'ari/tools/system.py', subdir: 'ari/tools')
python.install_sources('ari_ui/__init__.py', 'ari_ui/app.py', 'ari_ui/settings.py', 'ari_ui/fixture.py', 'ari_ui/lumaui_app.py',
                       'ari_ui/lumaui_style.py', 'ari_ui/live_data.py', subdir: 'ari_ui')

install_subdir('ari_ui/assets', install_dir: python.get_install_dir() / 'ari_ui')

datadir = get_option('datadir')
install_data('data/models.json', 'data/cloud-models.json', 'data/ari.css', install_dir: datadir / 'luma-ari')
install_subdir('eval/results', install_dir: datadir / 'luma-ari/eval', strip_directory: true)
install_data('data/org.projectluma.Ari.desktop', install_dir: datadir / 'applications')
install_data('data/org.projectluma.Ari.metainfo.xml', install_dir: datadir / 'metainfo')
install_data('data/org.projectluma.Ari.gschema.xml', install_dir: datadir / 'glib-2.0/schemas')
install_data('data/org.projectluma.Ari.svg', install_dir: datadir / 'icons/hicolor/scalable/apps')
install_data('data/org.projectluma.Ari1.service', 'data/org.projectluma.Ari.service',
             install_dir: datadir / 'dbus-1/services')
install_data('data/ari-daemon.service', install_dir: get_option('prefix') / 'lib/systemd/user')

foreach script : [['data/ari-daemon.in', 'ari-daemon', get_option('libexecdir') / 'luma-ari'],
                  ['data/ari.in', 'ari', get_option('bindir')],
                  ['data/org.projectluma.Ari.in', 'org.projectluma.Ari', get_option('bindir')]]
  configure_file(input: script[0], output: script[1], copy: true, install: true,
                 install_dir: script[2], install_mode: 'rwxr-xr-x')
endforeach
