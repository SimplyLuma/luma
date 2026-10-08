# SPDX-License-Identifier: MPL-2.0
#
# Built by scripts/packages/build-luma-python-app.sh luma-calculator. The
# tarball mirrors the repository: the app is src/luma-calculator.
#
# Installed beside the stock calculator for testing: its own application ID
# (org.projectluma.Calculator) and desktop file, and no Obsoletes or Conflicts.
Name:           luma-calculator
Version:        0.1.0
Release:        1.luma.4.creator20261004.1%{?dist}
Summary:        Calculator for Luma
License:        MPL-2.0
URL:            https://projectluma.org/
Source0:        %{name}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-gobject
BuildRequires:  gtk4
BuildRequires:  libadwaita
BuildRequires:  luma-developer-platform >= 0.1.0-1.luma.78~preview.20260923.1
BuildRequires:  desktop-file-utils
Requires:       python3
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       luma-developer-platform >= 0.1.0-1.luma.78~preview.20260923.1

%description
A calculator built on LumaUI, with exact decimal arithmetic, a readout that
keeps the whole expression, and a scientific mode.

%prep
%autosetup -n %{name}
cp src/%{name}/LICENSES/MPL-2.0.txt .

%build
# Pure Python over LumaUI; nothing to compile.

%install
app=src/luma-calculator
install -d %{buildroot}%{_datadir}/%{name}
cp -R $app/luma_calc $app/style $app/icons %{buildroot}%{_datadir}/%{name}/
install -d %{buildroot}%{_bindir}
cat >%{buildroot}%{_bindir}/%{name} <<'EOF'
#!%{python3} -s
import sys
sys.path.insert(0, "%{_datadir}/%{name}")
from luma_calc.app import main
raise SystemExit(main())
EOF
chmod 0755 %{buildroot}%{_bindir}/%{name}
install -D -m 0644 $app/icons/hicolor/scalable/apps/luma-v3-calculator.svg \
  %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/luma-v3-calculator.svg
desktop-file-install --dir=%{buildroot}%{_datadir}/applications $app/data/org.projectluma.Calculator.desktop
%py_byte_compile %{python3} %{buildroot}%{_datadir}/%{name}/luma_calc

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/org.projectluma.Calculator.desktop
grep -Fxq 'Name=Calculator' %{buildroot}%{_datadir}/applications/org.projectluma.Calculator.desktop
test -f %{buildroot}%{_datadir}/%{name}/style/calculator.css
%{python3} counted-unittest.py --self-test
%{python3} counted-unittest.py src/luma-calculator/tests 15
# The installed launcher's import path resolves in the installed tree.
PYTHONPATH= %{python3} -c "import sys; sys.path.insert(0, '%{buildroot}%{_datadir}/%{name}'); import luma_calc.app"

%files
%license MPL-2.0.txt
%{_bindir}/%{name}
%{_datadir}/%{name}/
%{_datadir}/applications/org.projectluma.Calculator.desktop
%{_datadir}/icons/hicolor/scalable/apps/luma-v3-calculator.svg

%changelog
* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4.creator20261004.1
- Release build from the reconciled 2026-09-28 tree.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- LumaUI port update: the app follows the latest approved design.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- First package, installed beside the stock calculator for testing.
