Name: luma-quick-view
Version: 46.0
Release: 1.luma.18.lumaui20260928%{?dist}
Summary: Quick View file previews for Luma Filer
License: GPL-2.0-or-later
URL: https://gitlab.gnome.org/GNOME/sushi
Source0: https://download.gnome.org/sources/sushi/46/sushi-46.0.tar.xz
Patch0: 0001-quick-view-document-source.patch
Patch1: 0002-quick-view-listings.patch
Patch2: 0003-quick-view-folder-overflow.patch
Patch3: 0004-quick-view-keyboard-workarea.patch
Patch4: 0005-quick-view-shared-preview-chrome.patch
Patch5: 0006-quick-view-inherit-theme.patch
Patch6: 0007-quick-view-libarchive-loader.patch
Patch7: 0008-quick-view-state-properties.patch
Patch8: 0009-quick-view-embedded-art-only.patch
Patch9: 0010-quick-view-content-sizing.patch
Patch10: 0011-quick-view-native-pane.patch
Patch11: 0012-quick-view-filer-activation.patch
Patch12: 0013-quick-view-caller-lifetime.patch
Patch13: 0014-quick-view-shared-child-clip.patch
Patch14: 0015-quick-view-media-facts.patch
Patch15: 0016-quick-view-image-chrome.patch
Patch16: 0017-quick-view-present-over-filer.patch
Patch17: 0018-quick-view-unsupported-file-state.patch
Source1: luma-quick-view-tests.tar
BuildRequires: gcc meson gettext gjs-devel gtk3-devel gtksourceview4-devel
BuildRequires: gobject-introspection-devel evince-devel webkit2gtk4.1-devel
BuildRequires: gstreamer1-plugins-base-devel libepoxy-devel harfbuzz-devel
BuildRequires: nodejs python3
Requires: libarchive bubblewrap gjs gtk3 gtksourceview4 webkit2gtk4.1 evince-libs python3-cmarkgfm
Provides: sushi = %{version}-%{release}
Obsoletes: sushi < 48

%description
Luma's maintained GNOME Sushi file previewer, retaining the NautilusPreviewer
D-Bus interface and native media and document viewers. HTML and Markdown offer
rendered and source views with scripting, navigation and resource loads denied.

%prep
%autosetup -n sushi-%{version} -p1

%build
%meson
%meson_build

%install
%meson_install
%find_lang sushi

%check
tar -xf %{SOURCE1} -C %{_builddir}
tests=%{_builddir}/quick-view
for test in keyboard-workarea image-sizing filer-protocol media-facts monitor-placement; do
  node "$tests/$test.js" "$PWD"
done
QUICK_VIEW_LISTING_BACKEND="$PWD/src/quick-view-listing.py" \
  python3 -m unittest discover -s "$tests" -p 'test_listing.py' -v

%files -f sushi.lang
%license COPYING
%{_bindir}/sushi
%{_libexecdir}/org.gnome.NautilusPreviewer
%{_libexecdir}/luma-quick-view-listing
%{_libdir}/sushi/
%{_datadir}/sushi/
%{_datadir}/dbus-1/services/org.gnome.NautilusPreviewer.service
%{_datadir}/metainfo/org.gnome.NautilusPreviewer.appdata.xml
