# SPDX-License-Identifier: Apache-2.0
Name:           luma-viewer
Version:        0.1.0
Release:        1.luma.17.creator20261007.1%{?dist}
Summary:        Open a file, fast, and look at it

License:        Apache-2.0
URL:            https://projectluma.org/
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  desktop-file-utils
BuildRequires:  python3-devel
# %%check imports the application and renders through GTK, Poppler and cairo.
BuildRequires:  python3-gobject
BuildRequires:  python3-cairo
BuildRequires:  gtk4 >= 4.22
BuildRequires:  libadwaita >= 1.9
BuildRequires:  luma-developer-platform >= 0.1.0-1.luma.102.creator20261007.1
BuildRequires:  poppler-glib >= 22.07.0
BuildRequires:  luma-leaf >= 0.1.0-1.luma.11.creator20261007.1
BuildRequires:  webkitgtk6.0
BuildRequires:  xorg-x11-server-Xvfb
BuildRequires:  dbus-daemon
# Real pointer input reaches the inherited Recents context-menu in %%check.
BuildRequires:  libXtst
BuildRequires:  pipewire
BuildRequires:  pipewire-pulseaudio
BuildRequires:  wireplumber
BuildRequires:  pulseaudio-utils
BuildRequires:  gstreamer1
BuildRequires:  gstreamer1-plugins-base
BuildRequires:  gstreamer1-plugins-good

Requires:       python3
Requires:       python3-gobject-base
Requires:       gtk4 >= 4.22
Requires:       libadwaita >= 1.9
Requires:       luma-developer-platform >= 0.1.0-1.luma.102.creator20261007.1
Requires:       gstreamer1
Requires:       gstreamer1-plugins-base
Requires:       gstreamer1-plugins-good
Requires:       google-figtree-fonts
# Poppler renders PDF. It is a hard requirement rather than a weak one: a
# viewer that cannot open a PDF is not this application.
Requires:       poppler-glib >= 22.07.0
Requires:       luma-leaf >= 0.1.0-1.luma.11.creator20261007.1
Requires:       webkitgtk6.0
Requires:       python3-cairo
Requires:       python3-gobject
# Images come through GdkPixbuf, which gtk4 already pulls in; the extra loaders
# are what add HEIF, AVIF and the rest, and are recommended rather than
# required so a minimal image still installs.
Recommends:     libheif
Recommends:     webp-pixbuf-loader

%description
Viewer opens a file so you can look at it: images, PDF, text and tables at full
fidelity, a faithful read-only preview of documents another application owns,
and an honest account of anything it cannot display.

Draw, sign, and annotate images and PDF pages, then save a new PNG or PDF copy.
Original files are preserved. PDF pages and original text/vector content remain
intact; new marks are stored as transparent annotation appearances.

%prep
%autosetup -n %{name}-%{version}

%build
# Pure Python over the shared Application Kit; nothing to compile.

%install
install -d %{buildroot}%{python3_sitelib}/luma_viewer
install -m 0644 luma_viewer/*.py %{buildroot}%{python3_sitelib}/luma_viewer/
install -D -m 0755 bin/luma-viewer %{buildroot}%{_bindir}/luma-viewer
install -D -m 0644 viewer.css %{buildroot}%{_datadir}/luma-viewer/viewer.css
for glyph in data/icons/hicolor/scalable/actions/*.svg; do
  install -D -m 0644 "$glyph" \
    "%{buildroot}%{_datadir}/luma-viewer/icons/hicolor/scalable/actions/$(basename "$glyph")"
done
install -D -m 0644 data/icons/hicolor/index.theme \
  %{buildroot}%{_datadir}/luma-viewer/icons/hicolor/index.theme
install -D -m 0644 data/icons/org.projectluma.Viewer.svg \
  %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Viewer.svg
install -D -m 0644 data/org.projectluma.Viewer.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.Viewer.desktop

%check
python3 -m py_compile %{buildroot}%{python3_sitelib}/luma_viewer/*.py
desktop-file-validate %{buildroot}%{_datadir}/applications/org.projectluma.Viewer.desktop
# Several tests realise real GTK windows, so they run under a private X
# server and session bus (as the rollout's %%check did).
xvfb-run -a -s "-screen 0 1920x1080x24" dbus-run-session -- env GDK_BACKEND=x11 GSK_RENDERER=cairo \
  PYTHONPATH=. python3 -m unittest discover -s tests -p "test_*.py" -v
# Decode actual saved WAV, MP3 and FLAC through the native GStreamer API,
# verify PCM on ordinary-user isolated PipeWire output at adaptive widths.
timeout 120 xvfb-run -a -s "-screen 0 1920x1080x24" dbus-run-session -- env GDK_BACKEND=x11 GSK_RENDERER=cairo \
  PYTHONPATH=%{buildroot}%{python3_sitelib} python3 tests/runtime_audio_preview.py

# Read-only native EPUB chapters and bounded OBJ/STL previews at all four widths.
timeout 120 xvfb-run -a -s "-screen 0 1920x1080x24" dbus-run-session -- env GDK_BACKEND=x11 GSK_RENDERER=cairo \
  PYTHONPATH=%{buildroot}%{python3_sitelib} python3 tests/runtime_document_preview.py

%post
# Registered broadly so Viewer appears under Open With for everything it can
# display. It is deliberately NOT made the default handler for anything here:
# Luma's defaults live in luma-desktop-launcher-policy's gnome-mimeapps.list,
# and an application that claims types on install is the kind of software
# people resent.
update-desktop-database %{_datadir}/applications >/dev/null 2>&1 || :
gtk4-update-icon-cache --force --quiet %{_datadir}/icons/hicolor >/dev/null 2>&1 || :

%postun
update-desktop-database %{_datadir}/applications >/dev/null 2>&1 || :
gtk4-update-icon-cache --force --quiet %{_datadir}/icons/hicolor >/dev/null 2>&1 || :

%files
%{python3_sitelib}/luma_viewer/
%{_bindir}/luma-viewer
%{_datadir}/luma-viewer/
%{_datadir}/applications/org.projectluma.Viewer.desktop
%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Viewer.svg

%changelog
* Wed Oct 07 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.16.creator20261007.1
- Read-only EPUB chapters through Leaf, and bounded interactive OBJ/STL previews.

* Tue Oct 06 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.12.creator20261006.1
- Native audio preview and appropriate file sharing, with real saved-media checks.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.8.creator20261004.1
- LumaUI update from the Prairie rollout review.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.6
- Rebuilt from the latest Viewer on LumaUI.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.5
- LumaUI port update: the app follows the latest approved design.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4
- LumaUI port: the app is rebuilt on LumaUI to match the approved design.

* Sat Sep 19 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.3
- Declare every PDF type (x-bzpdf, x-gzpdf, x-xzpdf, x-ext-pdf) so Viewer can
  be the PDF default; open .pdf.gz, .pdf.bz2 and .pdf.xz within the session
  limit; compressed non-PDF files are archives, not text.
- First release built from committed source (replaces the 1.luma.2 test build).

* Fri Sep 04 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- First package: three panes, persistent Recents, tiered formats, mark up.
