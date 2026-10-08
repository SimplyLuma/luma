# SPDX-License-Identifier: MPL-2.0

Name:           luma-audio-policy
Version:        0.1.0
Release:        1.luma.3%{?dist}
Summary:        Sound outputs that never take over on their own, and opt-in AirPlay
License:        MPL-2.0 AND CC-BY-SA-4.0
URL:            https://projectluma.org/sound
Source0:        luma-audio-policy.tar.gz

BuildArch:      noarch
BuildRequires:  desktop-file-utils
BuildRequires:  glib2
BuildRequires:  lua
BuildRequires:  meson >= 1.3
BuildRequires:  ninja-build
BuildRequires:  python3-devel >= 3.11
BuildRequires:  python3-gobject-base
BuildRequires:  systemd-rpm-macros
Requires:       python3 >= 3.11
Requires:       python3-gobject-base
# pipewire.conf.d fragments and the module.raop condition in 50-raop.conf
Requires:       pipewire >= 1.6
# libpipewire-module-raop-sink, loaded by luma-audio-devices for chosen receivers
Requires:       pipewire-libs >= 1.6
# pw-dump --monitor
Requires:       pipewire-utils >= 1.6
# wireplumber.settings.schema arrays and the default-nodes hook names
Requires:       wireplumber >= 0.5.10
# Wp-0.5 GObject introspection data
Requires:       wireplumber-libs >= 0.5.10
# AirPlay receivers are found through Avahi; passwords go to the keyring.
Recommends:     avahi
Recommends:     libsecret

%description
Luma's sound output policy. It never asks where sound should go. WirePlumber
keeps the current output when a display, dock or USB sound device appears and
never makes a network receiver the default unless the person picks it.
Automatic AirPlay discovery is off, so nearby Macs no longer appear as outputs.

luma-audio-devices, a session service, switches to headphones, headsets and
Bluetooth audio as soon as the person connects them (the sound returns when
they leave), remembers per device when the person moved the sound away, logs
every decision, and provides the AirPlay picker: receivers are looked for only
while the picker is open, and only the receiver the person chooses becomes an
output. The Shell's sound menu and Settings use it through
org.projectluma.AudioDevices1.

%prep
%autosetup -n luma-audio-policy

%build
%meson
%meson_build

%install
%meson_install
# Enabled by the package itself: a preset applies only on first install.
install -d %{buildroot}%{_userunitdir}/graphical-session.target.wants
ln -s ../luma-audio-devices.service \
  %{buildroot}%{_userunitdir}/graphical-session.target.wants/luma-audio-devices.service

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/org.projectluma.AudioDevices.desktop
# A windowless session service: never listed, never a dock app, and its
# notifications carry the Sound name and a Prairie icon.
for key in NoDisplay=true DBusActivatable=true Name=Sound Icon=audio-volume-high-symbolic; do
  grep -Fqx "$key" %{buildroot}%{_datadir}/applications/org.projectluma.AudioDevices.desktop
done
glib-compile-schemas --strict --dry-run %{buildroot}%{_datadir}/glib-2.0/schemas
PYTHONPATH=%{buildroot}%{python3_sitelib} %{python3} -c "import luma_audio_devices.classify, luma_audio_devices.routing"
cd tests && PYTHONPATH=$PWD/.. %{python3} -m unittest discover -p "test_*.py" -v

%files
%license LICENSES/MPL-2.0.txt LICENSES/CC-BY-SA-4.0.txt
%{_libexecdir}/luma-audio-devices
%{python3_sitelib}/luma_audio_devices/
%{_datadir}/pipewire/pipewire.conf.d/40-luma-network-audio-opt-in.conf
%{_datadir}/wireplumber/wireplumber.conf.d/60-luma-audio-policy.conf
%{_datadir}/wireplumber/scripts/lib/luma-output-policy.lua
%dir %{_datadir}/wireplumber/scripts/luma
%{_datadir}/wireplumber/scripts/luma/output-policy.lua
%{_userunitdir}/luma-audio-devices.service
%{_userunitdir}/graphical-session.target.wants/luma-audio-devices.service
%{_datadir}/dbus-1/services/org.projectluma.AudioDevices.service
%{_datadir}/dbus-1/interfaces/org.projectluma.AudioDevices1.xml
%{_datadir}/applications/org.projectluma.AudioDevices.desktop
%{_datadir}/glib-2.0/schemas/org.projectluma.audio-devices.gschema.xml
%{_datadir}/icons/hicolor/scalable/status/luma-network-speaker-symbolic.svg

%changelog
* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.3
- Never ask about output devices: the "Use <device> for sound?" notification,
  its answers and the new-device-prompts setting are removed
- Displays, docks and other outputs keep the sound where it is; headphones,
  headsets and Bluetooth audio the person connects take it, and it returns when
  they leave; moving the sound away from a device is remembered for it
- "Don't ask again" answers saved by earlier versions are removed from
  state.json and WirePlumber; every routing decision is logged for Vitals
* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- Sound's desktop entry uses the Prairie volume icon; %%check asserts it stays
  hidden and D-Bus activated, so the service never shows as an app
* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- New devices ask before taking the sound; headphones switch when connected
- Network outputs become the default only when chosen while present
- Automatic AirPlay discovery off; opt-in AirPlay picker with remembered receivers
