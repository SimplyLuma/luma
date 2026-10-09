# SPDX-License-Identifier: Apache-2.0

%global extension_uuid tiling-toggle@project-luma.local

Name:           gnome-shell-extension-luma-tiling-toggle
Version:        0.1.0
Release:        1.luma.12%{?dist}
Summary:        Project Luma Quick Settings bridge for Tiling Shell
License:        Apache-2.0
URL:            https://project-luma.local/
Source0:        extension.js
Source1:        metadata.json
Source2:        README.md
Source3:        LICENSE.md
Source4:        stylesheet.css
Source5:        verify-ink.py
BuildArch:      noarch
BuildRequires:  nodejs
BuildRequires:  python3
Requires:       gnome-shell >= 50
Requires:       gnome-shell-extension-tiling-shell >= 17.3

%description
Adds a standard Tiling switch and layout chooser to GNOME Quick Settings. The
control manages the enabled state of the separately packaged Tiling Shell
extension and uses its supported settings and Shell D-Bus interface.

%prep
cp %{SOURCE3} LICENSE

%build

%check
# The extension parses, and the layout picker's colours hold in all four
# appearance modes: outlines and the chosen layout 3:1, text 4.5:1.
node --check %{SOURCE0}
python3 %{SOURCE5} %{SOURCE4}

%install
install -D -m 0644 %{SOURCE0} \
  %{buildroot}%{_datadir}/gnome-shell/extensions/%{extension_uuid}/extension.js
install -D -m 0644 %{SOURCE1} \
  %{buildroot}%{_datadir}/gnome-shell/extensions/%{extension_uuid}/metadata.json
install -D -m 0644 %{SOURCE4} \
  %{buildroot}%{_datadir}/gnome-shell/extensions/%{extension_uuid}/stylesheet.css
install -D -m 0644 %{SOURCE2} \
  %{buildroot}%{_pkgdocdir}/README.md

%files
%license LICENSE
%doc %{_pkgdocdir}/README.md
%{_datadir}/gnome-shell/extensions/%{extension_uuid}/

%changelog
* Fri Oct 09 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.12
- Call the native extension manager on the Shell bus and object.

* Fri Oct 09 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.11
- Keep the Quick Options switch in sync with native automatic tiling and
  disabled extensions; restore its state when the Shell refuses a request.

* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.10
- The layout sheet reads like every Quick Options sheet: "Window layouts" with
  a muted count, and its actions as plain sentence-case rows under the
  hairline ("Edit layouts…", "New layout…", "Tiling settings").

* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.9
- Layout previews show again: the detail carries the appearance mode's class,
  which it never inherited, so .8's per-mode rules left every tile blank; a
  neutral fallback holds 3:1 in light and dark before a mode class arrives.

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.8
- The layout picker follows the appearance mode: previews are drawn by a
  stylesheet from the Shell's ink ledger instead of white inline colours, the
  chosen layout takes the state slate and a ring (ADR-043), and the actions
  sit in their own band below the layouts. %check measures every pair.

* Thu Aug 06 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.5
- Resolve Tiling Shell's compiled extension-local settings schema

* Thu Aug 06 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.4
- Add visual layout selection plus edit, new-layout, and settings actions

* Thu Aug 06 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.3
- Use GNOME Shell's live extension service and expose Tiling Settings

* Thu Aug 06 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.2
- Describe the separately packaged, narrowly patched Tiling Shell accurately

* Thu Aug 06 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.1
- Initial Quick Settings bridge
