Name:           luma-sound-theme
Version:        0.1.0
Release:        2.luma.1%{?dist}
Summary:        Project Luma event sound theme
License:        MPL-2.0
BuildArch:      noarch
Source0:        %{name}.tar.gz
Source1:        LICENSE
Requires:       sound-theme-freedesktop
BuildRequires:  glib2
BuildRequires:  gsettings-desktop-schemas

%description
Luma's freedesktop.org sound theme. It inherits every event sound from the
freedesktop theme and disables audio-volume-change, so changing the volume
makes no sound, and bell, so the error bell (Backspace with nothing to
delete, and refused actions like it) is silent; Luma shows the volume change
on screen instead, and the error bell has no replacement. It becomes the
default sound theme through a GSettings schema override. Event sounds stay
enabled, and every other event keeps its freedesktop sound. Removing the
package restores the freedesktop theme.

%prep
%setup -q -n %{name}
cp %{SOURCE1} LICENSE

%build

%install
install -D -m 0644 index.theme %{buildroot}%{_datadir}/sounds/luma/index.theme
install -D -m 0644 stereo/audio-volume-change.disabled \
  %{buildroot}%{_datadir}/sounds/luma/stereo/audio-volume-change.disabled
install -D -m 0644 stereo/bell.disabled \
  %{buildroot}%{_datadir}/sounds/luma/stereo/bell.disabled
install -D -m 0644 10_luma-sound-theme.gschema.override \
  %{buildroot}%{_datadir}/glib-2.0/schemas/10_luma-sound-theme.gschema.override

%check
# The override applies to the shipped schema and selects this theme.
schemas=$(mktemp -d)
cp %{_datadir}/glib-2.0/schemas/org.gnome.desktop.sound.gschema.xml "$schemas/"
cp %{buildroot}%{_datadir}/glib-2.0/schemas/10_luma-sound-theme.gschema.override "$schemas/"
glib-compile-schemas --strict "$schemas"
test "$(GSETTINGS_SCHEMA_DIR=$schemas GSETTINGS_BACKEND=memory gsettings get org.gnome.desktop.sound theme-name)" = "'luma'"
test "$(GSETTINGS_SCHEMA_DIR=$schemas GSETTINGS_BACKEND=memory gsettings get org.gnome.desktop.sound event-sounds)" = "true"
rm -rf "$schemas"
grep -qx 'Inherits=freedesktop' %{buildroot}%{_datadir}/sounds/luma/index.theme
test -e %{buildroot}%{_datadir}/sounds/luma/stereo/audio-volume-change.disabled
test -e %{buildroot}%{_datadir}/sounds/luma/stereo/bell.disabled
# Only the two intended events are silenced; nothing else in this theme's own
# stereo directory is disabled, so every other event still falls through to
# the inherited freedesktop sound.
disabled_count=$(find %{buildroot}%{_datadir}/sounds/luma/stereo -name '*.disabled' | wc -l)
test "$disabled_count" -eq 2

%files
%license LICENSE
%doc README.md
%{_datadir}/sounds/luma/
%{_datadir}/glib-2.0/schemas/10_luma-sound-theme.gschema.override

%changelog
* Thu Sep 17 2026 Project Luma <builds@project-luma.local> - 0.1.0-2.luma.1
- Luma sound theme: also disable bell, the system error-bell sound, as a
  backstop behind the gtk-error-bell and audible-bell defaults.
* Wed Sep 16 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.1
- Luma sound theme: freedesktop sounds without audio-volume-change.
