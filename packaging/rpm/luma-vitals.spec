# SPDX-License-Identifier: Apache-2.0

Name:           luma-vitals
Version:        0.1.0
Release:        1.luma.14.creator20261005.1%{?dist}
Summary:        What every application and service costs, and when one misbehaves
License:        Apache-2.0 AND MPL-2.0
URL:            https://projectluma.org/vitals
Source0:        luma-vitals.tar.gz
Source1:        LICENSE.md

BuildArch:      noarch
BuildRequires:  meson >= 1.3
BuildRequires:  ninja-build
BuildRequires:  python3-devel >= 3.11
BuildRequires:  systemd-rpm-macros
# The power guard's tests run the guard on a private bus against fakes.
BuildRequires:  dbus-daemon
BuildRequires:  python3-gobject-base
Requires:       python3 >= 3.11
Recommends:     python3-systemd
# Every Luma machine gets the memory guard with Vitals (ADR-026 §7).
Requires:       %{name}-memory-guard = %{version}-%{release}
# Performance must not stay on when the charger comes out (ADR-046 §5); on a
# machine without a battery the guard never switches anything.
Requires:       %{name}-power-guard = %{version}-%{release}
# A laptop's battery report is only complete with the processor's own energy
# counters, which no session may read; a machine without a battery loses
# nothing by having it, and the service stops itself where there is no RAPL.
Recommends:     %{name}-energy = %{version}-%{release}
# systemctl, systemd-run and gdbus, for restarting a runaway background agent
# and telling the person.
Requires:       systemd
Requires:       glib2

%description
Luma Vitals samples the CPU, memory, swap and I/O of every application and
service in a person's session every 15 seconds, together with the machine's
memory pressure and temperature. It records runaway CPU, memory growth,
idle services holding memory, memory pressure and heat as journal events,
keeps a day of samples and a month of hourly totals locally, and writes an
hourly report. `luma-vitals report` shows what cost the most. A background
agent that grows past 2 GB (or 15% of memory) and keeps growing is restarted
gracefully, and the person is told once.

On battery it also records what the machine is drawing: the rate out of the
battery, the charge left and the runtime that implies, the screen, and the
processor's own energy counters where luma-vitals-energy publishes them. Once
a minute it reads what woke the machine, which interrupts arrived, how much
of the time the processor and graphics spent idle, and what is keeping the
machine out of its low-power states. `luma-vitals battery` prints the last run
on battery: average draw, projected runtime, and a ranked list of what spent
the charge. None of it runs on mains power.

%package memory-guard
Summary:        Stop one app's runaway memory from stalling the whole desktop
Requires:       systemd-oomd-defaults
Requires:       zram-generator-defaults

%description memory-guard
System-wide memory policy for Luma: when used memory and swap both pass 90%,
systemd-oomd stops the app or background agent holding the most swap (only
in the apps' and background agents' slices, never the Shell or the session),
and swap on zram is compressed with zstd, which holds about a third more than
the kernel's default lzo-rle. The compositor has a best-effort 1 GiB memory
protection within the image's uresourced active-session budget, capped there
to 10% of physical RAM. It does not allocate memory or set a hard reservation.

%package energy
Summary:        Let a battery report name what the processor spent
Requires:       python3 >= 3.11

%description energy
The kernel's RAPL counters say how much energy the processor package, its
cores, its graphics and the memory controller have used. They are readable
only by root, because read thousands of times a second they leak what the
processor is doing (CVE-2020-8694). This publishes ten-second averages, which
leak nothing of the sort, to /run/luma-vitals/energy.json, so Luma Vitals can
tell a person that the graphics took four watts of the fourteen their laptop
was drawing. It measures only while the machine is running off its battery; on
mains power it wakes twice a minute to read one file, and on a machine with no
battery, or none of those counters, it does not stay running. It does nothing
else and holds no capability.

%package power-guard
Summary:        Step Performance down to Balanced while running on battery
License:        MPL-2.0
Requires:       python3 >= 3.11
Requires:       python3-gobject-base
Requires:       upower
Recommends:     ppd-service
Recommends:     python3-systemd
Requires(post): systemd
Requires(preun): systemd

%description power-guard
When a laptop starts running on its battery with the Performance power mode
on, the power guard switches it to Balanced, and when power returns it puts
back the mode the person chose while plugged in. It never lowers the mode on
mains power, respects a mode the person picks while on battery until the
next time the computer is plugged in, and leaves an application's temporary
hold alone. It switches only through the power-profiles D-Bus API, so the
power mode control always shows the truth, and records every change it makes
as a Luma Vitals event.

%package crash-evidence
Summary:        Keep the evidence of why a Luma machine last stopped
Requires:       python3 >= 3.11
Recommends:     python3-systemd
# Decodes an Intel Crash Log in the boot error record; absent elsewhere, the
# boot service simply keeps the raw record.
Recommends:     intel-crashlog
Requires(post): systemd
Requires(preun): systemd

%description crash-evidence
System-wide crash evidence for Luma Vitals: the journal is written to disk
every 15 seconds instead of every five minutes, efi_pstore keeps a kernel
panic's last messages for systemd-pstore, a kernel that detects a hard lockup
panics (and so leaves that record) instead of freezing, and a boot service
copies the firmware's Boot Error Record Table — the only trace a firmware
reset leaves — to /var/lib/luma/crash. On Intel machines with iclg installed it
decodes a new Crash Log once, keeps the decoded record and its cause tags beside
the raw one, and says in the journal that the computer restarted unexpectedly
and why. Luma Vitals reports each of these, and unclean shutdowns and crashed
programs, as events.

%prep
%autosetup -n luma-vitals
cp %{SOURCE1} LICENSE.md

%build
%meson
%meson_build

%install
%meson_install
# Enabled by the package itself: a preset only applies on first install.
install -d %{buildroot}%{_userunitdir}/graphical-session.target.wants
ln -s ../luma-vitals.service %{buildroot}%{_userunitdir}/graphical-session.target.wants/luma-vitals.service
install -d %{buildroot}%{_unitdir}/multi-user.target.wants
ln -s ../luma-crash-evidence.service %{buildroot}%{_unitdir}/multi-user.target.wants/luma-crash-evidence.service
ln -s ../luma-vitals-energy.service %{buildroot}%{_unitdir}/multi-user.target.wants/luma-vitals-energy.service
ln -s ../luma-vitals-power-guard.service %{buildroot}%{_unitdir}/multi-user.target.wants/luma-vitals-power-guard.service

%check
PYTHONPATH=$PWD %{python3} -m unittest discover -s tests -p "test_*.py" -v >unit.log 2>&1 || { cat unit.log; exit 1; }
cat unit.log
# A runner that reaches no tests exits 0 too: the power guard's ten, run
# against a fake UPower and profiles service, must each have passed.
guard_ok=$(grep -c '(test_power_guard\.PowerGuardTest\.test_[a-z_]*) \.\.\. ok$' unit.log || :)
[ "$guard_ok" -eq 10 ] || { echo "power guard: 10 tests must pass, $guard_ok did" >&2; exit 1; }
for slice in app.slice luma-background.slice; do
  grep -qx 'ManagedOOMSwap=kill' %{buildroot}%{_userunitdir}/$slice.d/60-luma-oomd-swap.conf
done
grep -qx 'compression-algorithm = zstd' %{buildroot}%{_prefix}/lib/systemd/zram-generator.conf.d/60-luma-zram-zstd.conf
grep -qx 'MemoryLow=1G' %{buildroot}%{_userunitdir}/org.gnome.Shell@.service.d/60-luma-compositor-memory.conf
# This is reclaim protection, not a cap or a new OOM policy for the Shell.
if grep -qE '^(Memory(Min|High|Max|SwapMax)|ManagedOOM[^=]*)=' %{buildroot}%{_userunitdir}/org.gnome.Shell@.service.d/60-luma-compositor-memory.conf; then exit 1; fi
test -f %{buildroot}%{python3_sitelib}/luma_vitals/agents.py
test -f %{buildroot}%{python3_sitelib}/luma_vitals/power.py
# The averaging window is the whole reason root's counters may be published.
grep -qx 'INTERVAL = 10.0' %{buildroot}%{_libexecdir}/luma-vitals-energy
# A meter that runs where there is nothing to measure is what this work is against.
grep -qx 'IDLE_INTERVAL = 30.0' %{buildroot}%{_libexecdir}/luma-vitals-energy
grep -q 'if not discharging(packs):' %{buildroot}%{_libexecdir}/luma-vitals-energy
grep -qx 'CapabilityBoundingSet=' %{buildroot}%{_unitdir}/luma-vitals-energy.service
grep -qx 'RuntimeDirectory=luma-vitals' %{buildroot}%{_unitdir}/luma-vitals-energy.service
%{python3} -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' %{buildroot}%{_libexecdir}/luma-vitals-energy
%{python3} -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' %{buildroot}%{_libexecdir}/luma-vitals-power-guard
test -x %{buildroot}%{_libexecdir}/luma-vitals-power-guard
grep -qx 'ExecStart=/usr/libexec/luma-vitals-power-guard' %{buildroot}%{_unitdir}/luma-vitals-power-guard.service
grep -qx 'StateDirectory=luma-vitals-power-guard' %{buildroot}%{_unitdir}/luma-vitals-power-guard.service
# It switches through the profiles API; it must never drive tuned itself.
if grep -nE 'tuned-adm|com\.redhat\.tuned|/sys/firmware/acpi/platform_profile|energy_performance_preference' %{buildroot}%{_libexecdir}/luma-vitals-power-guard; then exit 1; fi
grep -qx 'SyncIntervalSec=15s' %{buildroot}%{_prefix}/lib/systemd/journald.conf.d/60-luma-crash-evidence.conf
grep -qx 'kernel.softlockup_panic = 0' %{buildroot}%{_prefix}/lib/sysctl.d/60-luma-crash-evidence.conf
grep -qx 'kernel.hardlockup_panic = 1' %{buildroot}%{_prefix}/lib/sysctl.d/60-luma-crash-evidence.conf
grep -q 'efi_pstore.pstore_disable=0' %{buildroot}%{_prefix}/lib/bootc/kargs.d/60-luma-crash-evidence.toml
# Parse only: py_compile would leave a __pycache__ beside it in the buildroot.
for helper in luma-crash-evidence luma-crash-evidence-intel; do
  %{python3} -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' %{buildroot}%{_libexecdir}/$helper
done
grep -qx 'CapabilityBoundingSet=CAP_SYSLOG' %{buildroot}%{_unitdir}/luma-crash-evidence.service
grep -q '^-- e6f1229b2154445eaaf8f207d149fb1f$' %{buildroot}%{_prefix}/lib/systemd/catalog/luma-crash-evidence.catalog

%post crash-evidence
%sysctl_apply 60-luma-crash-evidence.conf

%files
%license LICENSE.md
%{_bindir}/luma-vitals
%{_libexecdir}/luma-vitals
%{python3_sitelib}/luma_vitals/
%{_userunitdir}/luma-vitals.service
%{_userunitdir}/graphical-session.target.wants/luma-vitals.service

%files energy
%license LICENSE.md
%{_libexecdir}/luma-vitals-energy
%{_unitdir}/luma-vitals-energy.service
%{_unitdir}/multi-user.target.wants/luma-vitals-energy.service

%post power-guard
%systemd_post luma-vitals-power-guard.service

%preun power-guard
%systemd_preun luma-vitals-power-guard.service

%postun power-guard
%systemd_postun_with_restart luma-vitals-power-guard.service

%files power-guard
%license LICENSE.md
%{_libexecdir}/luma-vitals-power-guard
%{_unitdir}/luma-vitals-power-guard.service
%{_unitdir}/multi-user.target.wants/luma-vitals-power-guard.service

%files memory-guard
%license LICENSE.md
%dir %{_userunitdir}/app.slice.d
%{_userunitdir}/app.slice.d/60-luma-oomd-swap.conf
%dir %{_userunitdir}/luma-background.slice.d
%{_userunitdir}/luma-background.slice.d/60-luma-oomd-swap.conf
%dir %{_prefix}/lib/systemd/zram-generator.conf.d
%{_prefix}/lib/systemd/zram-generator.conf.d/60-luma-zram-zstd.conf
%dir %{_userunitdir}/org.gnome.Shell@.service.d
%{_userunitdir}/org.gnome.Shell@.service.d/60-luma-compositor-memory.conf

%files crash-evidence
%license LICENSE.md
%{_libexecdir}/luma-crash-evidence
%{_libexecdir}/luma-crash-evidence-intel
%{_unitdir}/luma-crash-evidence.service
%{_prefix}/lib/systemd/catalog/luma-crash-evidence.catalog
%{_unitdir}/multi-user.target.wants/luma-crash-evidence.service
%{_prefix}/lib/systemd/journald.conf.d/60-luma-crash-evidence.conf
%{_prefix}/lib/sysctl.d/60-luma-crash-evidence.conf
%dir %{_prefix}/lib/bootc
%dir %{_prefix}/lib/bootc/kargs.d
%{_prefix}/lib/bootc/kargs.d/60-luma-crash-evidence.toml

%changelog
* Mon Oct 05 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.14.creator20261005.1
- Protect the compositor's resident working set through the active-session
  uresourced budget without introducing a hard reservation or app limit
- Keep the observed Vitals working set below its own reclaim throttle;
  retain bounded 96 MiB high and 192 MiB maximum limits

* Tue Sep 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.12
- Add luma-vitals-power-guard: Performance steps down to Balanced when the
  computer starts running on battery, and the mode chosen while plugged in
  comes back when power returns; it never lowers the mode on mains power and
  respects a mode picked on battery until the next time it is plugged in

* Sun Sep 20 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.11
- The energy service now measures only while the machine is running off its
  battery, wakes twice a minute on mains power, and does not stay running on a
  machine that has no battery

* Sun Sep 20 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.10
- Record what a laptop draws on battery: rate, charge left, projected runtime,
  screen and the processor's energy counters, and `luma-vitals battery` to read
  the last run back
- Name what spent it: the busiest applications, what woke the machine most,
  the busiest interrupts, and what is keeping the machine out of its low-power
  states, read once a minute and never on mains power
- Add luma-vitals-energy: ten-second averages of the processor's root-only
  energy counters, published where a person's session can read them

* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.9
- A clean restart is no longer reported as the computer stopping without
  shutting down: the shutdown message is matched from systemd-logind, which
  logs it, and PID 1 stopping the system or wtmp's shutdown record also count
- An unclean stop now says whether a crash record, sleep or neither explains it
* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.8
- An installed app that couldn't open becomes an event with its reason: the
  missing library, the signal or exit status, and its last error lines, from
  luma-capsule-launch's journal entry
* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.7
- Act, not just report: a background agent (run by luma-background, or autostarted with X-Luma-Background-Agent) above 2 GB or 15% of memory that grew in the last 10 minutes is restarted gracefully, once an hour at most, and the person is told once; apps the person uses are never touched
- Add luma-vitals-memory-guard: systemd-oomd stops the app or agent holding the most swap when memory and swap are both over 90% (app.slice and luma-background.slice only), and zram swap uses zstd
- Record each unit's slice with its sample

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.6
- Decode a new Intel Crash Log at boot with iclg: keep the decoded JSON and triage, journal the cause
- Keep each boot's module map (root only) so a later crash's instruction pointer names its module
- Name the Crash Log's cause and the module in the Vitals firmware-crash-record event

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.5
- Panic only on hard lockups; log soft lockups, which can be false positives under load or in VMs

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4
- Report unclean shutdowns, kernel and firmware crash records and crashed programs as events
- Add luma-vitals-crash-evidence: 15 s journal sync, efi_pstore, lockup panics, BERT capture at boot
- Do not start in the login screen's greeter session

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.3
- Count Android apps, which run in a system container outside the session

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- Count each application and service separately instead of the whole session as one
- Name packaged applications by their app instead of their container id

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- First Vitals: per-unit sampling, five detectors, journal events, hourly reports
