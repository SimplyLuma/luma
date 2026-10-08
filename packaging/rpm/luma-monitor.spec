# SPDX-License-Identifier: Apache-2.0
#
# Built by scripts/packages/build-luma-python-app.sh luma-monitor (or its
# build-luma-monitor.sh wrapper). The tarball mirrors the repository: the app
# is src/luma-monitor, and tests/fixtures/monitor-v70.json is read by its tests.
#
# Adapted from work/monitor-this-machine-20260920 (1.luma.8). Release lineage:
# 1.luma.7.preview20260911 is in the pool and 1.luma.8 in incoming, so this is
# 1.luma.9. Check the pool and incoming before bumping it again.
#
# Since 1.luma.9 the app installs under %%{_datadir}/luma-monitor, like the
# other Python LumaUI apps: it finds its stylesheets beside its package, and
# in site-packages that path did not exist.
#
# License: the source files carry Apache-2.0 headers, as 1.luma.8 did; they are
# not relicensed here.
Name:           luma-monitor
Version:        0.1.0
Release:        1.luma.16.creator20261007.1%{?dist}
Summary:        Native application activity monitor for Luma
License:        Apache-2.0
URL:            https://projectluma.org/
Source0:        %{name}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  luma-application-installer >= 0.1.0-1.luma.52.creator20261007.1
BuildRequires:  python3-gobject
BuildRequires:  gtk4
BuildRequires:  libadwaita
BuildRequires:  luma-developer-platform >= 0.1.0-1.luma.91.creator20261005.1
BuildRequires:  desktop-file-utils
BuildRequires:  dbus-daemon
BuildRequires:  xorg-x11-server-Xvfb
Requires:       python3
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       luma-developer-platform >= 0.1.0-1.luma.91.creator20261005.1
# The "This machine" view reads the report luma-capability writes. Monitor
# works without it and says the machine has not been checked yet, so this is
# a recommendation rather than a dependency.
Recommends:     luma-capability
# Authenticates signed installed clients for the narrow host sampler.
Requires:       luma-application-installer >= 0.1.0-1.luma.52.creator20261007.1

%description
Application-oriented, read-only system activity with explicitly unavailable
figures when the host cannot supply a measurement, and a "This machine" view
of what this computer can do. Uses LumaUI.

%prep
%autosetup -n %{name}

%build
# Pure Python over LumaUI; nothing to compile.

%install
app=src/luma-monitor
install -d %{buildroot}%{_datadir}/%{name}/data
cp -R $app/luma_monitor %{buildroot}%{_datadir}/%{name}/
install -m 0644 $app/data/monitor.css $app/data/machine.css %{buildroot}%{_datadir}/%{name}/data/
# The path the "This machine" window falls back to when started on its own.
ln -s data/monitor.css %{buildroot}%{_datadir}/%{name}/monitor.css
install -d %{buildroot}%{_bindir}
cat >%{buildroot}%{_bindir}/io.luma.Monitor <<'EOF'
#!%{python3} -s
import sys
sys.path.insert(0, "%{_datadir}/%{name}")
from luma_monitor.application import main
raise SystemExit(main())
EOF
chmod 0755 %{buildroot}%{_bindir}/io.luma.Monitor
cat >%{buildroot}%{_bindir}/luma-monitor-host <<'EOF'
#!%{python3} -s
import sys
sys.path.insert(0, "%{_datadir}/%{name}")
from luma_monitor.host_sampler import Broker
Broker().run()
EOF
chmod 0755 %{buildroot}%{_bindir}/luma-monitor-host
install -D -m 0644 $app/data/org.projectluma.MonitorHost1.service %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.MonitorHost1.service
install -D -m 0644 $app/data/luma-monitor-host.service %{buildroot}%{_userunitdir}/luma-monitor-host.service
install -D -m 0644 $app/data/io.luma.Monitor.service %{buildroot}%{_datadir}/dbus-1/services/io.luma.Monitor.service
desktop-file-install --dir=%{buildroot}%{_datadir}/applications $app/data/io.luma.Monitor.desktop
%py_byte_compile %{python3} %{buildroot}%{_datadir}/%{name}/luma_monitor

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/io.luma.Monitor.desktop
grep -Fxq 'Exec=%{_bindir}/io.luma.Monitor' %{buildroot}%{_datadir}/dbus-1/services/io.luma.Monitor.service
test -f %{buildroot}%{_datadir}/%{name}/data/monitor.css
test -f %{buildroot}%{_datadir}/%{name}/data/machine.css
%{python3} counted-unittest.py --self-test
PYTHONPATH=$PWD/src/luma-monitor %{python3} counted-unittest.py src/luma-monitor/tests 58
# The installed tree imports as the launcher imports it, and the stylesheets
# the app resolves from its own location are there.
PYTHONPATH= %{python3} -c "
import sys
from pathlib import Path
sys.path.insert(0, '%{buildroot}%{_datadir}/%{name}')
import luma_monitor.application as a
for leaf in ('monitor.css', 'machine.css'):
    path = Path(a.__file__).resolve().parents[1] / 'data' / leaf
    assert path.is_file(), f'missing {path}'
print('Monitor imports from the installed tree and finds its stylesheets')
"
cd src/luma-monitor
dbus-run-session -- env PYTHONPATH=$PWD %{python3} tests/host_api_runtime.py
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1800x1000x24" env GSK_RENDERER=cairo GTK_A11Y=none GSETTINGS_BACKEND=memory LUMA_MONITOR_STYLE_PATH=$PWD/data/monitor.css PYTHONPATH=$PWD %{python3} tests/creator_preview_runtime.py
dbus-run-session -- xvfb-run -a env GSK_RENDERER=cairo LUMA_MONITOR_STYLE_PATH=$PWD/data/monitor.css PYTHONPATH=$PWD %{python3} tests/runtime_smoke.py
dbus-run-session -- xvfb-run -a env GSK_RENDERER=cairo LUMA_MONITOR_STYLE_PATH=$PWD/data/monitor.css PYTHONPATH=$PWD %{python3} tests/responsive_smoke.py
dbus-run-session -- xvfb-run -a env GSK_RENDERER=cairo LUMA_MONITOR_STYLE_PATH=$PWD/data/monitor.css PYTHONPATH=$PWD %{python3} tests/machine_runtime.py

%files
%license LICENSE.md
%{_bindir}/io.luma.Monitor
%{_bindir}/luma-monitor-host
%{_userunitdir}/luma-monitor-host.service
%{_datadir}/%{name}/
%{_datadir}/applications/io.luma.Monitor.desktop
%{_datadir}/dbus-1/services/io.luma.Monitor.service
%{_datadir}/dbus-1/services/org.projectluma.MonitorHost1.service

%changelog
* Wed Oct 07 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.16.creator20261007.1
- Supply the signed application host sampler and bounded process identity API
- Qualify actual host D-Bus controls and the responsive production UI

* Tue Oct 06 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.16.creator20261007.1
- Start the first asynchronous sample when the window appears
- Remove the redundant This computer sidebar heading

* Mon Oct 05 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.14.creator20261005.1
- Supply canonical glyphs for reachable This machine application-menu actions
- Verify all menu icon names through actual GTK theme lookup

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.12.creator20261004.1
- Monitor: force-quit confirmation and the latest LumaUI port.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.10
- LumaUI port update: the app follows the latest approved design.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.9
- Built from the LumaUI port of Monitor. Installs under /usr/share/luma-monitor
  so the app finds its stylesheets, including the "This machine" one.

* Sun Sep 20 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.8
- "This machine" view from the capability report; follows the appearance.
