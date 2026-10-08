# SPDX-License-Identifier: Apache-2.0

Name:           luma-search
Version:        0.1.0
Release:        1.luma.7%{?dist}
Summary:        Shared Project Luma system Search service
License:        Apache-2.0
URL:            https://project-luma.local/
Source0:        luma-search-service
Source1:        org.projectluma.Search1.xml
Source2:        org.projectluma.Search.service
Source3:        org.projectluma.search.gschema.xml
Source4:        README.md
Source5:        LICENSE.md
Source6:        __init__.py
Source7:        contract.py
Source8:        providers.py
Source9:        luma-search-settings
Source10:       org.projectluma.SearchSettings.desktop
Source11:       ranking.py
Source12:       files.py
Source13:       50_luma-search-localsearch.gschema.override
Source14:       test_luma_search.py
Source15:       test_ranking_vectors.py
Source16:       ranking-vectors.json
Source17:       settings-pages.json
Source18:       settings-cases.json
Source19:       places.py
Source20:       filer-places.json
BuildArch:      noarch
BuildRequires:  systemd-rpm-macros
BuildRequires:  python3
Requires:       glib2
Requires:       python3-gobject-base
Requires:       gtk4
# Files and folders come from the LocalSearch index through tinysparql.
Requires:       libtinysparql
Recommends:     localsearch

%description
Provider-backed, form-factor-neutral aggregation, privacy, ranking, and
activation for Luma Search. The service is D-Bus activated and keeps no content
index of its own.

%prep
cp %{SOURCE5} LICENSE

%build

%check
mkdir -p check/src/luma-search/luma_search check/tests/unit check/tests/search
cp %{SOURCE0} %{SOURCE1} check/src/luma-search/
cp %{SOURCE6} %{SOURCE7} %{SOURCE8} %{SOURCE11} %{SOURCE12} %{SOURCE19} check/src/luma-search/luma_search/
cp %{SOURCE14} check/tests/unit/
cp %{SOURCE17} %{SOURCE20} check/src/luma-search/
cp %{SOURCE15} %{SOURCE16} %{SOURCE18} check/tests/search/
(cd check && python3 -m unittest tests.unit.test_luma_search -v) >check.log 2>&1 || { cat check.log; exit 1; }
cat check.log
# A suite that reached nothing, or skipped the Filer places tests, is a failure.
grep -Eq "^Ran ([2-9][0-9]|[1-9][0-9]{2,}) tests" check.log
test "$(grep -c "FilerPlacesTests.* ok$" check.log)" -ge 8
(cd check && python3 tests/search/test_ranking_vectors.py -v)

%install
install -D -m 0755 %{SOURCE0} %{buildroot}%{_libexecdir}/luma-search-service
install -D -m 0644 %{SOURCE1} \
  %{buildroot}%{_datadir}/dbus-1/interfaces/org.projectluma.Search1.xml
install -D -m 0644 %{SOURCE2} \
  %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.Search.service
install -D -m 0644 %{SOURCE3} \
  %{buildroot}%{_datadir}/glib-2.0/schemas/org.projectluma.search.gschema.xml
install -D -m 0644 %{SOURCE4} %{buildroot}%{_pkgdocdir}/README.md
install -D -m 0644 %{SOURCE6} %{buildroot}%{_libexecdir}/luma_search/__init__.py
install -D -m 0644 %{SOURCE7} %{buildroot}%{_libexecdir}/luma_search/contract.py
install -D -m 0644 %{SOURCE8} %{buildroot}%{_libexecdir}/luma_search/providers.py
install -D -m 0644 %{SOURCE11} %{buildroot}%{_libexecdir}/luma_search/ranking.py
install -D -m 0644 %{SOURCE12} %{buildroot}%{_libexecdir}/luma_search/files.py
install -D -m 0644 %{SOURCE19} %{buildroot}%{_libexecdir}/luma_search/places.py
install -D -m 0644 %{SOURCE17} %{buildroot}%{_datadir}/luma-search/settings-pages.json
install -D -m 0644 %{SOURCE20} %{buildroot}%{_datadir}/luma-search/filer-places.json
install -D -m 0644 %{SOURCE13} \
  %{buildroot}%{_datadir}/glib-2.0/schemas/50_luma-search-localsearch.gschema.override
install -D -m 0755 %{SOURCE9} %{buildroot}%{_libexecdir}/luma-search-settings
install -D -m 0644 %{SOURCE10} \
  %{buildroot}%{_datadir}/applications/org.projectluma.SearchSettings.desktop

%files
%license LICENSE
%doc %{_pkgdocdir}/README.md
%{_libexecdir}/luma-search-service
%{_libexecdir}/luma-search-settings
%{_libexecdir}/luma_search/
%{_datadir}/applications/org.projectluma.SearchSettings.desktop
%{_datadir}/dbus-1/interfaces/org.projectluma.Search1.xml
%{_datadir}/dbus-1/services/org.projectluma.Search.service
%{_datadir}/glib-2.0/schemas/org.projectluma.search.gschema.xml
%{_datadir}/glib-2.0/schemas/50_luma-search-localsearch.gschema.override
%{_datadir}/luma-search/

%posttrans
glib-compile-schemas %{_datadir}/glib-2.0/schemas &>/dev/null || :

%changelog
* Tue Sep 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.7
- Offer Filer's places (Applications, Home, Trash, the standard folders,
  Network, bookmarks) by name and everyday words, in Filer's labels, and
  open Filer there. Ship the shared place table for the Shell.
* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.6
- Take a provider's result kind from its app's Categories, so a mail app's
  results are mail, and rank mail, messages, contacts and events below apps,
  settings and files.

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.5
- Search files and folders in the LocalSearch index with one ranking model
  shared with Beam: clean exact and prefix names first, backups, copies and
  generated output last, recent and picked items raised.
- Find Settings pages and options by name and everyday words from
  settings-pages.json, shared with Beam, and open their panel.
- Add Reveal to show a file or folder in Filer, and keep dependency caches and
  virtual environments out of the LocalSearch index by default.

* Wed Sep 02 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4
- Return locally ranked results within a strict interaction budget instead of
  waiting for every optional external provider to exhaust its timeout.

* Tue Sep 01 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.3
- Bound provider activation by keeping synchronous proxy construction free of
  property loading and auto-start side effects.

* Tue Sep 01 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- Load the installed D-Bus interface XML from its system data location.

* Tue Sep 01 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- Add the shared SearchProvider2 adapter, privacy filter, ranking, activation,
  D-Bus activation, and bounded idle exit for desktop and handheld clients
