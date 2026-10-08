project('luma-vitals', version: '0.1.0', meson_version: '>= 1.3.0')
python = import('python').find_installation('python3')
python.install_sources('luma_vitals/__init__.py', 'luma_vitals/agents.py', 'luma_vitals/battery_cli.py',
                       'luma_vitals/cli.py', 'luma_vitals/crashes.py', 'luma_vitals/detectors.py',
                       'luma_vitals/power.py', 'luma_vitals/memory_feedback.py',
                       'luma_vitals/sampler.py', 'luma_vitals/service.py', 'luma_vitals/store.py',
                       subdir: 'luma_vitals')
install_data('data/luma-vitals.service', install_dir: get_option('prefix') / 'lib/systemd/user')
install_data('data/luma-vitals-daemon.in', install_dir: get_option('libexecdir'), rename: 'luma-vitals',
             install_mode: 'rwxr-xr-x')
install_data('data/luma-vitals.in', install_dir: get_option('bindir'), rename: 'luma-vitals',
             install_mode: 'rwxr-xr-x')

# Energy counters (ADR-026 §8): system-wide, packaged as luma-vitals-energy.
# RAPL is root-only since CVE-2020-8694; this publishes ten-second averages,
# which carry no side channel, where a person's session can read them.
install_data('energy/luma-vitals-energy', install_dir: get_option('libexecdir'),
             install_mode: 'rwxr-xr-x')
install_data('energy/luma-vitals-energy.service',
             install_dir: get_option('prefix') / 'lib/systemd/system')

# Power guard (ADR-046 §5): system-wide, packaged as luma-vitals-power-guard.
# Steps Performance down to Balanced on battery and puts the person's choice
# back when power returns, through the power-profiles D-Bus API only.
install_data('power-guard/luma-vitals-power-guard', install_dir: get_option('libexecdir'),
             install_mode: 'rwxr-xr-x')
install_data('power-guard/luma-vitals-power-guard.service',
             install_dir: get_option('prefix') / 'lib/systemd/system')

# Crash evidence (ADR-026 §6): system-wide, packaged as luma-vitals-crash-evidence.
prefix_lib = get_option('prefix') / 'lib'
install_data('crash-evidence/luma-crash-evidence', install_dir: get_option('libexecdir'),
             install_mode: 'rwxr-xr-x')
install_data('crash-evidence/luma-crash-evidence-intel', install_dir: get_option('libexecdir'),
             install_mode: 'rwxr-xr-x')
install_data('crash-evidence/luma-crash-evidence.service', install_dir: prefix_lib / 'systemd/system')
install_data('crash-evidence/luma-crash-evidence.catalog', install_dir: prefix_lib / 'systemd/catalog')
install_data('crash-evidence/60-luma-crash-evidence-journald.conf', install_dir: prefix_lib / 'systemd/journald.conf.d',
             rename: '60-luma-crash-evidence.conf')
install_data('crash-evidence/60-luma-crash-evidence-sysctl.conf', install_dir: prefix_lib / 'sysctl.d',
             rename: '60-luma-crash-evidence.conf')
install_data('crash-evidence/60-luma-crash-evidence-kargs.toml', install_dir: prefix_lib / 'bootc/kargs.d',
             rename: '60-luma-crash-evidence.toml')

# Memory guard (ADR-026 §7): system-wide, packaged as luma-vitals-memory-guard.
foreach slice : ['app.slice', 'luma-background.slice']
  install_data('memory-guard/60-luma-oomd-swap.conf', install_dir: prefix_lib / 'systemd/user' / (slice + '.d'))
endforeach
install_data('memory-guard/60-luma-zram-zstd.conf', install_dir: prefix_lib / 'systemd/zram-generator.conf.d')
install_data('memory-guard/60-luma-compositor-memory.conf',
             install_dir: prefix_lib / 'systemd/user/org.gnome.Shell@.service.d')
