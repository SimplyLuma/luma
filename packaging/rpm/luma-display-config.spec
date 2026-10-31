# SPDX-License-Identifier: Apache-2.0
Name:           luma-display-config
Version:        0.1.0
Release:        1.luma.7.creator20261024.1%{?dist}
Summary:        Headless Mutter display configuration helpers
License:        Apache-2.0
Source0:        luma-display-config.tar.gz
Source1:        LICENSE.md
BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-gobject-base
Requires:       python3
Requires:       python3-gobject-base
Obsoletes:      luma-displays < 0.2.0

%description
Display configuration model and Mutter D-Bus helpers for Ari. Display controls
live in Settings. This package replaces the retired standalone Displays app
without installing a launcher, activation service or monitor-arrival handler.

%prep
%autosetup -n luma-display-config
cp %{SOURCE1} LICENSE.md

%build

%install
install -d %{buildroot}%{python3_sitelib}/luma_displays
install -m 0644 luma_displays/{__init__,model,displayconfig}.py %{buildroot}%{python3_sitelib}/luma_displays/

%check
(cd /tmp && PYTHONPATH=%{buildroot}%{python3_sitelib} %{python3} -B -c "from luma_displays import model, displayconfig; assert model.__file__.startswith('%{buildroot}'); assert displayconfig.__file__.startswith('%{buildroot}')")
PYTHONPATH=%{buildroot}%{python3_sitelib} %{python3} -B -m unittest discover -s tests -p test_model.py -v
test ! -e %{buildroot}%{_bindir}/org.projectluma.Displays
test ! -d %{buildroot}%{_datadir}/applications
test ! -d %{buildroot}%{_datadir}/dbus-1

%files
%license LICENSE.md
%{python3_sitelib}/luma_displays/
