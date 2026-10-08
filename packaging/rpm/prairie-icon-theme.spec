# SPDX-License-Identifier: Apache-2.0

Name:           prairie-icon-theme
Version:        0.1.0
Release:        1.luma.38.creator20261006.1%{?dist}
Summary:        Prairie application icon theme for Project Luma
License:        CC-BY-SA-4.0 AND Apache-2.0 AND ISC AND MIT AND LicenseRef-Luma-System-Artwork-Pending
URL:            https://project-luma.local/
Source0:        index.theme
Source1:        org.gnome.Nautilus.svg
Source2:        org.gnome.Nautilus-symbolic.svg
Source5:        90-prairie.gschema.override
Source6:        README.md
Source7:        status-symbolics.tar
Source8:        places-icons.tar
Source9:        org.gnome.Calculator.svg
Source10:       org.gnome.Calculator-symbolic.svg
Source11:       window-symbolics.tar
Source12:       LUCIDE_LICENSE.txt
Source13:       app-icons.tar
Source14:       lucide-navigation-symbolics.tar
Source15:       LUCIDE_REFERENCE_LICENSE.txt
Source16:       manifest.json
Source17:       application-manifest.json
Source18:       system-icons.tar
Source19:       system-artwork.tar
Source20:       system-manifest.json
Source21:       system-integration.json
Source22:       SYSTEM_ARTWORK_NOTICE.md
Source23:       validate-system-icons.py
Source24:       lucide-transport-sources.tar
Source25:       lucide-messages-sources.tar
Source26:       lucide-pan-sources.tar
Source27:       lucide-fills-sources.tar
BuildRequires:  python3
BuildArch:      noarch
Requires:       adwaita-icon-theme
Requires(post): /usr/bin/gtk-update-icon-cache
Requires(post): /usr/bin/glib-compile-schemas
Requires(postun): /usr/bin/gtk-update-icon-cache
Requires(postun): /usr/bin/glib-compile-schemas

%description
Installs Project Luma's inheriting Prairie icon theme and its unlocked GNOME
default. The theme overrides branded application identities while retaining
Adwaita and hicolor as complete fallbacks.

%prep

%build

%install
install -D -m 0644 %{SOURCE0} \
  %{buildroot}%{_datadir}/icons/Prairie/index.theme
install -D -m 0644 %{SOURCE1} \
  %{buildroot}%{_datadir}/icons/Prairie/scalable/apps/org.gnome.Nautilus.svg
install -D -m 0644 %{SOURCE2} \
  %{buildroot}%{_datadir}/icons/Prairie/symbolic/apps/org.gnome.Nautilus-symbolic.svg
install -D -m 0644 %{SOURCE9} \
  %{buildroot}%{_datadir}/icons/Prairie/scalable/apps/org.gnome.Calculator.svg
install -D -m 0644 %{SOURCE10} \
  %{buildroot}%{_datadir}/icons/Prairie/symbolic/apps/org.gnome.Calculator-symbolic.svg
install -D -m 0644 %{SOURCE5} \
  %{buildroot}%{_datadir}/glib-2.0/schemas/90-prairie.gschema.override
install -D -m 0644 %{SOURCE6} %{buildroot}%{_pkgdocdir}/README.md
install -D -m 0644 %{SOURCE12} %{buildroot}%{_pkgdocdir}/LUCIDE_LICENSE.txt
tar -C %{buildroot}%{_datadir}/icons/Prairie -xf %{SOURCE7}
tar -C %{buildroot}%{_datadir}/icons/Prairie -xf %{SOURCE8}
tar -C %{buildroot}%{_datadir}/icons/Prairie -xf %{SOURCE18}
tar -C %{buildroot}%{_datadir}/icons/Prairie -xf %{SOURCE11}
# Every application icon the theme carries, shipped as a whole directory.
#
# Previously each icon was named individually as its own Source, so artwork
# added to the theme's asset tree was silently never packaged: twelve
# org.projectluma.* launcher icons existed in
# assets/icon-theme/Prairie/scalable/apps and none of them reached an installed
# image, leaving ten core applications rendering as question marks. Shipping the
# directory means new artwork is included by adding the file, not by remembering
# to edit this spec.
tar -C %{buildroot}%{_datadir}/icons/Prairie -xf %{SOURCE13}
install -m 0644 \
  %{buildroot}%{_datadir}/icons/Prairie/scalable/places/user-trash.svg \
  %{buildroot}%{_datadir}/icons/Prairie/scalable/apps/user-trash.svg
install -m 0644 \
  %{buildroot}%{_datadir}/icons/Prairie/scalable/places/user-trash-full.svg \
  %{buildroot}%{_datadir}/icons/Prairie/scalable/apps/user-trash-full.svg

tar -C %{buildroot}%{_datadir}/icons/Prairie -xf %{SOURCE14}
install -m0644 %{SOURCE15} %{buildroot}%{_pkgdocdir}/LUCIDE_REFERENCE_LICENSE.txt
install -m0644 %{SOURCE16} %{buildroot}%{_pkgdocdir}/lucide-navigation-manifest.json
install -d -m 0755 %{buildroot}%{_pkgdocdir}
tar -C %{buildroot}%{_pkgdocdir} -xf %{SOURCE24}
tar -C %{buildroot}%{_pkgdocdir} -xf %{SOURCE25}
tar -C %{buildroot}%{_pkgdocdir} -xf %{SOURCE26}
tar -C %{buildroot}%{_pkgdocdir} -xf %{SOURCE27}

install -m0644 %{SOURCE17} %{buildroot}%{_pkgdocdir}/application-manifest.json

install -m0644 %{SOURCE20} %{buildroot}%{_pkgdocdir}/system-manifest.json
install -m0644 %{SOURCE21} %{buildroot}%{_pkgdocdir}/system-integration.json
install -m0644 %{SOURCE22} %{buildroot}%{_pkgdocdir}/SYSTEM_ARTWORK_NOTICE.md

%check
# Third-party Firefox keeps artwork supplied by its own installed package.
test ! -e %{buildroot}%{_datadir}/icons/Prairie/scalable/apps/firefox.svg
test ! -e %{buildroot}%{_datadir}/icons/Prairie/scalable/apps/org.mozilla.firefox.svg
# The actual Disks launcher identity must use the approved rounded family art.
cmp %{buildroot}%{_datadir}/icons/Prairie/scalable/apps/org.projectluma.Disks.svg \
    %{buildroot}%{_datadir}/icons/Prairie/scalable/apps/org.projectluma.Disks.Preview.svg
python3 %{SOURCE23} --payload --theme %{buildroot}%{_datadir}/icons/Prairie \
  --manifest %{SOURCE20} --integration %{SOURCE21}
# The fills-only glyphs are one path of symbolic ink with no stroke, so GTK,
# the shell and GTK 3 all recolor them.
for glyph in cursor-default draw-freehand edit-rename application-x-apk \
  utilities-system-monitor emblem-default emblem-shared emblem-synchronizing \
  notifications; do
  icon=%{buildroot}%{_datadir}/icons/Prairie/symbolic/actions/$glyph-symbolic.svg
  grep -Fq 'fill="#2e3436"' "$icon"
  ! grep -Eqi 'stroke|currentColor|<(style|mask|filter|image|use)[ >]|href=' "$icon"
done

%post
/usr/bin/gtk-update-icon-cache -qtf %{_datadir}/icons/Prairie || :
/usr/bin/glib-compile-schemas %{_datadir}/glib-2.0/schemas || :

%postun
/usr/bin/gtk-update-icon-cache -qtf %{_datadir}/icons/Prairie || :
/usr/bin/glib-compile-schemas %{_datadir}/glib-2.0/schemas || :

%files
%doc %{_pkgdocdir}/system-manifest.json
%doc %{_pkgdocdir}/system-integration.json
%doc %{_pkgdocdir}/SYSTEM_ARTWORK_NOTICE.md
%doc %{_pkgdocdir}/application-manifest.json
%license %{_pkgdocdir}/LUCIDE_REFERENCE_LICENSE.txt
%doc %{_pkgdocdir}/lucide-navigation-manifest.json
%doc %{_pkgdocdir}/upstream/lucide/transport
%doc %{_pkgdocdir}/upstream/lucide/messages
%doc %{_pkgdocdir}/upstream/lucide/pan
%doc %{_pkgdocdir}/upstream/lucide/fills
%doc %{_pkgdocdir}/README.md
%doc %{_pkgdocdir}/LUCIDE_LICENSE.txt
%{_datadir}/icons/Prairie/
%{_datadir}/glib-2.0/schemas/90-prairie.gschema.override

%changelog
* Mon Oct 05 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.36.creator20261005.1
- Resolve the Disks Preview launcher through the canonical rounded Disks artwork.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.35.creator20261004.1
- Adds the airplane and hotspot (radio tower) glyphs Quick Options draws.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.33
- Approved icon family.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.32
- Latest LumaUI glyphs.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31
- Carries the latest LumaUI glyphs.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.30
- LumaUI glyphs: every Lucide icon the redesigned apps and Shell use, under stable symbolic names.

* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.29
- Add nine standard symbolic names Luma apps and the shell use that Adwaita 50
  no longer ships, so they stop drawing broken-image placeholders:
  cursor-default and draw-freehand (Layouts tools), edit-rename (Notes),
  emblem-default (Ari), emblem-shared (Layouts), emblem-synchronizing (live
  activity fallback), notifications (Do Not Disturb and the date menu),
  utilities-system-monitor (Vitals notifications) and application-x-apk
  (Android installer). Drawn from Lucide 1.39.0 (mouse-pointer-2, pen-tool,
  pencil-line, circle-check, share-2, refresh-cw, bell, activity, package),
  outlined to fills-only 16px symbolics by tools/outline-symbolic.py; sources
  and the upstream LICENSE are vendored in upstream/lucide/fills and digested
  in the navigation manifest.
- Give the Android application installer and its settings panel icons: the
  launcher's application-x-apk and APK files take the approved package
  artwork, and org.projectluma.AndroidSettings takes the Settings artwork.
  Both launchers had no icon in any installed theme.

* Wed Sep 16 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.28
- Add the pan family GTK and the kit name for disclosure and menu chevrons:
  pan-down (chevron-down), pan-up (chevron-up), pan-start (chevron-left) and
  pan-end (chevron-right), plus the pan-start and pan-end -symbolic-rtl mirrors
  Adwaita 50 ships, each taking the opposite upstream file. The kit title bar's
  menu chevron fell through to stock Adwaita. pan-start and pan-end are byte
  identical to go-previous and go-next.
- Their exact Lucide sources are vendored in upstream/lucide/pan and digested
  in the navigation manifest.

* Wed Sep 16 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.27
- Add the glyphs Messages, Contacts and Phone name, under their standard names,
  from Lucide: mail-attachment (paperclip), mail-send (send-horizontal),
  call-start (phone), camera-video (video), avatar-default (user),
  system-users (users), system-search (search), document-edit (square-pen),
  audio-input-microphone (mic), emblem-favorite (heart), view-refresh
  (rotate-cw), dialog-warning (triangle-alert), emblem-ok (check), go-next
  (chevron-right, with its -rtl mirror from chevron-left), phone (smartphone),
  mail-message-new (message-square), and luma-business (building-2) for a
  short-code sender's avatar; and edit-clear (x), object-select (check) and
  go-previous (chevron-left, -rtl from chevron-right), which GTK's search
  entry, drop-down and Adwaita's back button name. They fell through to stock
  Adwaita or were drawn by hand in the app.
- Their exact Lucide sources are vendored in upstream/lucide/messages and
  digested in the navigation manifest. building-2 is absent from the supplied
  lucide-static snapshot and is lucide-react 1.31.0's published icon node.
- assets/icon-theme/tools/adapt-lucide-symbolic.py records the adaptation; it
  reproduces the transport row byte for byte.

* Tue Sep 15 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.26
- Add the media transport row under its standard names, from Lucide's shuffle,
  skip-back, play, pause, skip-forward and repeat, with the two -symbolic-rtl
  skip mirrors Adwaita 50 also ships. Every player and the Shell's media island
  named these icons already and fell through to stock Adwaita; they now resolve
  to Prairie's own Lucide geometry.
- Vendor those upstream Lucide SVGs and digest them in the navigation manifest,
  so the ISC geometry each glyph claims is verifiable from the SRPM alone.
- Re-sync the eight bundled application identities that had drifted from the
  6 September canvas. Release 1.luma.25 was pinned but never built; the spec
  still read 1.luma.24.
* Mon Sep 07 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.23
- Package 51 approved system icon names with exact-byte provenance and payload checks
- Preserve current application, symbolic, and full Trash artwork

* Thu Sep 03 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.19
- Describe and ship what the browser tile actually is. The installed icon is
  original Prairie geometry — the shared tile gradient, the house bloom and an
  authored globe — and embeds no Mozilla artwork. The package nevertheless
  carried Mozilla's official 2019 logo as a source, installed it as package
  documentation, and declared MPL-2.0 and Mozilla trademark terms for it.
- Remove that unused upstream asset along with its integrity assertion, its
  source entry and its documentation install, and drop MPL-2.0 from the licence
  expression. No other payload in this package requires it; the Lucide geometry
  keeps its ISC terms and its licence file. Git history retains the original
  asset and its attribution.

* Thu Sep 03 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.18
- Package the complete Prairie application artwork directory and validate the
  current 1024px authoring canvas without rewriting the legacy Filer source.

* Mon Aug 31 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.17
- Refine the native window minimize, maximize, and restore glyph geometry

* Mon Aug 31 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.16
- Expand window-control strokes to fill-safe GTK symbolic geometry.

* Mon Aug 31 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.14
- Add the canonical simulator window-control glyphs under GTK standard names

* Sun Aug 30 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.13
- Align the installed Filer application icon with the canonical simulator art

* Sat Aug 15 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.12
- Add complete modern and legacy Prairie battery status families

* Mon Aug 10 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.11
- Add full-colour and symbolic Prairie Calculator artwork under its upstream ID

* Mon Aug 10 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.10
- Add byte-identical Applications-context aliases for the native dock tile

* Mon Aug 10 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.9
- Add Prairie empty and full Trash artwork under canonical Places names

* Mon Aug 10 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.8
- Use the final native 16px connected-network symbolic artwork

* Mon Aug 10 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.7
- Replace status glyphs with native 16px fill-only symbolic artwork

* Mon Aug 10 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.6
- Add a Prairie connected-wired glyph for virtual and Ethernet systems

* Mon Aug 10 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.5
- Use GNOME's canonical symbolic foreground ink for Shell recoloring

* Mon Aug 10 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.4
- Add Prairie Wi-Fi, volume, and power symbolic status glyph families

* Sun Aug 09 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.3
- Add Prairie's Firefox tile around unchanged official Mozilla 2019 artwork

* Sun Aug 09 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.2
- Replace the exploratory icon with the exact canonical prairie-os.html asset

* Sun Aug 09 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.1
- Add the inheriting Prairie theme, Filer full-color and symbolic artwork,
  and the unlocked GNOME icon-theme default
