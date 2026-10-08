Name:           luma-desktop-launcher-policy
Version:        0.1.0
Release:        28.creator20261007.1%{?dist}
Summary:        Luma desktop launcher visibility policy
License:        Apache-2.0
BuildArch:      noarch
Source0:        %{name}.tar.gz
Source1:        LICENSE.md
Source2:        system-flatpak-replacements.txt
BuildRequires:  desktop-file-utils
BuildRequires:  dconf
BuildRequires:  systemd-rpm-macros
BuildRequires:  python3
BuildRequires:  gsettings-desktop-schemas
BuildRequires:  gnome-shell-common
BuildRequires:  dbus-daemon
Requires:       python3

%description
Desktop-only XDG application entry overrides hide backend office launchers,
parental controls, media-sharing and colour-profile utilities, and handheld-only
configuration tools. Their functionality
and provider packages remain available. Removing this package restores the
upstream entries. Viewer is the default for PDFs and images; a once-per-account
step hands those types back to Viewer where a personal default still names a
replaced stock viewer, a missing application or a browser's claim.

%prep
%setup -q -n %{name}
cp %{SOURCE1} LICENSE.md

%build

%install
# Keep the overlay inside the immutable tree, not OSTree's mutable /usr/local.
install -d %{buildroot}%{_datadir}/luma/desktop-overrides/applications
install -m 0644 applications/*.desktop %{buildroot}%{_datadir}/luma/desktop-overrides/applications/
install -D -m 0644 60-luma-desktop-launcher-policy.conf %{buildroot}%{_prefix}/lib/environment.d/60-luma-desktop-launcher-policy.conf
install -D -m 0644 gnome-mimeapps.list %{buildroot}%{_sysconfdir}/xdg/gnome-mimeapps.list

# The browser's own energy settings, as recommended defaults rather than
# managed policy, so every one of them stays changeable in the browser's
# settings. One file, installed once per vendor policy root.
for root in chromium/policies opt/chrome/policies opt/edge/policies brave/policies; do
  install -D -m 0644 browser-policy/luma-energy.json \
    %{buildroot}%{_sysconfdir}/$root/recommended/luma-energy.json
done

install -D -m 0644 dconf/user %{buildroot}%{_sysconfdir}/dconf/profile/luma
install -D -m 0644 dconf/00-luma-desktop %{buildroot}%{_sysconfdir}/dconf/db/luma.d/00-luma-desktop
dconf compile %{buildroot}%{_sysconfdir}/dconf/db/luma %{buildroot}%{_sysconfdir}/dconf/db/luma.d

install -D -m 0755 greeter/luma-greeter-displays %{buildroot}%{_libexecdir}/luma-greeter-displays
install -D -m 0644 greeter/luma-greeter-displays.service %{buildroot}%{_unitdir}/luma-greeter-displays.service
install -d %{buildroot}%{_presetdir}
printf 'enable luma-greeter-displays.service\n' > %{buildroot}%{_presetdir}/60-luma-greeter-displays.preset
# A preset only applies when a package is first installed, so machines that
# received this service in an update never ran it. Ship the enablement itself.
install -d %{buildroot}%{_unitdir}/graphical.target.wants
ln -s ../luma-greeter-displays.service %{buildroot}%{_unitdir}/graphical.target.wants/luma-greeter-displays.service

# Stock system Flatpaks with a native Luma replacement: removed once, on machines
# installed before their entry existed, only where the native provider is present.
install -D -m 0644 %{SOURCE2} %{buildroot}%{_datadir}/luma/system-flatpak-replacements.txt
install -D -m 0755 flatpak-replacements/luma-flatpak-replacements %{buildroot}%{_libexecdir}/luma-flatpak-replacements
install -D -m 0644 flatpak-replacements/luma-flatpak-replacements.service %{buildroot}%{_unitdir}/luma-flatpak-replacements.service
printf 'enable luma-flatpak-replacements.service\n' > %{buildroot}%{_presetdir}/60-luma-flatpak-replacements.preset
install -d %{buildroot}%{_unitdir}/multi-user.target.wants
ln -s ../luma-flatpak-replacements.service %{buildroot}%{_unitdir}/multi-user.target.wants/luma-flatpak-replacements.service

# Once per account, for machines whose personal mimeapps.list predates the
# Viewer defaults. Shipped enabled: a user preset applies only on first install.
install -D -m 0755 default-apps/luma-default-apps-migration %{buildroot}%{_libexecdir}/luma-default-apps-migration
install -D -m 0644 default-apps/luma-default-apps-migration.service %{buildroot}%{_userunitdir}/luma-default-apps-migration.service
install -d %{buildroot}%{_userunitdir}/default.target.wants
ln -s ../luma-default-apps-migration.service %{buildroot}%{_userunitdir}/default.target.wants/luma-default-apps-migration.service

%check
python3 tests/check_touchpad_defaults.py %{buildroot}%{_sysconfdir}/dconf/db/luma
python3 tests/check_dock_defaults.py %{buildroot}%{_sysconfdir}/dconf/db/luma
desktop-file-validate %{buildroot}%{_datadir}/luma/desktop-overrides/applications/*.desktop
%{python3} tests/check_launcher_visibility.py --self-test

# Electron applications that never chose a backend must land on Wayland.
grep -q '^ELECTRON_OZONE_PLATFORM_HINT=auto$' \
  %{buildroot}%{_prefix}/lib/environment.d/60-luma-desktop-launcher-policy.conf

# The energy defaults parse, carry what they claim, and are recommended, not
# managed: a managed policy would grey the control out in the browser.
for f in %{buildroot}%{_sysconfdir}/chromium/policies/recommended/luma-energy.json \
         %{buildroot}%{_sysconfdir}/opt/chrome/policies/recommended/luma-energy.json \
         %{buildroot}%{_sysconfdir}/opt/edge/policies/recommended/luma-energy.json \
         %{buildroot}%{_sysconfdir}/brave/policies/recommended/luma-energy.json; do
  python3 -c "import json,sys; d=json.load(open(sys.argv[1])); \
assert d['BatterySaverModeAvailability'] == 2, d; \
assert 'BackgroundModeEnabled' not in d, d; \
assert d['MemorySaverModeSavings'] == 1, d" "$f"
done
test ! -e %{buildroot}%{_sysconfdir}/chromium/policies/managed/luma-energy.json
# Every PDF type goes to Viewer, and the Document Viewer override is gone.
for type in application/pdf application/x-bzpdf application/x-gzpdf application/x-xzpdf application/x-ext-pdf; do
  grep -Fxq "$type=org.projectluma.Viewer.desktop;" %{buildroot}%{_sysconfdir}/xdg/gnome-mimeapps.list
done
test ! -e %{buildroot}%{_datadir}/luma/desktop-overrides/applications/org.gnome.Papers.desktop
grep -Fxq 'org.gnome.Papers luma-viewer' %{buildroot}%{_datadir}/luma/system-flatpak-replacements.txt
bash -n %{buildroot}%{_libexecdir}/luma-flatpak-replacements
# The migration, hermetically: Papers and a browser's claim become Viewer, a
# personal choice stays, and a second run is a no-op.
check=$(mktemp -d)
mkdir -p "$check/home/.config" "$check/data/applications"
printf '[Desktop Entry]\nName=Viewer\n' >"$check/data/applications/org.projectluma.Viewer.desktop"
printf '[Desktop Entry]\nName=B\nCategories=Network;WebBrowser;\n' >"$check/data/applications/b.desktop"
printf '[Desktop Entry]\nName=R\nCategories=Office;\n' >"$check/data/applications/r.desktop"
printf '[Default Applications]\napplication/pdf=org.gnome.Papers.desktop\napplication/x-gzpdf=b.desktop\nimage/png=r.desktop\n' \
  >"$check/home/.config/mimeapps.list"
HOME="$check/home" XDG_DATA_HOME="$check/none" XDG_DATA_DIRS="$check/data" \
  %{buildroot}%{_libexecdir}/luma-default-apps-migration
grep -Fxq 'application/pdf=org.projectluma.Viewer.desktop;' "$check/home/.config/mimeapps.list"
grep -Fxq 'application/x-gzpdf=org.projectluma.Viewer.desktop;' "$check/home/.config/mimeapps.list"
grep -Fxq 'image/png=r.desktop' "$check/home/.config/mimeapps.list"
test -e "$check/home/.local/state/luma/default-apps-migration/viewer-1"
cp "$check/home/.config/mimeapps.list" "$check/before"
HOME="$check/home" XDG_DATA_HOME="$check/none" XDG_DATA_DIRS="$check/data" \
  %{buildroot}%{_libexecdir}/luma-default-apps-migration
cmp "$check/before" "$check/home/.config/mimeapps.list"
rm -rf "$check"

%post
%systemd_post luma-greeter-displays.service luma-flatpak-replacements.service

%preun
%systemd_preun luma-greeter-displays.service luma-flatpak-replacements.service

%files
%license LICENSE.md
%{_datadir}/luma/desktop-overrides/
%{_prefix}/lib/environment.d/60-luma-desktop-launcher-policy.conf
%config(noreplace) %{_sysconfdir}/xdg/gnome-mimeapps.list
%{_sysconfdir}/chromium/policies/recommended/luma-energy.json
%{_sysconfdir}/opt/chrome/policies/recommended/luma-energy.json
%{_sysconfdir}/opt/edge/policies/recommended/luma-energy.json
%{_sysconfdir}/brave/policies/recommended/luma-energy.json

%config(noreplace) %{_sysconfdir}/dconf/profile/luma
%{_sysconfdir}/dconf/db/luma
%{_sysconfdir}/dconf/db/luma.d/00-luma-desktop
%{_libexecdir}/luma-greeter-displays
%{_unitdir}/luma-greeter-displays.service
%{_unitdir}/graphical.target.wants/luma-greeter-displays.service
%{_presetdir}/60-luma-greeter-displays.preset
%{_datadir}/luma/system-flatpak-replacements.txt
%{_libexecdir}/luma-flatpak-replacements
%{_unitdir}/luma-flatpak-replacements.service
%{_unitdir}/multi-user.target.wants/luma-flatpak-replacements.service
%{_presetdir}/60-luma-flatpak-replacements.preset
%{_libexecdir}/luma-default-apps-migration
%{_userunitdir}/luma-default-apps-migration.service
%{_userunitdir}/default.target.wants/luma-default-apps-migration.service

%changelog
* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-23
- Rebuilt for the integrated Shell candidate: the default Shelf is one
  centred row of the dock, live tiles and status.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-22
- Place the default Shelf dock and live tiles at center with system status
  at the end of the screen, using the Studio Desktop spacing.

* Sun Sep 20 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-21
- The browser no longer closes completely when its last window closes. It
  saved power, and it also stopped the browser's notifications arriving with
  no window open, which some people rely on. Energy Saver on battery and
  releasing long-unused tabs are unchanged.

* Sun Sep 20 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-20
- An Electron application that never chose a display backend now runs on
  Wayland instead of falling to Electron's X11 default and being composited
  twice through XWayland; one that made its own choice still keeps it.
- Chromium-family browsers start with battery defaults: their own energy
  saving when unplugged, tab discarding after long disuse, and no staying
  resident once the last window is closed. Shipped as recommended policy, not
  managed, so every one of them stays changeable in the browser's settings.
