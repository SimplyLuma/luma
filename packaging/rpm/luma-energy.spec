# SPDX-License-Identifier: MPL-2.0

Name:           luma-energy
Version:        0.1.0
Release:        1.luma.1%{?dist}
Summary:        Spend the battery on what the person is actually looking at
License:        MPL-2.0
URL:            https://projectluma.org/developer/platform/energy
Source0:        luma-energy.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel >= 3.11
BuildRequires:  systemd-rpm-macros
Requires:       python3 >= 3.11
Requires:       python3-gobject-base
# systemctl --user set-property --runtime, freeze and thaw.
Requires:       systemd >= 256
# Without the compositor's report this service knows nothing and does nothing.
Requires:       gnome-shell-extension-luma-energy = %{version}-%{release}
# Mains or battery is read from UPower; without it, mains is assumed, which is
# the setting that does the least.
Recommends:     upower

%description
A laptop running Luma is a laptop running other people's software, and on
battery that is where the charge goes. Luma Energy spends power on the
application in front of the person and less on the ones buried behind it,
without the person having to police anything.

On Wayland there is no protocol that tells an application it is completely
covered by another window, so a browser or Electron application buried under a
maximised window believes it is visible and keeps painting. The compositor is
the only component that knows otherwise. Luma's Shell extension reports which
applications are focused, visible, occluded or hidden, and this service decides
what that should cost, using the systemd scope every application already has.

The application in front of the person is never touched. Anything playing
sound, using the camera, holding an alarm or still doing real work is left
alone whatever its windows are doing. Nothing is paused unless pausing has been
turned on, nothing at all is limited on mains, and every setting is
runtime-scoped: stopping this service, turning it off or restarting the machine
returns it exactly to how it behaved without it.

%prep
%autosetup -n luma-energy

%build

%install
install -d -m 0755 %{buildroot}%{python3_sitelib}/luma_energy
install -m 0644 luma_energy/*.py %{buildroot}%{python3_sitelib}/luma_energy/
install -D -m 0755 bin/luma-energy-service %{buildroot}%{_libexecdir}/luma-energy-service
install -D -m 0755 bin/luma-energy %{buildroot}%{_bindir}/luma-energy
install -D -m 0644 data/luma-energy.service %{buildroot}%{_userunitdir}/luma-energy.service
install -D -m 0644 data/org.projectluma.Energy1.service \
  %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.Energy1.service
install -D -m 0644 data/luma-energy.preset %{buildroot}%{_userpresetdir}/80-luma-energy.preset
install -D -m 0644 README.md %{buildroot}%{_pkgdocdir}/README.md

%check
cd %{_builddir}/luma-energy
# The rules that matter are the ones about what must never happen, so those
# are the ones that run here: the foreground untouched, the Shell and the
# essential agents out of reach, nothing clamped or paused on mains, an
# application with work to do left alone, and every limit undone on the way
# out. A build that cannot prove those does not ship.
PYTHONPATH=. %{python3} -m unittest discover -s tests -p 'test_*.py' -v

%files
%license LICENSE
%doc %{_pkgdocdir}/README.md
%{python3_sitelib}/luma_energy/
%{_libexecdir}/luma-energy-service
%{_bindir}/luma-energy
%{_userunitdir}/luma-energy.service
%{_datadir}/dbus-1/services/org.projectluma.Energy1.service
%{_userpresetdir}/80-luma-energy.preset

%changelog
* Sun Sep 20 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- First release. An application the person cannot see gets a smaller share of
  the processor and the disk and is told it does not need a high clock; the
  one in front of them is never touched. Pausing hidden applications outright
  is built but off, and is proven before it is proposed as a default.
