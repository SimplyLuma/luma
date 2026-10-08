# SPDX-License-Identifier: Apache-2.0

Name:           luma-sticky-notes
Version:        0.1.0
Release:        1.luma.7%{?dist}
Summary:        Edge-docked colour-coded sticky notes for Project Luma
License:        Apache-2.0
URL:            https://projectluma.org/apps/sticky-notes
Source0:        luma-sticky-notes.tar.gz
Source1:        LICENSE.md

BuildArch:      noarch
BuildRequires:  luma-developer-platform
BuildRequires:  python3-gobject-base
BuildRequires:  meson >= 1.3
BuildRequires:  ninja-build
BuildRequires:  python3-devel >= 3.11
BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib
Requires:       python3 >= 3.11
Requires:       python3-gobject
Requires:       gtk4 >= 4.18
Requires:       libadwaita >= 1.7
Requires:       luma-developer-platform
# The note body face. Sticky Notes asks for Caveat by family name in its CSS
# and this is the only package on the image that provides it; without it every
# note silently falls back to the system sans and stops reading as handwriting.
Requires:       google-caveat-fonts

# Deliberately NOT a dependency. gtk4-layer-shell is imported optionally at
# runtime and only exists on the handheld (phoc/wlroots) lane; the desktop
# lane runs Mutter, which has no layer-shell protocol, and the dock is an
# ordinary window there. Requiring it would pull a library onto every desktop
# image that nothing on that image can use.
# Recommends:   gtk4-layer-shell

%description
Sticky Notes keeps each note as a colour-coded tab stacked against the edge of
the screen. Pointing at a tab slides its note out; clicking opens it in a small
window with its own paper colour, a pin, and a row of colours to change it to.
Notes live in a private SQLite database in the user's own data directory.
Completing a note is a state it keeps, and deleting one is recoverable.

%prep
%autosetup -n luma-sticky-notes
cp %{SOURCE1} LICENSE.md

%build
%meson
%meson_build

%install
%meson_install
desktop-file-validate %{buildroot}%{_datadir}/applications/org.projectluma.StickyNotes.desktop

%check
# D-Bus activation starts the binary this package installed, wherever the
# prefix put it, and no template placeholder survives.
grep -Fxq 'Name=org.projectluma.StickyNotes' %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.StickyNotes.service
grep -Fxq 'Exec=%{_bindir}/org.projectluma.StickyNotes --gapplication-service' %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.StickyNotes.service
if grep -Fq '@' %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.StickyNotes.service; then
  echo 'unsubstituted placeholder in org.projectluma.StickyNotes.service' >&2
  exit 1
fi
PYTHONPATH=$PWD %{python3} -m unittest discover -s tests -v
appstream-util validate-relax --nonet data/org.projectluma.StickyNotes.metainfo.xml
PYTHONPATH=$PWD %{python3} -m py_compile luma_sticky_notes/*.py
# Every module the sources directory holds must also be installed. This was
# added to the tree and not to meson.build, so the application failed at
# import, D-Bus activation failed with it, and the dock's + button reported
# nothing at all to the person pressing it.
%{python3} - <<'PYCHECK'
import pathlib, re, sys
listed = set(re.findall(r"'luma_sticky_notes/([^']+\.py)'",
                        pathlib.Path('meson.build').read_text()))
# ._* are AppleDouble side files that ride along from a macOS copy;
# they are not modules and must not be packaged.
present = {p.name for p in pathlib.Path('luma_sticky_notes').glob('*.py')
           if not p.name.startswith('._')}
missing = sorted(present - listed)
if missing:
    sys.exit('meson.build does not install: ' + ', '.join(missing))
print('  every module is installed:', len(present))
PYCHECK

%files
%license LICENSE.md
%{_bindir}/org.projectluma.StickyNotes
%{python3_sitelib}/luma_sticky_notes/
%{_datadir}/applications/org.projectluma.StickyNotes.desktop
%{_datadir}/dbus-1/services/org.projectluma.StickyNotes.service
%{_datadir}/metainfo/org.projectluma.StickyNotes.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.StickyNotes.svg
%{_datadir}/luma-sticky-notes/sticky-notes.css

%changelog
* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.7
- Generate the D-Bus service file from the install prefix

* Sat Sep 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.5
- Publish the stack for a shell that draws it, and stand down where one does

* Sat Sep 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4
- Keep the new-note button reachable when the stack is empty

* Fri Sep 11 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.3
- Rest the stack as slivers and open it on approach
- Draw the peek as its own tab opened out, spine and perforation included
- Let the paper be the window, with the note's own dots and drag grip

* Fri Sep 11 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- Release the toolkit's 360px window floor so the dock is a rail, not a slab
- Scroll the tab stack instead of growing the window past the screen
- Make Close readable on paper and the colour swatches round

* Fri Sep 11 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- Add the edge-docked Sticky Notes application, its private store, and its tests
