semantics_test = executable(
  'test-luma-semantics',
  'test-semantics.c',
  dependencies: semantics_dep,
)
test('semantics', semantics_test)

background_agent_test = executable(
  'test-luma-background-agent',
  'test-background-agent.c',
  dependencies: semantics_dep,
)
test('background-agent', background_agent_test, env: ['G_DEBUG=fatal-criticals'])

context_test = executable(
  'test-luma-context',
  'test-context.c',
  dependencies: ui_dep,
)
test('context', context_test)

media_fader_test = executable(
  'test-luma-media-fader',
  'test-media-fader.c',
  dependencies: ui_dep,
)
test('media-fader', media_fader_test, env: ['G_DEBUG=fatal-criticals'])

media_transport_lcd_test = executable(
  'test-luma-media-transport-lcd',
  'test-media-transport-lcd.c',
  dependencies: ui_dep,
)
test('media-transport-lcd', media_transport_lcd_test, env: ['G_DEBUG=fatal-criticals'])

c1_test = executable('test-lumaui-c1', 'test-lumaui-c1.c', dependencies: ui_dep)
test('lumaui-c1', c1_test, env: ['G_DEBUG=fatal-criticals', 'GSETTINGS_BACKEND=memory'])
timeline_test = executable('test-luma-timeline', 'test-timeline.c', dependencies: ui_dep)
test('timeline', timeline_test, env: ['G_DEBUG=fatal-criticals'])

scaffold_test = executable('test-luma-scaffold', 'test-scaffold.c', dependencies: ui_dep)
test('scaffold', scaffold_test, env: ['G_DEBUG=fatal-criticals'])

application_icon_test = executable('test-luma-application-icon', 'test-application-icon.c', dependencies: ui_dep)
test('application-icon', application_icon_test, env: ['G_DEBUG=fatal-criticals'])

window_frame_test = executable(
  'test-luma-window-frame', 'test-window-frame.c',
  dependencies: meson.get_compiler('c').find_library('m'),
)
test('window-frame', window_frame_test)

welcome_test = executable('test-luma-welcome', 'test-welcome-view.c', dependencies: ui_dep)
test('welcome', welcome_test, env: ['G_DEBUG=fatal-criticals'])

application_menu_test = executable('test-luma-application-menu', 'test-application-menu.c', dependencies: ui_dep)
test('application-menu', application_menu_test, env: ['G_DEBUG=fatal-criticals'])

native_app_updates_test = executable('test-native-app-updates', 'test-native-app-updates.c', dependencies: ui_dep)
test('native-app-updates', native_app_updates_test,
  env: ['G_DEBUG=fatal-criticals', 'GSETTINGS_BACKEND=memory'], timeout: 90)

empty_state_test = executable('test-luma-empty-state', 'test-empty-state.c', dependencies: ui_dep)
test('empty-state', empty_state_test, env: ['G_DEBUG=fatal-criticals'])

save_sheet_test = executable('test-luma-save-sheet', 'test-save-sheet.c', dependencies: ui_dep)
test('save-sheet', save_sheet_test, env: ['G_DEBUG=fatal-criticals'])

reorder_hint_test = executable('test-luma-reorder-hint', 'test-reorder-hint.c', dependencies: ui_dep)
test('reorder-hint', reorder_hint_test, env: ['G_DEBUG=fatal-criticals'])

subdir('appearance-schemas')
appearance_test = executable('test-luma-surface-policy', 'test-surface-policy.c',
  dependencies: appearance_dep)
test('surface-policy', appearance_test, depends: appearance_test_schemas,
  env: ['G_DEBUG=fatal-criticals', 'GSETTINGS_BACKEND=memory',
        'GSETTINGS_SCHEMA_DIR=' + appearance_test_schema_dir])

surface_backdrop_test = executable(
  'test-luma-surface-backdrop',
  'test-surface-backdrop.c',
  dependencies: ui_dep,
)
test(
  'surface-backdrop',
  surface_backdrop_test,
  env: ['G_DEBUG=fatal-criticals', 'GSETTINGS_BACKEND=memory'],
)

# LumaUI parts (ADR-052): a part's C test joins once it exists, and the parity
# test compares every C part's widget tree with its Python twin's.
fs = import('fs')
lumaui_part_tests = [
  'ui-kit', 'media-lcd-controls', 'layer-host', 'mode-switch', 'corner-pill', 'sidebar-foot', 'sidebar-toggle',
  'badges', 'toast', 'destructive-dialog', 'stacked-button', 'details-pane', 'table-header',
  'action-center', 'cards', 'file-card', 'selection-bubble', 'navigation-trail-bar',
  'menu-drawer', 'contact', 'text-field', 'place-search', 'message-bubble', 'type-label',
  'creative-workspace', 'creative-tools', 'creative-layers', 'creative-properties', 'controls', 'progress-line', 'title-island', 'list-first', 'tab-bar',
]
foreach stem : lumaui_part_tests
  if fs.exists('test-lumaui-' + stem + '.c')
    exe = executable('test-lumaui-' + stem, 'test-lumaui-' + stem + '.c', dependencies: ui_dep)
    test('lumaui-' + stem, exe, env: ['G_DEBUG=fatal-criticals', 'GSETTINGS_BACKEND=memory'])
  endif
endforeach

python3 = find_program('python3', required: false)
if python3.found()
  test('mobile-identity', python3,
    args: [meson.current_source_dir() / 'test-mobile-identity.py'],
    depends: [ui_lib, ui_gir[1]],
    env: ['PYTHONPATH=' + meson.project_source_root() / 'appkit',
          'GI_TYPELIB_PATH=' + meson.project_build_root() / 'ui' + ':' + meson.project_build_root() / 'semantics' + ':' + meson.project_build_root() / 'appearance',
          'LD_LIBRARY_PATH=' + meson.project_build_root() / 'ui' + ':' + meson.project_build_root() / 'semantics' + ':' + meson.project_build_root() / 'appearance'])
  test('hidden-width-watch', python3,
    args: [meson.current_source_dir() / 'test-hidden-width-watch.py'],
    depends: [ui_lib, ui_gir[1]],
    env: ['PYTHONPATH=' + meson.project_source_root() / 'appkit',
          'GI_TYPELIB_PATH=' + meson.project_build_root() / 'ui' + ':' + meson.project_build_root() / 'semantics' + ':' + meson.project_build_root() / 'appearance',
          'LD_LIBRARY_PATH=' + meson.project_build_root() / 'ui' + ':' + meson.project_build_root() / 'semantics' + ':' + meson.project_build_root() / 'appearance'])
  test('lumaui-tokens', python3, args: [meson.project_source_root() / 'ui/gen-lumaui-tokens.py', '--check'])
  test('lumaui-c-parity', python3,
    args: [meson.current_source_dir() / 'lumaui-c-parity.py'],
    depends: [ui_lib, ui_gir[1]],
    # Both twins resolve the same bundled assets, independent of the builder's active icon theme.
    env: ['G_DEBUG=fatal-criticals', 'GSETTINGS_BACKEND=memory',
          'LUMA_APPKIT_ICON_PATH=' + meson.project_source_root() / 'appkit/luma_appkit/icons',
          'GI_TYPELIB_PATH=' + meson.project_build_root() / 'ui' + ':' + meson.project_build_root() / 'semantics' + ':' + meson.project_build_root() / 'appearance',
          'LD_LIBRARY_PATH=' + meson.project_build_root() / 'ui' + ':' + meson.project_build_root() / 'semantics' + ':' + meson.project_build_root() / 'appearance'],
    timeout: 300)
endif
