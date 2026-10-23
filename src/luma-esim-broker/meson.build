# SPDX-License-Identifier: Apache-2.0
project('luma-esim', 'c', version: '0.1.0', default_options: ['warning_level=3', 'werror=true'])
gio = dependency('gio-unix-2.0', version: '>=2.80')
json = dependency('json-glib-1.0', version: '>=1.8')
production_test_args = ['-ULUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY', '-ULUMA_NATIVE_ESIM_PRODUCTION_OWNER']
core = ['profile-store.c', 'profile-cache.c', 'lpac-client.c', 'native-terminal-client.c']
executable('luma-esim-service', ['service.c'] + core, dependencies: [gio, json], install: true, install_dir: get_option('libexecdir'))
profile_test = executable('test-profile-store', ['test-profile-store.c', 'profile-store.c'], dependencies: [gio, json], c_args: production_test_args)
rules_test = executable('test-service-rules', ['test-service-rules.c'] + core, dependencies: [gio, json], c_args: production_test_args)
test('profile-boundaries', profile_test)
test('operation-and-authorization-rules', rules_test)
notifications_test = executable('test-notifications', ['test-notifications.c', 'profile-store.c', 'native-terminal-client.c'], dependencies: [gio, json], c_args: production_test_args)
test('carrier-notification-command-and-sequence-boundaries', notifications_test)
install_data('org.projectluma.ESim1.xml', install_dir: get_option('datadir') / 'luma-esim')
install_data('data/org.projectluma.esim.policy', install_dir: get_option('datadir') / 'polkit-1/actions')
install_data('data/org.projectluma.ESim1.conf', install_dir: get_option('datadir') / 'dbus-1/system.d')
install_data('data/org.projectluma.ESim1.service', install_dir: get_option('datadir') / 'dbus-1/system-services')
install_data('data/luma-esim.service', install_dir: '/usr/lib/systemd/system')
install_data('data/luma_esim.cil', install_dir: get_option('datadir') / 'selinux/packages')

cache_test = executable('test-profile-cache', ['test-profile-cache.c', 'profile-cache.c', 'profile-store.c'], dependencies: [gio, json], c_args: production_test_args)
test('per-sender-snapshot-lifetimes', cache_test)

private_bus = find_program('dbus-run-session')
lifecycle_test = executable('test-service-lifecycle', ['test-service-lifecycle.c', 'profile-store.c', 'profile-cache.c', 'lpac-client.c', 'native-terminal-client.c'], dependencies: [gio, json], c_args: production_test_args + ['-DESIM_TEST_XML="' + meson.current_source_dir() / 'org.projectluma.ESim1.xml' + '"'])
test('private-bus-service-lifecycle', private_bus, args: ['--config-file=' + meson.current_source_dir() / 'test-private-bus.conf', '--', lifecycle_test], timeout: 30)

# Explicit nonshipping candidate mode retains the unchanged production matrix.
# Actual service C is compiled in each mode; controlled card/bus fixtures make
# refusal observable without using an eUICC or any activation secret.
candidate_lifecycle = executable('test-service-candidate-lifecycle', ['test-service-candidate-lifecycle.c'] + core, dependencies: [gio, json], c_args: ['-ULUMA_NATIVE_ESIM_PRODUCTION_OWNER', '-DLUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY', '-DESIM_TEST_XML="' + meson.current_source_dir() / 'org.projectluma.ESim1.xml' + '"'])
test('private-bus-candidate-operation-refusals', private_bus, args: ['--config-file=' + meson.current_source_dir() / 'test-private-bus.conf', '--', candidate_lifecycle], timeout: 30)

# Exact source-owned candidate authority predicates, with controlled facts.
bound_enable = executable('test-bound-enable', ['test-bound-enable.c', 'profile-store.c', 'profile-cache.c'], dependencies: [gio, json], c_args: ['-ULUMA_NATIVE_ESIM_PRODUCTION_OWNER'])
test('candidate-native-binding-and-single-use-enable', bound_enable)
native_evidence = executable('test-native-evidence', 'test-native-evidence.c', dependencies: [gio], c_args: ['-ULUMA_NATIVE_ESIM_PRODUCTION_OWNER'])
test('candidate-SIM-client-epoch-invalidation', native_evidence)
queued_open = executable('test-queued-open-guard', 'test-queued-open-guard.c', dependencies: [gio], c_args: ['-ULUMA_NATIVE_ESIM_PRODUCTION_OWNER'])
test('candidate-queued-OPEN-epoch-client-refusal', queued_open)
terminal_registry = executable('test-native-terminal-registry', 'test-native-terminal-registry.c', dependencies: [gio], c_args: ['-ULUMA_NATIVE_ESIM_PRODUCTION_OWNER'])
test('candidate-terminal-handle-generation-refusal', terminal_registry)

# Lost-OPEN diagnostic recovery is unavailable before any worker/lease/QMI in
# all non-full modes. Real private bus dispatch tests the actual method gate.
foreach mode : ['default', 'readonly', 'channel']
  mode_args = production_test_args + ['-DESIM_TEST_XML="' + meson.current_source_dir() / 'org.projectluma.ESim1.xml' + '"']
  if mode == 'readonly'
    mode_args += ['-DLUMA_NATIVE_ESIM_READONLY_DIAGNOSTIC_ONLY']
  elif mode == 'channel'
    mode_args += ['-DLUMA_NATIVE_ESIM_CHANNEL_DIAGNOSTIC_ONLY']
  endif
  refusal = executable('test-recovery-method-refusal-' + mode, ['test-recovery-method-refusal.c'] + core, dependencies: [gio, json], c_args: mode_args)
  test('native-recovery-method-refuses-' + mode, private_bus, args: ['--config-file=' + meson.current_source_dir() / 'test-private-bus.conf', '--', refusal], timeout: 10)
endforeach

# Actual main() path selection and registration, beyond direct worker fixtures.
foreach startup_mode : ['default', 'candidate', 'production']
  startup_args = production_test_args + ['-DESIM_TEST_XML="' + meson.current_source_dir() / 'org.projectluma.ESim1.xml' + '"']
  if startup_mode == 'candidate'
    startup_args += ['-DLUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY']
  elif startup_mode == 'production'
    startup_args += ['-DLUMA_NATIVE_ESIM_PRODUCTION_OWNER']
  endif
  startup = executable('test-service-main-startup-' + startup_mode, ['test-service-main-startup.c'] + core, dependencies: [gio, json], c_args: startup_args)
  test('actual-main-public-XML-startup-' + startup_mode, private_bus, args: ['--config-file=' + meson.current_source_dir() / 'test-private-bus.conf', '--', startup], timeout: 15)
endforeach

production_lifecycle = executable('test-service-production-lifecycle', ['test-service-production-lifecycle.c'] + core, dependencies: [gio, json], c_args: ['-ULUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY', '-DLUMA_NATIVE_ESIM_PRODUCTION_OWNER', '-DESIM_TEST_XML="' + meson.current_source_dir() / 'org.projectluma.ESim1.xml' + '"'])
test('private-bus-production-native-binding-profile-management', private_bus, args: ['--config-file=' + meson.current_source_dir() / 'test-private-bus.conf', '--', production_lifecycle], timeout: 30)

normal_epoch = executable('test-native-operation-epoch', 'test-native-operation-epoch.c', dependencies: [gio])
test('production-native-operation-epoch', normal_epoch)
slot_refresh = executable('test-native-slot-refresh-extracted', 'test-native-slot-refresh-extracted.c', dependencies: [gio])
test('production-native-slot-refresh-constructor', slot_refresh)

# Native MMBaseSim is directly exported outside ObjectManager.
direct_sim = executable('test-native-direct-sim-discovery', 'test-native-direct-sim-discovery.c', dependencies: [gio], c_args: ['-ULUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY', '-DLUMA_NATIVE_ESIM_PRODUCTION_OWNER'])
test('native-direct-SIM-advertised-discovery', private_bus, args: ['--config-file=' + meson.current_source_dir() / 'test-private-bus.conf', '--', direct_sim], timeout: 30)

# Source-owned callback and exact-token release protocol regressions.
retained_cancel = executable('test-native-retained-owner-cancellation', 'test-native-retained-owner-cancellation.c', dependencies: [gio], c_args: production_test_args)
test('native-retained-owner-cancellation-and-lifetime', retained_cancel)
reservation_release = executable('test-native-reservation-release', 'test-native-reservation-release.c', dependencies: [gio], c_args: production_test_args)
test('native-reservation-release-proof-wait-and-owner-refusal', reservation_release)

# The same production worker on a Titan transport: private preparation must
# precede switch APDUs, and a refusal must leave the card unchanged.
titan_lifecycle = executable('test-service-titan-lifecycle', ['test-service-production-lifecycle.c'] + core, dependencies: [gio, json], c_args: ['-ULUMA_NATIVE_ESIM_FULL_LPAC_CANDIDATE_ONLY', '-DLUMA_NATIVE_ESIM_PRODUCTION_OWNER', '-DLUMA_TEST_TITAN_BACKEND', '-DESIM_TEST_XML="' + meson.current_source_dir() / 'org.projectluma.ESim1.xml' + '"'])
test('private-bus-Titan-preparation-before-profile-mutation', private_bus, args: ['--config-file=' + meson.current_source_dir() / 'test-private-bus.conf', '--', titan_lifecycle], timeout: 30)
