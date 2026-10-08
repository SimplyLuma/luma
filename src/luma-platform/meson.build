project(
  'luma-developer-platform',
  'c',
  version: '0.1.0',
  license: 'Apache-2.0',
  meson_version: '>= 1.3.0',
  default_options: ['c_std=c17', 'warning_level=2', 'werror=true'],
)

gnome = import('gnome')
pkg = import('pkgconfig')

glib_dep = dependency('glib-2.0', version: '>= 2.80')
gobject_dep = dependency('gobject-2.0', version: '>= 2.80')
gio_dep = dependency('gio-2.0', version: '>= 2.80')
gtk_dep = dependency('gtk4', version: '>= 4.18')
adw_dep = dependency('libadwaita-1', version: '>= 1.7')
wayland_client_dep = dependency('wayland-client', version: '>= 1.23')

subdir('appearance')
subdir('semantics')
subdir('ui')
subdir('tests')

install_subdir('schemas', install_dir: get_option('datadir') / 'luma-platform')
install_subdir('protocols', install_dir: get_option('datadir') / 'luma-platform')
install_subdir('sdk/luma_sdk/templates', install_dir: get_option('datadir') / 'luma-platform')
install_data(
  'ui/luma-ui.css',
  'appkit/luma-appkit-base.css',
  'appkit/luma-appkit-high-contrast-tokens.css',
  'appkit/luma-appkit-high-contrast.css',
  'appkit/luma-appkit.css', 'appkit/luma-appkit-tokens.css',
  'appkit/luma-empty-state-tokens.css',
  'appkit/luma-appkit-dark.css', 'appkit/luma-appkit-dark-tokens.css',
  'appkit/luma-appkit-frost-tokens.css',
  'appkit/luma-appkit-frost.css',
  'appkit/luma-appkit-glass-tokens.css',
  'appkit/luma-appkit-glass.css',
  'appkit/lumaui-palette.css', 'appkit/lumaui-toolkit.css',
  install_dir: get_option('datadir') / 'luma-appkit',
)
# LumaUI family sheets (luma-appkit-<family>.css and its -dark/-frost/-glass/
# -high-contrast variants) are found on disk, the way the kit finds them, so a
# new part family needs no build change.
lumaui_family_css = []
_family_list = run_command(find_program('python3'), '-c',
  'import pathlib, re, sys\n'
  + 'core = {"base", "tokens", "dark", "frost", "glass", "high"}\n'
  + 'rx = re.compile(r"^luma-appkit-([a-z][a-z0-9_]*)(-(dark|frost|glass|high-contrast))?[.]css$")\n'
  + 'for p in sorted(pathlib.Path(sys.argv[1]).glob("luma-appkit-*.css")):\n'
  + '    m = rx.match(p.name)\n'
  + '    if m and m.group(1) not in core and not p.name.endswith("-tokens.css"): print(p.name)\n',
  meson.current_source_dir() / 'appkit', check: true)
foreach _sheet : _family_list.stdout().strip().split('\n')
  if _sheet != ''
    lumaui_family_css += 'appkit' / _sheet
  endif
endforeach
if lumaui_family_css.length() > 0
  install_data(lumaui_family_css, install_dir: get_option('datadir') / 'luma-appkit')
endif
install_subdir('appkit/fonts', install_dir: get_option('datadir') / 'luma-appkit')
install_subdir(
  'appkit/icons',
  # hicolor declares scalable/actions, not symbolic/actions.  Symbolic SVGs are
  # identified by their -symbolic name and recoloured by GTK after lookup.
  install_dir: get_option('datadir') / 'icons/hicolor/scalable/actions',
  strip_directory: true,
)
install_data(
  'broker/org.projectluma.SemanticBroker1.xml',
  install_dir: get_option('datadir') / 'dbus-1/interfaces',
)

# Native compatibility surfaces share App Kit geometry without a second UI runtime.
install_headers(
  'compat/luma-window-frame.h', 'compat/luma-frame-tokens.h', 'compat/luma-frame-glyphs.h',
  subdir: 'luma-1/compat',
)
