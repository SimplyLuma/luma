# SPDX-License-Identifier: Apache-2.0

Name:           luma-logos
Version:        0.1.0
Release:        1.luma.2%{?dist}
Summary:        Luma system logos, replacing Fedora's
License:        Apache-2.0
URL:            https://simplyluma.com
Source0:        luma-logos.tar.gz

BuildArch:      noarch
BuildRequires:  python3
BuildRequires:  librsvg2-tools

# The Fedora remix model (generic-logos): this package provides what
# fedora-logos provides, and the -httpd subpackage what fedora-logos-httpd
# provides, so replacing Fedora's pair with Luma's pair satisfies every
# requirement (gdm, plymouth, breeze: system-logos; httpd, nginx:
# system-logos(httpd-logo-ng)) without dnf reaching for generic-logos.
Provides:       system-logos = %{version}-%{release}
Provides:       gnome-logos = %{version}-%{release}
Provides:       redhat-logos = %{version}-%{release}
Provides:       fedora-logos = %{version}-%{release}
# Obsoletes match package names only, so these never touch Luma's own
# Provides. The bound sits above any Fedora release's fedora-logos.
Obsoletes:      fedora-logos < 999
Conflicts:      generic-logos

%package httpd
Summary:        Luma logo for web server test pages, replacing Fedora's
License:        Apache-2.0
Provides:       system-logos-httpd = %{version}-%{release}
Provides:       system-logos(httpd-logo-ng)
Provides:       fedora-logos-httpd = %{version}-%{release}
Obsoletes:      fedora-logos-httpd < 999
Conflicts:      generic-logos-httpd

%description httpd
The "powered by" logo web servers such as httpd and nginx show on their
default test page, replacing Fedora's.

%description
The logos Luma shows wherever the system presents its own mark: Settings ›
About (os-release LOGO=luma-logo), the login screen, the start-here icon and
Plymouth's fallback watermark. They replace Fedora's logos, whose marks a
Fedora remix may not use.

%prep
%autosetup -n luma-logos

%build
python3 render.py luma-wordmark.svg out
for size in 16 22 24 32 48 64 96 128 256; do
  rsvg-convert --width=$size --height=$size out/luma-logo.svg -o out/luma-logo-$size.png
done
rsvg-convert --height=128 out/luma-logo-white.svg -o out/system-logo-white.png
rsvg-convert --height=48 out/luma-logo-white.svg -o out/luma-login-logo.png
rsvg-convert --width=256 --height=256 out/luma-logo.svg -o out/luma-logo-256.png
rsvg-convert --height=31 out/luma-logo-text.svg -o out/poweredby.png

%install
icons=%{buildroot}%{_datadir}/icons/hicolor
install -D -m 0644 out/luma-logo.svg $icons/scalable/apps/luma-logo.svg
install -D -m 0644 out/luma-logo-dark.svg $icons/scalable/apps/luma-logo-dark.svg
install -D -m 0644 out/luma-logo-text.svg $icons/scalable/apps/luma-logo-text.svg
install -D -m 0644 out/luma-logo-text-dark.svg $icons/scalable/apps/luma-logo-text-dark.svg
install -D -m 0644 out/luma-logo.svg $icons/scalable/apps/start-here.svg
install -D -m 0644 out/luma-logo.svg $icons/scalable/places/start-here.svg
for size in 16 22 24 32 48 64 96 128 256; do
  install -D -m 0644 out/luma-logo-$size.png $icons/${size}x${size}/apps/luma-logo.png
  install -D -m 0644 out/luma-logo-$size.png $icons/${size}x${size}/places/start-here.png
done
install -D -m 0644 out/luma-logo-256.png %{buildroot}%{_datadir}/pixmaps/luma-logo.png
install -D -m 0644 out/system-logo-white.png %{buildroot}%{_datadir}/pixmaps/system-logo-white.png
install -D -m 0644 out/luma-login-logo.png %{buildroot}%{_datadir}/pixmaps/luma-login-logo.png
install -d -m 0755 %{buildroot}%{_datadir}/luma/logos
install -m 0644 out/*.svg %{buildroot}%{_datadir}/luma/logos/
install -D -m 0644 zz-luma-logos.gschema.override \
  %{buildroot}%{_datadir}/glib-2.0/schemas/zz-luma-logos.gschema.override
install -D -m 0644 out/poweredby.png %{buildroot}%{_datadir}/pixmaps/poweredby.png
install -D -m 0644 LICENSE %{buildroot}%{_licensedir}/%{name}/LICENSE
install -D -m 0644 LICENSE %{buildroot}%{_licensedir}/%{name}-httpd/LICENSE

%check
for name in luma-logo luma-logo-dark luma-logo-text luma-logo-text-dark start-here; do
  test -s %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/$name.svg
done
test -s %{buildroot}%{_datadir}/pixmaps/system-logo-white.png
test -s %{buildroot}%{_datadir}/pixmaps/poweredby.png
# No Fedora mark or name in anything this package ships.
! grep -rli fedora %{buildroot}%{_datadir}/icons %{buildroot}%{_datadir}/luma %{buildroot}%{_datadir}/glib-2.0
python3 - %{buildroot}%{_datadir}/luma/logos <<'PY'
import sys, pathlib, xml.dom.minidom
for path in pathlib.Path(sys.argv[1]).glob("*.svg"):
    xml.dom.minidom.parse(str(path))
PY

%files
%license %{_licensedir}/%{name}/LICENSE
%{_datadir}/icons/hicolor/scalable/apps/luma-logo.svg
%{_datadir}/icons/hicolor/scalable/apps/luma-logo-dark.svg
%{_datadir}/icons/hicolor/scalable/apps/luma-logo-text.svg
%{_datadir}/icons/hicolor/scalable/apps/luma-logo-text-dark.svg
%{_datadir}/icons/hicolor/scalable/apps/start-here.svg
%{_datadir}/icons/hicolor/scalable/places/start-here.svg
%{_datadir}/icons/hicolor/*x*/apps/luma-logo.png
%{_datadir}/icons/hicolor/*x*/places/start-here.png
%{_datadir}/pixmaps/luma-logo.png
%{_datadir}/pixmaps/system-logo-white.png
%{_datadir}/pixmaps/luma-login-logo.png
%{_datadir}/luma/logos/
%{_datadir}/glib-2.0/schemas/zz-luma-logos.gschema.override

%files httpd
%license %{_licensedir}/%{name}-httpd/LICENSE
%{_datadir}/pixmaps/poweredby.png

%changelog
* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- Split luma-logos-httpd so fedora-logos and fedora-logos-httpd can be
  swapped out together on rpm-ostree systems without pulling generic-logos

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- First release: Luma's logos replace Fedora's (ADR-040)
