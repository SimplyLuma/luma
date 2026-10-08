# SPDX-License-Identifier: Apache-2.0

Name:           luma-agenda
Version:        0.1.0
Release:        1.luma.9%{?dist}
Summary:        Upcoming calendar event producer for Project Luma's live extensions
License:        Apache-2.0
URL:            https://projectluma.org/platform/agenda
Source0:        luma-agenda.tar.gz
Source1:        LICENSE.md

BuildArch:      noarch
BuildRequires:  python3-devel >= 3.11
BuildRequires:  systemd-rpm-macros
Requires:       python3 >= 3.11
Requires:       python3-gobject
# Calendar owns both halves of this: the events come from its backend, and the
# publication is made under its application identity, which the Semantic Broker
# resolves from its root-owned desktop file.
Requires:       prairie-core-apps
Requires:       luma-shell-state

%description
Luma Agenda puts the next thing in your day on the shelf. It reads the same
calendars the Calendar application reads, chooses the event happening now or
the next one to start before the day is out, and publishes it to the Semantic
Broker as a live extension under Calendar's own identity.

Nothing from tomorrow is shown, because a shelf announcing tomorrow's stand-up
all evening is noise. The event's name is published as private, so the shelf
replaces it with a neutral phrase while the screen is locked.

%prep
%autosetup -n luma-agenda
cp %{SOURCE1} LICENSE.md

%build

%install
install -D -m 0755 bin/luma-agenda-producer %{buildroot}%{_libexecdir}/luma-agenda-producer
install -d -m 0755 %{buildroot}%{python3_sitelib}/luma_agenda
install -m 0644 luma_agenda/*.py %{buildroot}%{python3_sitelib}/luma_agenda/
install -D -m 0644 data/luma-agenda-producer.service \
  %{buildroot}%{_userunitdir}/luma-agenda-producer.service
# Shipped enabled: the shelf showing your next meeting is the default
# behaviour, and a unit nobody enables is a unit that never runs.
install -D -m 0644 data/luma-agenda-producer.preset \
  %{buildroot}%{_userpresetdir}/80-luma-agenda.preset

%check
PYTHONPATH=$PWD %{python3} -m unittest discover -s tests -v
PYTHONPATH=$PWD %{python3} -m py_compile luma_agenda/*.py

%post
%systemd_user_post luma-agenda-producer.service

%preun
%systemd_user_preun luma-agenda-producer.service

%files
%license LICENSE.md
%{_libexecdir}/luma-agenda-producer
%{python3_sitelib}/luma_agenda/
%{_userunitdir}/luma-agenda-producer.service
%{_userpresetdir}/80-luma-agenda.preset

%changelog
* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.9
- Log a calendar read when the number of events changes or the read is slow, not every two minutes
- Log under luma-agenda rather than systemd-run
- Do not start in the login screen's greeter session
* Sat Sep 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.7
- Read the calendar off the main loop, so a service that is still starting
  delays the island by however long it takes rather than by every timeout
- Only pass the open timeout to backends that accept it; passing it to one
  that does not raised on every read and the island never appeared at all

* Sat Sep 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.6
- Give a calendar four seconds rather than thirty to answer, so the first read
  of a cold session costs seconds instead of the measured 60.1

* Sat Sep 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.5
- Time the calendar read, so a cold backend and a slow schedule stop looking
  alike from outside the producer

* Sat Sep 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4
- Look again soon when the first reads find no calendar, so the event appears
  at login rather than after the Calendar application is opened by hand

* Sat Sep 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.3
- Use a risk the broker knows, so the publication is not silently refused
- Refresh before the publication expires instead of after

* Sat Sep 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- Hold the application before running it, so the producer stays alive

* Sat Sep 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- First packaging of the upcoming calendar event producer
