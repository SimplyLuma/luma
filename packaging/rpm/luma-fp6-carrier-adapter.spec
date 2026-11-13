# SPDX-License-Identifier: Apache-2.0
Name: luma-fp6-carrier-adapter
Version: 0.1.0
Release: 1.luma.13%{?dist}
Summary: Native Fairphone 6 carrier and SIM session admission
License: Apache-2.0
Source0: luma-fp6-carrier-pdc
Source1: luma-fp6-carrier-pdc.service
Source2: test_carrier_kernel.py
Source3: LICENSE.md
Source4: luma-fp6-primary-gw-session
Source5: luma-fp6-primary-gw-session.service
Source6: test_primary_gw_parser.py
Source7: test_carrier_lifecycle.py
Source8: test_stock_catalog.py
Source9: stock-platform-catalog.txt
Source10: stock-software-catalog.txt
Source11: luma-fp6-modem-store
Source12: luma-fp6-modem-store.service
Source13: luma-fp6-rmtfs
Source14: 50-luma-fp6-modem-store.conf
Source15: luma-fp6-modem-restart
Source16: luma-fp6-modem-restart.service
Source17: test_modem_store.py
Source18: fp6-fresh-install-modem-efs.md
Source19: luma-fp6-carrier-select
Source20: luma-fp6-carrier-select.service
Source21: carriers.toml
Source22: test_carrier_select.py
Source23: luma-fp6-modem-state-notify
Source24: luma-fp6-modem-state-notify.service
BuildArch: noarch
BuildRequires: bash
BuildRequires: python3-devel
BuildRequires: systemd
BuildRequires: diffutils
BuildRequires: util-linux
Requires: bash
Requires: coreutils
Requires: diffutils
Requires: python3
Recommends: mobile-broadband-provider-info
Requires: util-linux
Requires: libqmi-utils
# The DSDS platform and the AT&T and T-Mobile modem configurations it loads.
Requires: luma-fp6-firmware >= 20261002-1.luma.3
Requires: rmtfs
Requires: systemd

%description
Owns the guarded native carrier adapter and its existing system service.
No modem firmware, subscriber configuration, credentials or partition data
are included. Activation requires the existing exact model, slot, native
kernel, normal packaged qmicli, exact firmware digests before a required load,
and native rmtfs file-store policy.
The service is not enabled or restarted by installing this package.

It also owns the private modem store: on first boot the modem storage
partitions are copied once, read-only, into /var/lib/luma/rmtfs, and rmtfs
serves and persists the modem's EFS there. No modem partition is written.

%prep
mkdir -p scripts/mobile tests/mobile
cp %{SOURCE0} scripts/mobile/luma-fp6-carrier-pdc
cp %{SOURCE1} scripts/mobile/luma-fp6-carrier-pdc.service
cp %{SOURCE2} tests/mobile/test_carrier_kernel.py
cp %{SOURCE3} LICENSE.md
cp %{SOURCE4} scripts/mobile/luma-fp6-primary-gw-session
cp %{SOURCE5} scripts/mobile/luma-fp6-primary-gw-session.service
cp %{SOURCE6} tests/mobile/test_primary_gw_parser.py
cp %{SOURCE7} tests/mobile/test_carrier_lifecycle.py
cp %{SOURCE8} tests/mobile/test_stock_catalog.py
cp %{SOURCE9} tests/mobile/stock-platform-catalog.txt
cp %{SOURCE10} tests/mobile/stock-software-catalog.txt
cp %{SOURCE11} %{SOURCE12} %{SOURCE13} %{SOURCE14} %{SOURCE15} %{SOURCE16} scripts/mobile/
cp %{SOURCE17} tests/mobile/test_modem_store.py
cp %{SOURCE18} fp6-fresh-install-modem-efs.md
cp %{SOURCE19} %{SOURCE20} %{SOURCE23} %{SOURCE24} scripts/mobile/
cp %{SOURCE21} carriers.toml
cp %{SOURCE22} tests/mobile/test_carrier_select.py

%build
bash -n scripts/mobile/luma-fp6-carrier-pdc
bash -n scripts/mobile/luma-fp6-primary-gw-session
bash -n scripts/mobile/luma-fp6-modem-store
bash -n scripts/mobile/luma-fp6-rmtfs
bash -n scripts/mobile/luma-fp6-modem-restart
bash -n scripts/mobile/luma-fp6-modem-state-notify
# The notice must never hold up user@.service (and so the login): no oneshot,
# not under default.target.
grep -qx 'Type=exec' scripts/mobile/luma-fp6-modem-state-notify.service
grep -qx 'WantedBy=graphical-session.target' scripts/mobile/luma-fp6-modem-state-notify.service
! grep -v '^#' scripts/mobile/luma-fp6-modem-state-notify.service | grep -q 'default.target\|Type=oneshot'

%install
install -D -m 0755 scripts/mobile/luma-fp6-carrier-pdc %{buildroot}%{_libexecdir}/luma-fp6-carrier-pdc
install -D -m 0644 scripts/mobile/luma-fp6-carrier-pdc.service %{buildroot}%{_unitdir}/luma-fp6-carrier-pdc.service

install -D -m 0755 scripts/mobile/luma-fp6-primary-gw-session %{buildroot}%{_libexecdir}/luma-fp6-primary-gw-session
install -D -m 0644 scripts/mobile/luma-fp6-primary-gw-session.service %{buildroot}%{_unitdir}/luma-fp6-primary-gw-session.service

install -D -m 0755 scripts/mobile/luma-fp6-modem-store %{buildroot}%{_libexecdir}/luma-fp6-modem-store
install -D -m 0644 scripts/mobile/luma-fp6-modem-store.service %{buildroot}%{_unitdir}/luma-fp6-modem-store.service
install -D -m 0755 scripts/mobile/luma-fp6-rmtfs %{buildroot}%{_libexecdir}/luma-fp6-rmtfs
install -D -m 0644 scripts/mobile/50-luma-fp6-modem-store.conf %{buildroot}%{_unitdir}/rmtfs.service.d/50-luma-fp6-modem-store.conf
install -D -m 0755 scripts/mobile/luma-fp6-modem-restart %{buildroot}%{_libexecdir}/luma-fp6-modem-restart
install -D -m 0644 scripts/mobile/luma-fp6-modem-restart.service %{buildroot}%{_unitdir}/luma-fp6-modem-restart.service
install -D -m 0755 scripts/mobile/luma-fp6-carrier-select %{buildroot}%{_libexecdir}/luma-fp6-carrier-select
install -D -m 0644 scripts/mobile/luma-fp6-carrier-select.service %{buildroot}%{_unitdir}/luma-fp6-carrier-select.service
install -D -m 0644 carriers.toml %{buildroot}%{_datadir}/luma/fp6-carriers/carriers.toml
install -D -m 0755 scripts/mobile/luma-fp6-modem-state-notify %{buildroot}%{_libexecdir}/luma-fp6-modem-state-notify
install -D -m 0644 scripts/mobile/luma-fp6-modem-state-notify.service %{buildroot}%{_userunitdir}/luma-fp6-modem-state-notify.service
# Enabled for every graphical session; it exits at once unless the modem lacks its EFS.
install -d %{buildroot}%{_userunitdir}/graphical-session.target.wants
ln -s ../luma-fp6-modem-state-notify.service %{buildroot}%{_userunitdir}/graphical-session.target.wants/luma-fp6-modem-state-notify.service

%check
python3 tests/mobile/test_stock_catalog.py scripts/mobile/luma-fp6-carrier-pdc
python3 tests/mobile/test_carrier_lifecycle.py scripts/mobile/luma-fp6-carrier-pdc
%{python3} tests/mobile/test_primary_gw_parser.py -v
%{python3} tests/mobile/test_carrier_kernel.py -v
%{python3} tests/mobile/test_modem_store.py scripts/mobile -v
PYTHONDONTWRITEBYTECODE=1 %{python3} tests/mobile/test_carrier_select.py scripts/mobile/luma-fp6-carrier-select carriers.toml -v
cmp scripts/mobile/luma-fp6-carrier-select %{buildroot}%{_libexecdir}/luma-fp6-carrier-select
cmp carriers.toml %{buildroot}%{_datadir}/luma/fp6-carriers/carriers.toml
%{python3} - <<'PYCODE'
import hashlib, os, pathlib, stat, subprocess, tempfile
root=pathlib.Path('%{buildroot}')
script=root/'usr/libexec/luma-fp6-carrier-pdc'
unit=root/'usr/lib/systemd/system/luma-fp6-carrier-pdc.service'
assert stat.S_IMODE(script.stat().st_mode)==0o755
assert script.read_bytes()==pathlib.Path('scripts/mobile/luma-fp6-carrier-pdc').read_bytes()
body=pathlib.Path('scripts/mobile/luma-fp6-carrier-pdc.service').read_text()
assert unit.read_text()==body
assert body.splitlines().count('ExecStart=/usr/libexec/luma-fp6-carrier-pdc')==1
with tempfile.TemporaryDirectory(prefix='luma-carrier-verify-',dir='/tmp') as tmp:
    executable=pathlib.Path(tmp,'luma-fp6-carrier-pdc');executable.write_bytes(script.read_bytes());executable.chmod(0o755)
    assert os.access(executable,os.X_OK) and executable.read_bytes()==script.read_bytes()
    temporary_unit=pathlib.Path(tmp,'luma-fp6-carrier-pdc.service')
    temporary_unit.write_text(body.replace('ExecStart=/usr/libexec/luma-fp6-carrier-pdc','ExecStart='+str(executable)))
    subprocess.run(['systemd-analyze','verify',str(temporary_unit)],env=dict(os.environ,SYSTEMD_UNIT_PATH=tmp+':/usr/lib/systemd/system'),check=True,timeout=20)
assert unit.read_text()==body and script.read_bytes()==pathlib.Path('scripts/mobile/luma-fp6-carrier-pdc').read_bytes()
print('PASS genuine packaged script and unchanged service unit')
PYCODE
# The modem store never runs a partition-writing rmtfs mode.
! grep -Eq -- '(^|[[:space:]])-(W|R)([[:space:]]|$)' %{buildroot}%{_libexecdir}/luma-fp6-rmtfs
grep -Fqx '  exec /usr/bin/rmtfs -o /var/lib/luma/rmtfs -s' %{buildroot}%{_libexecdir}/luma-fp6-rmtfs
grep -Fqx 'exec /usr/bin/rmtfs -r -P -s' %{buildroot}%{_libexecdir}/luma-fp6-rmtfs
! grep -Eq '(of=|>)[[:space:]]*"?/dev/' %{buildroot}%{_libexecdir}/luma-fp6-modem-store
for unit in luma-fp6-modem-store.service luma-fp6-modem-restart.service luma-fp6-carrier-select.service; do
  sed 's#/usr/libexec/#%{buildroot}%{_libexecdir}/#' %{buildroot}%{_unitdir}/$unit > $unit
  SYSTEMD_UNIT_PATH=$PWD:/usr/lib/systemd/system systemd-analyze verify $PWD/$unit
done

%files
%license LICENSE.md
%{_libexecdir}/luma-fp6-carrier-pdc
%{_unitdir}/luma-fp6-carrier-pdc.service

%{_libexecdir}/luma-fp6-primary-gw-session
%{_unitdir}/luma-fp6-primary-gw-session.service

%doc fp6-fresh-install-modem-efs.md
%{_libexecdir}/luma-fp6-modem-store
%{_unitdir}/luma-fp6-modem-store.service
%{_libexecdir}/luma-fp6-rmtfs
%dir %{_unitdir}/rmtfs.service.d
%{_unitdir}/rmtfs.service.d/50-luma-fp6-modem-store.conf
%{_libexecdir}/luma-fp6-modem-restart
%{_unitdir}/luma-fp6-modem-restart.service
%{_libexecdir}/luma-fp6-carrier-select
%{_unitdir}/luma-fp6-carrier-select.service
%dir %{_datadir}/luma
%dir %{_datadir}/luma/fp6-carriers
%{_datadir}/luma/fp6-carriers/carriers.toml
%{_libexecdir}/luma-fp6-modem-state-notify
%{_userunitdir}/luma-fp6-modem-state-notify.service
%{_userunitdir}/graphical-session.target.wants/luma-fp6-modem-state-notify.service

%changelog
* Fri Nov 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.13
- carrier-pdc: software configurations of other modem firmware releases no
  longer stop the profile selection. An unknown Inactive configuration is
  counted and left alone; the firmware's own Commercial-TMO counts as active
  for tmo-qrel1695; an unknown Active one is still refused and its
  description logged. Catalog bound 26 -> 48 entries.
- carrier-select: T-Mobile and Tello use T-Mobile's default data APN from
  mobile-broadband-provider-info (the provider listing the most network codes
  for a shared network) instead of a table APN or the attach APN; it accepts
  the same active-configuration equivalents as carrier-pdc.

* Mon Nov 09 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.12
- Source moved to SimplyLuma/luma (1.luma.11 source).
- tmo-qrel1695 loads the T-Mobile commercial configuration from
  luma-fp6-firmware when the modem catalog lacks it, as att-qrel1695 does;
  Commercial-TMO as Android provisions it (4ea00db3...) is accepted and
  counts as active for tmo-qrel1695.
- Requires luma-fp6-firmware 1.luma.3, which ships the configurations.

* Sat Oct 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.11
- efs-missing only when rmtfs has no usable modem data: a complete store seeded
  from real EFS is healthy even with blank modemst (the normal installed state)
- The modem notice tries a few times for ten seconds at most; no long wait

* Sat Oct 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.10
- Fix the login loop in 1.luma.9: the modem notice runs in the graphical
  session (Type=exec, graphical-session.target) instead of as a oneshot that
  default.target, and so the login, waited for

* Fri Oct 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.9
- Publish efs-missing when modemst is blank; tell the person once per boot to run
  Android once; the primary SIM session skips at once and bounds every modem call

* Fri Oct 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.8
- Report conditions a retry cannot change once (exit 3, no restart loop)
- Keep the carrier selection when the modem profile cannot be switched
- Do not seed the private modem store from blank modemst (no IMEI); retire a
  store seeded that way once Android has repaired the partitions
- Match Tello by GID1 6941 on 310240/310260

* Fri Oct 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.7
- Choose modem profile, APN and modes from the active SIM (luma-fp6-carrier-select)
  with a packaged carrier table and mobile-broadband-provider-info fallback

* Fri Oct 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.6
- Seed a private modem store on first boot and serve the modem from it
- Restart the modem before ModemManager when the platform config stays Pending
- Ensure the platform config without a carrier profile; skip on read-only rmtfs

* Thu Oct 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.5
- Admit the exact SELinux kernel successor while preserving prior rollback ABIs

* Thu Oct 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4
- Preserve the exact stock Milos PDC catalog while selecting configured identities
- Verify all retained identities after activation and reject pending-only results

* Thu Oct 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.3
- Use the packaged native QMI client and accept either unique native boot slot
- Verify external carrier profiles only before an actual required load

* Mon Oct 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- Own the shared native Primary GW helper and unchanged service unit
- Preserve active slot1/2 sessions and select exactly one physical or eUICC USIM
- Test parser refusal for ambiguous, absent, malformed and unsupported status
* Tue Oct 06 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- Package the native adapter without changing carrier configuration or autoactivation
- Preserve guarded ATT/TMO selection; admit only three exact native kernel releases
