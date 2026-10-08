# SPDX-License-Identifier: Apache-2.0

Name:           luma-backgrounds
Version:        0.2.0
Release:        1.luma.4%{?dist}
Summary:        Project Luma desktop backgrounds
License:        LicenseRef-Luma-Project-Asset
URL:            https://project-luma.local/
Source0:        prism.png
Source2:        luma-backgrounds.xml
Source3:        README.md
Source4:        luma-prism.png
Source5:        meadow.png
Source6:        ember.png
BuildArch:      noarch

%description
Installs Project Luma's desktop background collection and its GNOME wallpaper
picker metadata. Desktop selection remains an unlocked GNOME preference.

%prep

%build

%install
install -D -m 0644 %{SOURCE0} \
  %{buildroot}%{_datadir}/backgrounds/luma/prism.png
install -D -m 0644 %{SOURCE2} \
  %{buildroot}%{_datadir}/gnome-background-properties/luma-backgrounds.xml
install -D -m 0644 %{SOURCE3} \
  %{buildroot}%{_pkgdocdir}/README.md
install -D -m 0644 %{SOURCE4} \
  %{buildroot}%{_datadir}/backgrounds/luma/luma-prism.png
install -D -m 0644 %{SOURCE5} \
  %{buildroot}%{_datadir}/backgrounds/luma/meadow.png
install -D -m 0644 %{SOURCE6} \
  %{buildroot}%{_datadir}/backgrounds/luma/ember.png

%files
%doc %{_pkgdocdir}/README.md
%{_datadir}/backgrounds/luma/prism.png
%{_datadir}/backgrounds/luma/meadow.png
%{_datadir}/backgrounds/luma/ember.png
%{_datadir}/backgrounds/luma/luma-prism.png
%{_datadir}/gnome-background-properties/luma-backgrounds.xml

%changelog
* Fri Sep 11 2026 Project Luma <builds@project-luma.local> - 0.2.0-1.luma.4
- Offer Prism, Meadow and Ember through the GNOME wallpaper picker
- Replace the Prism asset with the smaller supplied encoding of the same image
- Retain the handheld compatibility asset at its existing path

* Fri Sep 11 2026 Project Luma <builds@project-luma.local> - 0.2.0-1.luma.3
- Offer only the default Prism wallpaper; retain the handheld compatibility asset
- Remove the legacy Mesh asset from installed systems

* Wed Sep 02 2026 Project Luma <builds@project-luma.local> - 0.2.0-1.luma.2
- Ship the handheld Prism encoding at its existing luma-prism.png path alongside
  the desktop prism.png so one package serves the phone compositor, greeter and
  desktop picker

* Sat Aug 29 2026 Project Luma <builds@project-luma.local> - 0.2.0-1.luma.1
- Add Prism as the default while retaining Luma Mesh Gradient in the collection

* Fri Aug 07 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.2
- Replace the oversized PNG source with the selected 8K JPEG

* Fri Aug 07 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.1
- Package the original Luma Mesh Gradient and GNOME picker metadata
