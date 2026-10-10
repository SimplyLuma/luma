# SPDX-License-Identifier: Apache-2.0

Name:           prairie-core-apps
Version:        0.1.0
Release:        1.luma.102.creator20261010.1%{?dist}
Summary:        Project Luma responsive core applications
License:        Apache-2.0 AND CC-BY-SA-4.0
URL:            https://project-luma.local/apps
BuildArch:      noarch

Source0:        prairie-core-apps.tar.gz
Source1:        LICENSE.md
Source2:        luma-continuity.tar.gz
# Exact owned appearance contract for private theme checks, without a build cycle.
Source3:        org.project_luma.shell-state.gschema.xml

BuildRequires:  dbus-daemon
BuildRequires:  desktop-file-utils
BuildRequires:  evolution-data-server
BuildRequires:  python3-devel
BuildRequires:  luma-application-installer >= 0.1.0-1.luma.60.creator20261007.1
# Avatar kinds, ScrollView, the kit menu (1.luma.67) and the Lightbox (1.luma.68).
BuildRequires:  luma-developer-platform >= 0.1.0-1.luma.102.creator20261007.1
# The list checks every glyph Messages shows against the installed Prairie theme.
BuildRequires:  prairie-icon-theme >= 0.1.0-1.luma.28
BuildRequires:  xorg-x11-server-Xvfb
BuildRequires:  libXtst
# tests/connect_native_session.py waits for the real portal (Settings and
# Inhibit, gtk backend) on its private bus; a clean builder must have both.
BuildRequires:  xdg-desktop-portal
BuildRequires:  xdg-desktop-portal-gtk
# Luma Continuity is not a build dependency: it BuildRequires this package, so
# requiring it here made a cycle that no clean builder could start. The Connect
# runtime checks below run against its source from this same tree (Source2,
# tests/luma-continuity on PYTHONPATH), which is also the exact revision they
# should prove. What that source imports at run time is required here instead.
BuildRequires:  python3dist(cryptography)
BuildRequires:  (python3dist(websockets) >= 15.0.1 with python3dist(websockets) < 18)
BuildRequires:  (python3dist(authlib) >= 1.7.2 with python3dist(authlib) < 1.8)
BuildRequires:  (python3dist(httpx) >= 0.28.1 with python3dist(httpx) < 0.29)
BuildRequires:  (python3dist(joserfc) >= 1.7.5 with python3dist(joserfc) < 2)
BuildRequires:  openssl
BuildRequires:  qrencode-libs
BuildRequires:  libsecret
BuildRequires:  gstreamer1-plugins-base
BuildRequires:  gstreamer1-plugins-good
BuildRequires:  gstreamer1-plugins-bad-free
BuildRequires:  libnice-gstreamer1
BuildRequires:  pipewire-pulseaudio
BuildRequires:  libX11
BuildRequires:  libXtst
Requires:       google-figtree-fonts
Requires:       evolution-data-server
Requires:       gtk4 >= 4.22
Requires:       ibm-plex-mono-fonts
Requires:       libadwaita >= 1.9
Requires:       luma-developer-platform >= 0.1.0-1.luma.102.creator20261007.1
# Messages' glyphs are Prairie's Lucide set by standard name (1.luma.27).
Requires:       prairie-icon-theme >= 0.1.0-1.luma.28
Requires:       gstreamer1
Requires:       gstreamer1-plugin-gtk4
Requires:       libcamera-gstreamer
# Clock plays its alarm and timer tones itself, through GStreamer, from the
# freedesktop sound theme.
Requires:       gstreamer1-plugins-base
Requires:       sound-theme-freedesktop
# Clock asks the Background portal to start it at login while alarms exist; it
# writes the autostart entry itself when the portal is not running.
Recommends:     xdg-desktop-portal
# Runs Clock's alarms agent, and wakes a suspended computer for the next alarm
# (org.projectluma.BackgroundWake1). Without it alarms ring on resume.
Requires:       luma-background >= 0.1.0-1.luma.5.creator20261007.1
Requires:       /usr/bin/notify-send
Requires:       python3
Requires:       python3-gobject-base
# Fixed Clock host API authenticates signed installed independent clients.
Requires:       luma-application-installer >= 0.1.0-1.luma.60.creator20261007.1
# Weather's Add City search, and each city's time zone, come from libgweather's
# location database. Its forecast does not: libgweather 4.6 publishes neither a
# precipitation figure nor a UV index, so the numbers come from MET Norway.
# Weather runs without this, with coordinate entry in place of a name search.
Recommends:     libgweather
# Network accounts in Messages (ADR-023); Messages works without them.
Recommends:     luma-messages-bridges
# Helpers before 0.9 asked the phone to act on messages automatically and do not
# enforce a person's token for deliveries.
Conflicts:      luma-messages-bridges < 0.1.0-0.9.experiment

%description
Project Luma's source-owned responsive application toolkit and core
applications. One binary and one codebase per app serve desktop and handheld
presentation. Luma owns each visible application and delegates only to shared
system services such as Evolution Data Server and ModemManager.

%prep
%autosetup -n prairie-core-apps
# Luma Continuity source for the shared Phone/Messages Connect runtime smokes.
tar -xzf %{SOURCE2} -C tests
cp %{SOURCE1} LICENSE.md

%build

%install
install -d %{buildroot}%{python3_sitelib}/prairie_ui
install -m 0644 prairie_ui/*.py %{buildroot}%{python3_sitelib}/prairie_ui/
install -d %{buildroot}%{python3_sitelib}/prairie_apps
install -m 0644 prairie_apps/*.py %{buildroot}%{python3_sitelib}/prairie_apps/
install -D -m 0755 bin/prairie-messages \
  %{buildroot}%{_bindir}/prairie-messages
install -D -m 0755 bin/prairie-messages-daemon \
  %{buildroot}%{_bindir}/prairie-messages-daemon
install -D -m 0755 bin/prairie-phone-daemon \
  %{buildroot}%{_bindir}/prairie-phone-daemon
install -D -m 0755 bin/prairie-camera %{buildroot}%{_bindir}/prairie-camera
install -D -m 0755 bin/prairie-phone %{buildroot}%{_bindir}/prairie-phone
install -D -m 0755 bin/prairie-contacts %{buildroot}%{_bindir}/prairie-contacts
install -D -m 0755 bin/prairie-calendar %{buildroot}%{_bindir}/prairie-calendar
install -D -m 0755 bin/prairie-tasks %{buildroot}%{_bindir}/prairie-tasks
install -D -m 0755 bin/prairie-notes %{buildroot}%{_bindir}/prairie-notes
install -D -m 0755 bin/prairie-voice-memos %{buildroot}%{_bindir}/prairie-voice-memos
install -D -m 0755 bin/prairie-photos %{buildroot}%{_bindir}/prairie-photos
install -D -m 0755 bin/luma-migrate-photos-directory %{buildroot}%{_bindir}/luma-migrate-photos-directory
install -D -m 0755 bin/prairie-weather %{buildroot}%{_bindir}/prairie-weather
install -D -m 0755 bin/prairie-clock %{buildroot}%{_bindir}/prairie-clock
install -D -m 0755 bin/prairie-clock-alarm %{buildroot}%{_bindir}/prairie-clock-alarm
install -D -m 0755 bin/luma-connect-sync %{buildroot}%{_bindir}/luma-connect-sync
install -d -m 0755 %{buildroot}%{_datadir}/prairie-core
install -m 0644 style/*.css %{buildroot}%{_datadir}/prairie-core/
install -D -m 0644 data/org.projectluma.Messages.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.Messages.desktop
install -D -m 0644 data/org.projectluma.Camera.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.Camera.desktop
install -D -m 0644 data/org.projectluma.Phone.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.Phone.desktop
install -D -m 0644 data/org.projectluma.Contacts.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.Contacts.desktop
install -D -m 0644 data/org.projectluma.Calendar.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.Calendar.desktop
install -D -m 0644 data/org.projectluma.Tasks.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.Tasks.desktop
install -D -m 0644 data/org.projectluma.Notes.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.Notes.desktop
install -D -m 0644 data/org.projectluma.VoiceMemos.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.VoiceMemos.desktop
install -D -m 0644 data/org.projectluma.Photos.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.Photos.desktop
install -D -m 0644 data/org.projectluma.Weather.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.Weather.desktop
install -D -m 0644 data/org.projectluma.Clock.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.Clock.desktop
# D-Bus activation starts Clock without a window: the old alarm units hand over
# to it this way, and the shell activates notification buttons through it.
install -d %{buildroot}%{_datadir}/dbus-1/services
sed 's|@bindir@|%{_bindir}|' data/org.projectluma.Clock.service.in \
  > %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.Clock.service
sed 's|@bindir@|%{_bindir}|' data/org.projectluma.ClockHost1.service.in \
  > %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.ClockHost1.service
install -D -m 0644 data/org.projectluma.Messages.svg \
  %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Messages.svg
install -D -m 0644 data/org.projectluma.Notes.svg \
  %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Notes.svg
# Notes' folder glyph. It goes into the system icon theme the way the kit's
# own symbolics do, so a packaged Notes finds it without a search path.
for glyph in folder pin; do
  install -D -m 0644 data/icons/hicolor/scalable/actions/luma-$glyph-symbolic.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/actions/luma-$glyph-symbolic.svg
done
# Clock's four tab glyphs. The tab bar is the app's only navigation, so these
# are not decoration: on the handheld the bottom bar is icon over label.
for glyph in world alarm stopwatch timer; do
  install -D -m 0644 data/icons/hicolor/scalable/actions/luma-clock-$glyph-symbolic.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/actions/luma-clock-$glyph-symbolic.svg
done
# Phone's dialer, call controls and call history glyphs, and the Contacts card's
# Message, Call, Video and Mail actions. They sat in the source tree unpackaged,
# so both apps drew broken-image placeholders.
for glyph in data/icons/hicolor/scalable/actions/luma-phone-*-symbolic.svg \
    data/icons/hicolor/scalable/actions/luma-{call,mail,message,video}-symbolic.svg; do
  install -D -m 0644 "$glyph" \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/actions/$(basename "$glyph")
done
install -D -m 0644 data/prairie-messages-daemon.service \
  %{buildroot}%{_userunitdir}/prairie-messages-daemon.service
# Background agents (ADR-033): windowless processes that keep Messages, Phone,
# Calendar and Clock working with their windows closed. Each app declares its
# agent for luma-background, which generates its unit under the category's
# limits and starts and wakes it. Until that service is installed, an autostart
# entry starts Messages', Phone's and Calendar's agents at login (and steps
# aside once it is); Clock asks the Background portal for its autostart.
for app in Messages Phone Calendar Clock; do
  install -D -m 0644 data/agents/org.projectluma.$app.toml \
    %{buildroot}%{_datadir}/luma/background/org.projectluma.$app.toml
done
for app in Messages Phone Calendar; do
  install -D -m 0644 data/agents/org.projectluma.$app.Agent.desktop \
    %{buildroot}%{_sysconfdir}/xdg/autostart/org.projectluma.$app.Agent.desktop
done
# Calendar's agent shows reminders; evolution-alarm-notify would show each twice.
install -D -m 0644 data/agents/50-luma-calendar-reminders.conf \
  %{buildroot}%{_userunitdir}/evolution-alarm-notify.service.d/50-luma-calendar-reminders.conf
install -D -m 0644 data/prairie-phone-daemon.service \
  %{buildroot}%{_userunitdir}/prairie-phone-daemon.service
install -D -m 0644 data/luma-connect-sync.service \
  %{buildroot}%{_userunitdir}/luma-connect-sync.service
install -D -m 0644 data/luma-connect-sync.timer \
  %{buildroot}%{_userunitdir}/luma-connect-sync.timer
install -D -m 0644 data/luma-connect-sync.path \
  %{buildroot}%{_userunitdir}/luma-connect-sync.path
install -D -m 0644 data/luma-connect-sync-watch.service \
  %{buildroot}%{_userunitdir}/luma-connect-sync-watch.service
install -d -m 0755 %{buildroot}%{_userpresetdir}
printf 'enable luma-connect-sync.timer\nenable luma-connect-sync.path\nenable luma-connect-sync-watch.service\n' > %{buildroot}%{_userpresetdir}/80-luma-connect-sync.preset
install -D -m 0644 data/60-luma-handheld-telephony.rules \
  %{buildroot}%{_datadir}/polkit-1/rules.d/60-luma-handheld-telephony.rules
install -D -m 0644 data/luma-mimeapps.list \
  %{buildroot}%{_sysconfdir}/xdg/mimeapps.list

install -d -m 0755 %{buildroot}%{_sysconfdir}/skel/Photos
install -D -m 0644 data/luma-user-dirs.dirs \
  %{buildroot}%{_sysconfdir}/skel/.config/user-dirs.dirs

%check
# Exercise shared-library grants and home aliases against the installed payload.
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=%{buildroot}%{python3_sitelib} \
  python3 -B tests/test_prairie_photos_backend.py -v
# Creator review: persisted setup city, real ZIP response boundaries, actual
# GTK state transitions and widths/themes against the installed payload.
env PYTHONPATH=%{buildroot}%{python3_sitelib} python3 tests/creator_preview_unit.py
env PYTHONPATH=%{buildroot}%{python3_sitelib} python3 tests/creator_followup_unit.py
env PYTHONPATH=%{buildroot}%{python3_sitelib} python3 tests/audio_continuation.py
# Saved pixel edits must export their rendered pixels while preserving originals.
printf 'BEGIN PHOTOS EXPORT PURE\n'
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=%{buildroot}%{python3_sitelib} \
  python3 -B tests/photos_export_unit.py -v
printf 'END PHOTOS EXPORT PURE\n'
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1800x1000x24" \
  env GSK_RENDERER=cairo GTK_A11Y=none GSETTINGS_BACKEND=memory \
    LUMA_CREATOR_SCHEMA_FILE=%{SOURCE3} \
    LUMA_NOTES_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/notes.css \
    LUMA_PHOTOS_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/photos.css \
    LUMA_CLOCK_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/clock.css \
    LUMA_PHONE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/phone.css \
    LUMA_MESSAGES_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/messages.css \
    LUMA_CALENDAR_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/calendar.css \
    LUMA_CAMERA_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/camera.css \
    LUMA_TASKS_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/tasks.css \
    LUMA_MEMO_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/memo.css \
    PYTHONPATH=%{buildroot}%{python3_sitelib} \
  python3 tests/creator_preview_runtime.py
# Real EDS invalidations must not rebuild a submitted comment back to the top.
LUMA_CREATOR_SCHEMA_FILE=%{SOURCE3} \
LUMA_TASKS_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/tasks.css \
PYTHONPATH=%{buildroot}%{python3_sitelib} \
python3 tests/connect_native_session.py -- python3 tests/tasks_comment_eds_runtime.py
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1920x1080x24" \
  env GSK_RENDERER=cairo GTK_A11Y=none GSETTINGS_BACKEND=memory \
    LUMA_CREATOR_SCHEMA_FILE=%{SOURCE3} \
    LUMA_NOTES_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/notes.css \
    LUMA_MESSAGES_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/messages.css \
    PYTHONPATH=%{buildroot}%{python3_sitelib} \
  python3 tests/creator_followup_runtime.py
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1440x900x24" \
  env GSK_RENDERER=cairo GTK_A11Y=none GSETTINGS_BACKEND=memory \
    LUMA_NOTES_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/notes.css \
    PYTHONPATH=%{buildroot}%{python3_sitelib} \
  python3 tests/notes_creator_followup_runtime.py
# Current creator workflows execute against the staged installed payload.
# Replies/contacts and synthetic audio remain explicit private fixtures.
for regression in messages_recipient_runtime messages_details_runtime messages_host_lifecycle_runtime phone_navigation_runtime voice_memos_audio; do
  timeout 120s xvfb-run -a --server-args="-screen 0 1920x1080x24" dbus-run-session -- \
    env GSK_RENDERER=cairo GTK_A11Y=none GSETTINGS_BACKEND=memory \
      LUMA_CREATOR_SCHEMA_FILE=%{SOURCE3} \
      LUMA_MESSAGES_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/messages.css \
      LUMA_PHONE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/phone.css \
      PYTHONPATH=%{buildroot}%{python3_sitelib} \
    python3 "tests/$regression.py"
done
# The existing username action must be in the visible native viewport, usable
# by real pointer input and Enter, and reject stale/duplicate UI submissions.
username_root=$(mktemp -d)
mkdir -p "$username_root"/{config,data,cache,state,runtime,captures}
chmod 0700 "$username_root/runtime"
timeout 120s xvfb-run -a --server-args="-screen 0 1920x1080x24" dbus-run-session -- \
  env GSK_RENDERER=cairo GSETTINGS_BACKEND=keyfile \
    HOME="$username_root" XDG_CONFIG_HOME="$username_root/config" XDG_DATA_HOME="$username_root/data" \
    XDG_CACHE_HOME="$username_root/cache" XDG_STATE_HOME="$username_root/state" XDG_RUNTIME_DIR="$username_root/runtime" \
    LUMA_CREATOR_SCHEMA_FILE=%{SOURCE3} LUMA_MESSAGES_USERNAME_OUTPUT="$username_root/captures" \
    PYTHONPATH=%{buildroot}%{python3_sitelib} \
  python3 tests/messages_username_runtime.py
rm -rf "$username_root"
# Luma Hub sync engine: exporter, shared lists/photos, Tide sources, messages/calls logs,
# and which install (system package or Flatpak) of each synced app it targets.
for unit in connect_sync_unit connect_profile_unit connect_shared_unit connect_tide_unit connect_messages_unit messages_cloud_unit connect_leaf_unit messages_accounts_unit connect_installs_unit connect_notes_unit connect_watch_unit notes_model_unit connect_calendar_unit collaboration_recipients_unit collaboration_ownership_unit collaboration_transport_unit collaboration_generation_unit phone_shared_data_unit; do
  env PYTHONPATH=%{buildroot}%{python3_sitelib} HOME=$(mktemp -d) python3 tests/$unit.py
done
# Notes invitation kinds, readonly recovery and live metadata invalidation.
timeout 120s xvfb-run -a --server-args="-screen 0 1440x1080x24" dbus-run-session -- \
  env GSK_RENDERER=cairo GTK_A11Y=none GSETTINGS_BACKEND=memory \
    LUMA_CREATOR_SCHEMA_FILE=%{SOURCE3} \
    PYTHONPATH=%{buildroot}%{python3_sitelib} \
  python3 tests/collaboration_role_runtime.py
PYTHONPATH=%{buildroot}%{python3_sitelib} python3 tests/test_phone_backend_regression.py
PYTHONPATH=%{buildroot}%{python3_sitelib} python3 tests/phone_luma_contacts_unit.py
PYTHONPATH=%{buildroot}%{python3_sitelib} python3 tests/tasks_comment_time_unit.py
dbus-run-session -- env PYTHONPATH=%{buildroot}%{python3_sitelib} python3 tests/phone_owner_runtime_smoke.py --absence-seconds 65
PYTHONPATH=%{buildroot}%{python3_sitelib} python3 -m unittest discover -s tests -p test_messages_send_recovery.py -v
# Contacts' live search and empty state, the edit dialog's account name, and
# Phone without a modem: no notices, one line to pair a phone in Connect, and
# calls through a phone paired in Connect. Real windows, invented contacts.
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1280x900x24" \
  env GSK_RENDERER=cairo PRAIRIE_EDS_MODE=disabled \
    PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
    PYTHONPATH=%{buildroot}%{python3_sitelib} \
  python3 tests/contacts_phone_fixes_runtime.py
desktop-file-validate %{buildroot}%{_datadir}/applications/*.desktop
# Every luma-* glyph Contacts and Phone name is installed, and each is a
# fills-only symbolic (one ink, no strokes) that every renderer recolors.
for name in $(grep -ohE '"luma-(phone-[a-z]+|call|mail|message|video)-symbolic"' \
    %{buildroot}%{python3_sitelib}/prairie_apps/contacts.py \
    %{buildroot}%{python3_sitelib}/prairie_apps/phone.py | tr -d '"' | sort -u); do
  glyph=%{buildroot}%{_datadir}/icons/hicolor/scalable/actions/$name.svg
  test -s "$glyph" || { echo "missing glyph $name" >&2; exit 1; }
done
for glyph in %{buildroot}%{_datadir}/icons/hicolor/scalable/actions/luma-{phone-*,call,mail,message,video}-symbolic.svg; do
  grep -q 'fill="#2e3436"' "$glyph"
  ! grep -Eqi 'stroke|currentColor|<(style|mask|filter|image|use)[ >]|href=' "$glyph"
done
# Settings lists an app under Notifications only when its desktop file says it
# uses them. These five post notifications, so the key must stay.
for app in Messages Phone Calendar Clock Weather; do
  grep -qx 'X-GNOME-UsesNotifications=true' \
    %{buildroot}%{_datadir}/applications/org.projectluma.$app.desktop
done
grep -qx 'Exec=%{_bindir}/prairie-clock --gapplication-service' \
  %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.Clock.service
! grep -q '@' %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.Clock.service
# Clock's alarm service: scheduling, missed and late alarms, snooze, timers,
# the move off the old systemd units and the Background portal answers; the
# sleep hold and wake-up before an alarm, and the logind and session-manager
# holds against stand-ins on a private bus.
env PYTHONPATH=%{buildroot}%{python3_sitelib} %{python3} tests/clock_host_unit.py
dbus-run-session -- xvfb-run -a env PYTHONPATH=%{buildroot}%{python3_sitelib} %{python3} tests/clock_host_lifecycle.py
dbus-run-session -- env PYTHONPATH=%{buildroot}%{python3_sitelib} %{python3} tests/clock_host_runtime.py
dbus-run-session -- env PYTHONPATH=%{buildroot}%{python3_sitelib} HOME=$(mktemp -d) python3 tests/clock_alarms_unit.py
# ADR-033 agents: contract files agree, no GTK in any agent, Phone and Messages
# decisions, the agent publisher's single instance and published state.
dbus-run-session -- env PYTHONPATH=%{buildroot}%{python3_sitelib} HOME=$(mktemp -d) python3 tests/background_agents_unit.py
# Deliveries (messages_outbound): only a person's claimed request reaches a helper,
# once; a long randomized session of reconnects, helper crashes, unanswered sends,
# gone media and backfill delivers exactly the person's sends.
env PYTHONPATH=%{buildroot}%{python3_sitelib} HOME=$(mktemp -d) OUTBOUND_FUZZ_STEPS=400 python3 tests/messages_outbound_unit.py
env PYTHONPATH=%{buildroot}%{python3_sitelib} HOME=$(mktemp -d) python3 tests/messages_app_mailbox_unit.py
# Preserve pending UI state when an outgoing UID resolves its canonical conversation.
env PYTHONPATH=%{buildroot}%{python3_sitelib} HOME=$(mktemp -d) python3 tests/messages_conversation_migration_unit.py
desktop-file-validate %{buildroot}%{_sysconfdir}/xdg/autostart/org.projectluma.*.Agent.desktop
python3 -m py_compile \
  %{buildroot}%{python3_sitelib}/prairie_ui/*.py \
  %{buildroot}%{python3_sitelib}/prairie_apps/*.py
for geometry in 980x680:false 500x800:true; do
  size=${geometry%%:*}
  expected=${geometry##*:}
  dbus-run-session -- xvfb-run -a --server-args="-screen 0 ${size}x24" \
    env GSK_RENDERER=cairo PRAIRIE_EDS_MODE=disabled XDG_STATE_HOME=$(mktemp -d) \
      PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
      LUMA_CLOCK_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/clock.css \
      LUMA_WEATHER_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/weather.css \
      PYTHONPATH=%{buildroot}%{python3_sitelib} \
    python3 tests/runtime_smoke.py \
      --expected-collapsed "$expected" --expected-decorated true
done
for geometry in 980x680:false 500x800:true; do
  size=${geometry%%:*}
  expected=${geometry##*:}
  dbus-run-session -- xvfb-run -a --server-args="-screen 0 ${size}x24" \
    env GSK_RENDERER=cairo PRAIRIE_EDS_MODE=disabled XDG_STATE_HOME=$(mktemp -d) \
      LUMA_PRESENTATION_MODE=fullscreen-mobile LUMA_INPUT_MODE=touch \
      PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
      LUMA_CLOCK_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/clock.css \
      LUMA_WEATHER_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/weather.css \
      PYTHONPATH=%{buildroot}%{python3_sitelib} \
    python3 tests/runtime_smoke.py \
      --expected-collapsed "$expected" --expected-decorated false
done

dbus-run-session -- env PYTHONPATH=%{buildroot}%{python3_sitelib} python3 tests/messages_reply_runtime_smoke.py
# Messages' window frees the rows and bubbles of earlier renders: 150 reconnect
# re-renders with an open conversation of pictures, and context menus opened
# and closed, keep the widget count and resident memory flat (2026-09-16 leak).
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1280x900x24" \
  env GSK_RENDERER=cairo PRAIRIE_EDS_MODE=disabled HOME=$(mktemp -d) \
    PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
    PYTHONPATH=%{buildroot}%{python3_sitelib} \
  python3 tests/messages_window_leak_runtime.py
# The open conversation keeps the reader's position through redraws, receipts and
# new messages (it snapped to the bottom), and pictures show whole at their own
# shape with a bounded, released pixel cache (they were kilobyte previews).
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1280x900x24" \
  env GSK_RENDERER=cairo PRAIRIE_EDS_MODE=disabled HOME=$(mktemp -d) \
    PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
    LUMA_MESSAGES_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/messages.css \
    PYTHONPATH=%{buildroot}%{python3_sitelib} \
  python3 tests/messages_thread_runtime.py
# Scroll anchoring (Nick, 2026-09-17: "Messages often jumps me up"): the message
# at the top of the view holds still (at most 1 px in any painted frame) through late
# pictures, receipts, reactions, upserts, a typing row, new messages, older
# history, a full redraw, a resize and a click on text, updates change rows in
# place, and a reader at the end stays at the end.
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1280x900x24" \
  env GSK_RENDERER=cairo PRAIRIE_EDS_MODE=disabled HOME=$(mktemp -d) \
    PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
    LUMA_MESSAGES_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/messages.css \
    PYTHONPATH=%{buildroot}%{python3_sitelib} \
  python3 tests/messages_scroll_anchor_runtime.py
# The conversation list: rows one fixed height whatever they hold, unread bold
# with a dot and read not, avatars as initials or person/business/group glyphs,
# the kit menu with only real actions (and Delete confirmed), the header naming
# only the service with no three-dot button, and every glyph Prairie's.
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1280x900x24" \
  env GSK_RENDERER=cairo PRAIRIE_EDS_MODE=disabled HOME=$(mktemp -d) \
    PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
    LUMA_MESSAGES_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/messages.css \
    PYTHONPATH=%{buildroot}%{python3_sitelib} \
  python3 tests/messages_list_runtime.py

# Clock opens onto saved world clocks and alarms (it once could not), wide,
# narrow and handheld.
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1280x900x24" \
  env GSK_RENDERER=cairo PRAIRIE_EDS_MODE=disabled \
    PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
    LUMA_CLOCK_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/clock.css \
    PYTHONPATH=%{buildroot}%{python3_sitelib} \
  python3 tests/clock_window_runtime_smoke.py

# Weather's condition mapping, night rule, units, range tracks, cache and
# missing-figure rows need no display.
env PYTHONPATH=%{buildroot}%{python3_sitelib} HOME=$(mktemp -d) python3 tests/weather_unit.py

for width in 360 500 1024 1280; do
  compact=false
  [ "$width" -gt 639 ] || compact=true
  dbus-run-session -- xvfb-run -a --server-args="-screen 0 1440x1000x24" \
    env GSK_RENDERER=cairo PRAIRIE_EDS_MODE=disabled \
      PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
      LUMA_PHOTOS_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/photos.css \
      PYTHONPATH=%{buildroot}%{python3_sitelib} \
    python3 tests/photos_runtime_smoke.py --width "$width" --height 800 \
      --expected-compact "$compact" --expected-decorated true --require-luma-platform
done

# Exercise the actual Photos window callback and persisted draft with native
# pointer events; preview geometry alone cannot prove Done/Cancel behavior.
for width in 360 500 1024 1440; do
  timeout --kill-after=5s 60s xvfb-run -a --server-args="-screen 0 1920x1200x24" \
    dbus-run-session -- env GSK_RENDERER=cairo GTK_A11Y=none PRAIRIE_EDS_MODE=disabled \
      PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
      LUMA_PHOTOS_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/photos.css \
      PYTHONPATH=%{buildroot}%{python3_sitelib} \
    python3 tests/photos_free_crop_runtime.py --width "$width"
done

# Real GSK/GTK-owner rendering and lossless crop/rotation/colour PNG readback.
printf 'BEGIN PHOTOS EXPORT NATIVE\n'
timeout --kill-after=5s 60s dbus-run-session -- xvfb-run -a \
  --server-args="-screen 0 1920x1200x24" \
  env PYTHONDONTWRITEBYTECODE=1 GSK_RENDERER=cairo GTK_A11Y=none \
    PRAIRIE_EDS_MODE=disabled GSETTINGS_BACKEND=memory \
    PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
    LUMA_PHOTOS_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/photos.css \
    PYTHONPATH=%{buildroot}%{python3_sitelib} \
  python3 -B tests/photos_export_runtime.py -v
printf 'END PHOTOS EXPORT NATIVE\n'

for presentation in windowed fullscreen-mobile; do
  dbus-run-session -- xvfb-run -a --server-args="-screen 0 1600x1000x24" \
    env GSK_RENDERER=cairo LUMA_PRESENTATION_MODE="$presentation" \
      LUMA_NOTES_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/notes.css \
      PYTHONPATH=%{buildroot}%{python3_sitelib} \
    python3 tests/notes_file_runtime_smoke.py
done

# Notes' editor in a real window, once light and once dark: list markers that
# can be seen, Enter and Backspace in lists, the title and its placeholder,
# the page menu, folder rename, pictures pasted and dropped in, and saving.
for appearance in light dark; do
  scheme=""
  [ "$appearance" = dark ] && scheme="-dark"
  dbus-run-session -- xvfb-run -a --server-args="-screen 0 1280x900x24" \
    env GSK_RENDERER=cairo PRAIRIE_EDS_MODE=disabled HOME=$(mktemp -d) \
      NOTES_TEST_APPEARANCE=$appearance NOTES_TEST_SHOTS=$(mktemp -d) \
      LUMA_APPKIT_TOKENS_PATH=%{_datadir}/luma-appkit/luma-appkit$scheme-tokens.css \
      LUMA_APPKIT_STYLE_PATH=%{_datadir}/luma-appkit/luma-appkit$scheme.css \
      PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
      LUMA_NOTES_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/notes.css \
      PYTHONPATH=%{buildroot}%{python3_sitelib} \
    python3 tests/notes_polish_runtime.py
done

# Notes pictures never size the window: a page with wide screenshots opens at
# the window's own size on a small and a large screen, and the pictures fit
# the text column from the first frame.
for screen in 1366x768 1920x1200; do
  dbus-run-session -- xvfb-run -a --server-args="-screen 0 ${screen}x24" \
    env GSK_RENDERER=cairo PRAIRIE_EDS_MODE=disabled HOME=$(mktemp -d) \
      LUMA_APPKIT_TOKENS_PATH=%{_datadir}/luma-appkit/luma-appkit-tokens.css \
      LUMA_APPKIT_STYLE_PATH=%{_datadir}/luma-appkit/luma-appkit.css \
      PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
      LUMA_NOTES_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/notes.css \
      PYTHONPATH=%{buildroot}%{python3_sitelib} \
    python3 tests/notes_picture_sizing_runtime.py
done

# Notes shows what sync writes while it is open: the list keeps its scroll and
# selection, an unedited open page refreshes in place, an edited one keeps the
# person's text and saving keeps exactly one other copy; no refresh loop.
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1280x900x24" \
  env GSK_RENDERER=cairo PRAIRIE_EDS_MODE=disabled HOME=$(mktemp -d) \
    LUMA_APPKIT_TOKENS_PATH=%{_datadir}/luma-appkit/luma-appkit-tokens.css \
    LUMA_APPKIT_STYLE_PATH=%{_datadir}/luma-appkit/luma-appkit.css \
    PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
    LUMA_NOTES_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/notes.css \
    PYTHONPATH=%{buildroot}%{python3_sitelib} \
  python3 tests/notes_live_reload_runtime.py

for fixture in empty_states_runtime_smoke rest_empty_states_runtime_smoke; do
  dbus-run-session -- xvfb-run -a --server-args="-screen 0 1920x1200x24" \
    env GSK_RENDERER=cairo PRAIRIE_EDS_MODE=disabled \
      PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
      LUMA_NOTES_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/notes.css \
      LUMA_CONTACTS_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/contacts.css \
      LUMA_MESSAGES_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/messages.css \
      LUMA_CLOCK_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/clock.css \
      LUMA_WEATHER_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/weather.css \
      PYTHONPATH=%{buildroot}%{python3_sitelib} python3 tests/$fixture.py
done

# Additional current shared Messages provider check; all canonical checks above remain.
LUMA_MESSAGES_CONNECT_UI_OUTPUT=%{_builddir}/connect-ui-output \
PYTHONPATH=$PWD/tests/luma-continuity:%{buildroot}%{python3_sitelib} \
PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
LUMA_MESSAGES_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/messages.css \
PRAIRIE_EDS_MODE=disabled \
python3 tests/connect_native_session.py -- python3 tests/messages_connect_runtime_smoke.py

# ADR-022: this device's messages and a Luma Connect phone in one window; each
# conversation sends through its own service only.
LUMA_MESSAGES_CONNECT_UI_OUTPUT=%{_builddir}/services-ui-output \
PYTHONPATH=$PWD/tests/luma-continuity:%{buildroot}%{python3_sitelib} \
PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
LUMA_MESSAGES_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/messages.css \
PRAIRIE_EDS_MODE=disabled \
python3 tests/connect_native_session.py -- python3 tests/messages_services_runtime_smoke.py

# ADR-023: add a network account by QR code through the Accounts dialog, use it, remove it (fake helper).
LUMA_MESSAGES_CONNECT_UI_OUTPUT=%{_builddir}/accounts-ui-output \
PYTHONPATH=$PWD/tests/luma-continuity:%{buildroot}%{python3_sitelib} \
PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
LUMA_MESSAGES_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/messages.css \
PRAIRIE_EDS_MODE=disabled \
python3 tests/connect_native_session.py -- python3 tests/messages_accounts_runtime_smoke.py

LUMA_MESSAGES_CONNECT_UI_OUTPUT=%{_builddir}/composer-ui-output \
PYTHONPATH=$PWD/tests/luma-continuity:%{buildroot}%{python3_sitelib} \
PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
LUMA_MESSAGES_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/messages.css \
PRAIRIE_EDS_MODE=disabled \
python3 tests/connect_native_session.py -- python3 tests/messages_composer_runtime_smoke.py
# A computer without a modem: no notice on an open conversation, one calm line
# once there is a draft that cannot go out by text.
LUMA_MESSAGES_CONNECT_UI_OUTPUT=%{_builddir}/cellular-notice-output \
PYTHONPATH=$PWD/tests/luma-continuity:%{buildroot}%{python3_sitelib} \
PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
LUMA_MESSAGES_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/messages.css \
PRAIRIE_EDS_MODE=disabled \
python3 tests/connect_native_session.py -- python3 tests/messages_cellular_notice_runtime.py

LUMA_CALLS_UI_OUTPUT=%{_builddir}/calls-ui-output \
PYTHONPATH=$PWD/tests/luma-continuity:%{buildroot}%{python3_sitelib} \
PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
LUMA_PHONE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/phone.css \
PRAIRIE_EDS_MODE=disabled \
python3 tests/connect_native_session.py -- python3 tests/calls_session_runtime_smoke.py

LUMA_CALLS_UI_OUTPUT=%{_builddir}/calls-connect-ui-output \
PYTHONPATH=$PWD/tests/luma-continuity:%{buildroot}%{python3_sitelib} \
PRAIRIE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/prairie.css \
LUMA_PHONE_STYLE_PATH=%{buildroot}%{_datadir}/prairie-core/phone.css \
PRAIRIE_EDS_MODE=disabled \
python3 tests/connect_native_session.py -- python3 tests/calls_connect_runtime_smoke.py

%post
update-desktop-database %{_datadir}/applications >/dev/null 2>&1 || :
gtk-update-icon-cache --force --quiet %{_datadir}/icons/hicolor >/dev/null 2>&1 || :

%posttrans
systemctl --global enable prairie-messages-daemon.service >/dev/null 2>&1 || :

%postun
update-desktop-database %{_datadir}/applications >/dev/null 2>&1 || :
gtk-update-icon-cache --force --quiet %{_datadir}/icons/hicolor >/dev/null 2>&1 || :


%files
%license LICENSE.md
%{_bindir}/prairie-messages
%{_bindir}/prairie-messages-daemon
%{_bindir}/prairie-phone-daemon
%{_bindir}/prairie-camera
%{_bindir}/prairie-phone
%{_bindir}/prairie-contacts
%{_bindir}/prairie-calendar
%{_bindir}/prairie-tasks
%{_bindir}/prairie-notes
%{_bindir}/prairie-voice-memos
%{_bindir}/prairie-photos
%{_bindir}/luma-migrate-photos-directory
%{_bindir}/prairie-weather
%{_bindir}/prairie-clock
%{_bindir}/prairie-clock-alarm
%{_bindir}/luma-connect-sync
%{_datadir}/dbus-1/services/org.projectluma.Clock.service
%{_datadir}/dbus-1/services/org.projectluma.ClockHost1.service
%dir %{_datadir}/luma
%dir %{_datadir}/luma/background
%{_datadir}/luma/background/org.projectluma.Messages.toml
%{_datadir}/luma/background/org.projectluma.Phone.toml
%{_datadir}/luma/background/org.projectluma.Calendar.toml
%{_datadir}/luma/background/org.projectluma.Clock.toml
%{python3_sitelib}/prairie_ui/
%{python3_sitelib}/prairie_apps/
%{_datadir}/prairie-core/
%{_datadir}/applications/org.projectluma.*.desktop
%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Messages.svg
%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Notes.svg
%{_datadir}/icons/hicolor/scalable/actions/luma-folder-symbolic.svg
%{_datadir}/icons/hicolor/scalable/actions/luma-pin-symbolic.svg
%{_datadir}/icons/hicolor/scalable/actions/luma-clock-world-symbolic.svg
%{_datadir}/icons/hicolor/scalable/actions/luma-clock-alarm-symbolic.svg
%{_datadir}/icons/hicolor/scalable/actions/luma-clock-stopwatch-symbolic.svg
%{_datadir}/icons/hicolor/scalable/actions/luma-clock-timer-symbolic.svg
%{_datadir}/icons/hicolor/scalable/actions/luma-phone-*-symbolic.svg
%{_datadir}/icons/hicolor/scalable/actions/luma-call-symbolic.svg
%{_datadir}/icons/hicolor/scalable/actions/luma-mail-symbolic.svg
%{_datadir}/icons/hicolor/scalable/actions/luma-message-symbolic.svg
%{_datadir}/icons/hicolor/scalable/actions/luma-video-symbolic.svg
%{_userunitdir}/prairie-messages-daemon.service
%config(noreplace) %{_sysconfdir}/xdg/autostart/org.projectluma.Messages.Agent.desktop
%config(noreplace) %{_sysconfdir}/xdg/autostart/org.projectluma.Phone.Agent.desktop
%config(noreplace) %{_sysconfdir}/xdg/autostart/org.projectluma.Calendar.Agent.desktop
%dir %{_userunitdir}/evolution-alarm-notify.service.d
%{_userunitdir}/evolution-alarm-notify.service.d/50-luma-calendar-reminders.conf
%{_userunitdir}/prairie-phone-daemon.service
%{_userunitdir}/luma-connect-sync.service
%{_userunitdir}/luma-connect-sync.timer
%{_userunitdir}/luma-connect-sync.path
%{_userunitdir}/luma-connect-sync-watch.service
%{_userpresetdir}/80-luma-connect-sync.preset
%{_datadir}/polkit-1/rules.d/60-luma-handheld-telephony.rules
%config(noreplace) %{_sysconfdir}/xdg/mimeapps.list
%config(noreplace) %{_sysconfdir}/skel/.config/user-dirs.dirs
%dir %{_sysconfdir}/skel/Photos

%changelog
* Sat Oct 10 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.102.creator20261010.1
- Recognize the shared Photos library through the system home directory alias.

* Thu Oct 08 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.100.creator20261008.1
- Preserve verified Luma contacts without a telephone number in Phone.
- Keep carrier actions unavailable without a number and run the reachable regression.
- Format Tasks comment timestamps for display without rewriting stored values.

* Thu Oct 08 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.99.creator20261008.1
- Resolve sent conversation identity within its account and preserve drafts, replies and attachments.
- Gate canonical conversation migration and its signed app mailbox operation.

* Wed Oct 07 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.98.creator20261007.1
- Export saved Photos crop, rotation and colour adjustments without rewriting originals.
- Verify actual GTK-owner rendering, reopened PNG pixels and bounded failure paths.

* Wed Oct 07 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.95.creator20261007.1
- Keep live collaboration events independent of unrelated calendar sync failures
- Verify partial-failure cursors, causal delivery and exact server backoff

* Wed Oct 07 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.92.creator20261006.1
- Retain submitted-comment visibility across late native focus and EDS provider redraws until user navigation.
- Check late focus, remount, and native user-scroll release in the real EDS runtime regression.

* Tue Oct 06 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.91.creator20261006.1
- Initialize the compact Tasks details panel before its first render.
- Preserve comment scroll and pending reveal across real provider readbacks.
- Gate fresh compact launches and real EDS comment submission at four widths.

* Tue Oct 06 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.88.creator20261006.1
- Keep username submission visible in a shared native bottom action bar and
  protect typed drafts from stale checks, duplicate claims and dismissed callbacks.
- Make Luma username discovery explicit without adding an email directory.

* Mon Oct 05 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.87.creator20261005.1
- Clear imported recurrence additions for an explicit Never selection
- Preserve weekday and end/count constraints when editing custom intervals
- Keep exception identity, calendar undo and unrelated task fields intact

* Mon Oct 05 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.86.creator20261005.1
- Persist Tasks preset/custom repeat rules through the existing calendar store
- Complete Messages filter and conversation menu glyphs
- Exercise actual responsive repeat controls and icon lookup in native GTK

* Mon Oct 05 2026 Luma Team <hello@project-luma.local> - 0.1.0-1.luma.85.creator20261005.1
- Carry qualified Connect57 shared sign-in UI as Source2; core runtime sources unchanged.

* Mon Oct 05 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.84.creator20261005.1
- Handle absent Write through real Depot and reduce unused root-note tree space
- Present actual Messages empty states and shared account forms with dismissible recipient picker
- Seed pristine Clock/Weather stores from Atlas and parse Tasks time in either phrase order
- Use shared Calendar button surfaces and responsive due/repeat controls

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.82.creator20261004.1
- Built-in apps: Messages, Photos and Clock join the LumaUI ports; Tasks keeps every draft.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.79.connect.60~lumaui.20260926.1
- LumaUI ports of the built-in apps, round 3.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.78.connect.60~lumaui.20260926.1
- LumaUI ports of every built-in app, with the Tasks draft-saving fixes.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.77.connect.60~lumaui.20260926.1
- Tasks keeps new comment and step drafts across reloads, failed saves and completion.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.76.connect.60~lumaui.20260926.1
- Tasks keeps title and notes drafts across a save or navigation boundary.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.75.connect.60~lumaui.20260926.1
- LumaUI port of Contacts, Notes, Calendar, Messages, Phone, Photos, Clock, Weather, Camera, Voice Memos and Tasks; lossless contact saves.

* Tue Sep 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.60~preview.20260922.1
- Notes shows pages that sync brings in while it is open: the list updates
  in place, keeping its scroll and selection, and an open page not being
  edited shows the new version. Nothing had watched the library, so pulled
  changes appeared only after Notes was reopened.
- A page being edited when another device's version arrives keeps the
  person's text; saving keeps the other version as one "(other copy)"
  instead of silently writing over it. Sync no longer writes a pulled
  version over a page saved in Notes during the pull.

* Tue Sep 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.59~preview.20260922.1
- Calendar, Messages, Phone, Contacts, Weather and Camera load their style
  sheets through the application kit, so they follow the desktop into
  Frost and Glass instead of keeping the palette they started with.

* Tue Sep 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.57~preview.20260922.1
- Messages: Try Again on a received photo that is waiting for its file asks
  the phone for it once (with luma-messages-bridges 0.11). Never for this
  account's own messages and never from an automatic retry.

* Tue Sep 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.56~preview.20260922.1
- Messages: a picture whose full-size file the phone has not published no
  longer waits behind a spinner for ever. The helper's give-up lived in its
  memory and started over on every restart, reconnect and tap. Messages now
  bounds the wait itself (10 minutes, 90 seconds after Try Again), then shows
  the picture as not available yet with Try Again; a file that arrives later
  still lands.

* Tue Sep 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.55~preview.20260922.1
- Notes: pictures no longer size the window. Every picture's width counted
  toward the editor's minimum width, so a page with a wide screenshot opened
  the window past the screen and then shrank it a few pixels a frame as the
  pictures followed the column they were holding open. Pictures now size
  themselves from the text column (never wider, never enlarged, shape kept)
  and the editor's minimum is its margins, so the window opens at its own size.

* Tue Sep 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.54~preview.20260922.1
- Calendar sync: moving Personal events into the Luma calendar is confirmed
  by each event's UID on the server. It compared the server's event count
  with the count before the move, which never grew again once an earlier pass
  had uploaded them, so every sync pass failed. A move still waiting is
  reported and retried without failing the other services, and one backup
  of unchanged events is kept instead of one per pass.

* Tue Sep 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.53~preview.20260922.1
- Notes sync both ways: pages added, changed or deleted on another device or
  on the web now reach this device (by page id, pictures fetched). A page
  changed on both sides keeps both versions once; a page deleted elsewhere
  goes to Recently Deleted here unless it was changed here since last sent.

* Mon Sep 21 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.52~preview.20260921.1
- Notes sync: a page the host keeps for another device no longer makes a
  new "(other copy)" on every sync pass. A change refused although it named
  the host's current version is not treated as a conflict and is not resent
  until the page changes; the same host version is never kept twice (logged).

* Sat Sep 19 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.51~preview.20260919.1
- Notes sync: a picture the host has no room for is marked "Not synced" on
  the page with a quiet note, and waits until the host reports less storage
  in use or a picture is removed; text keeps syncing. A picture refused as
  too large offers "Shrink and attach" and is never retried. Rate limits
  back off and retry; pictures owed are remembered so they go up later
  without resending the page. A page restored from Recently Deleted
  re-uploads pictures the host has since removed.

* Sat Sep 19 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.50~preview.20260919.1
- Notes: bullets and numbers are drawn in the text colour in light and dark;
  Enter continues a list and leaves it on an empty item; Backspace at the
  start of an item outdents, then takes the bullet off. Enter in the title
  goes to the page, and "Untitled page" is a placeholder only. A page's menu
  opens, renames, duplicates, pins, moves, exports and deletes with undo;
  double-click renames a folder; the page starts a token-sized gap below the
  title. Pictures paste and drop in, are stored once by content, fit the text
  width, and have Write's controls and menu. A picture pasted from the
  clipboard is read without blocking.
- Notes sync: debounced atomic saves, incremental coalesced pushes with
  backoff, revision conflicts that keep both versions, and pictures uploaded
  once by content hash; hosts without the incremental protocol still get
  snapshots. %%check runs the editor suite light and dark.

* Sat Sep 19 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.49~preview.20260918.1
- Contacts' search narrows the list as you type, on name, phone number and
  email address, and says so when nobody matches. It asked the address book
  service to search and showed whatever came back.
- Contacts' edit dialog and sidebar name the account new contacts go to (On
  this computer, Luma account or the online account's own name) instead of
  "prairie account".
- Phone on a computer with no modem shows no notice in the sidebar and one
  line under the keypad offering to pair a phone in Connect; with a phone
  paired through Connect, calls go through it. No text names a system tool.

* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.48~preview.20260918.1
- Contacts and Phone show their action, dialer and call-history glyphs. The
  19 glyphs were in the source tree but never packaged, and Contacts looked
  for them in a directory nothing installed. They now install into hicolor
  as fills-only symbolics, and %%check fails if either app names one that is
  missing.
- Messages no longer shows a red "Cellular messaging is unavailable" line on
  every conversation on a computer without a modem. The line for this
  device's own text service appears only once there is a draft to send, and
  every status line is now quiet secondary text rather than an error color.

* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.47~preview.20260918.1
- Messages: the open conversation stays pinned while pictures load,
  reactions arrive or the window resizes. The conversation is now its own
  scrollable view that places the rows and the scroll position in the same
  layout, anchored to the message being read (or to the end when the reader
  is there), so no frame is ever painted before the position absorbs a
  height change. GtkViewport read the position before placing the rows and
  showed a 200px jump for one frame when a tall picture finished above, and
  a 100px jump of the newest message on a resize. The scroll-anchor check
  now allows at most 1px in any painted frame and has no known failures.

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.46~preview.20260917.1
- Calendar follows ADR-043: the open details toggle takes the kit's state
  slate (luma_state) instead of an ink block, and the details panel's New
  event suggestion is an ordinary button, so today's date stays the view's
  one strong fill. Needs the luma-developer-platform release that defines
  luma_state; the Requires below is raised to it at build time.
- Notes: Don't Save closes a file window even after the sheet was cancelled
  once; the window used to stay open.
- Messages: tests/messages_scroll_anchor_runtime.py reports two known
  one-frame jumps (a tall picture finishing above, a resize at the end) as
  known failures rather than fatal ones; everything else it checks is enforced.

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.44~preview.20260917.1
- Messages: reactions sit in one pill on the bubble's top corner (trailing
  for a message sent, leading for one received), overlapping the edge within
  the bubble's padding so they never cover text, on a solid raised fill with a
  ring in the conversation's ground. Several reactions share one pill with the
  number of people beside them. The pill's room is the row's margin, so a
  reaction arriving on the message being read does not move its text
- Messages, Phone, Calendar, Clock and Weather declare
  X-GNOME-UsesNotifications, the key Settings needs before it lists an app
  under Notifications. Their per-app switches, including Badge App Icon, were
  unreachable.
- Messages keeps the reader's place: the conversation is anchored to the
  message at the top of the view (or to the newest message when you are at
  the end) instead of a pixel offset, and is corrected in the same layout pass,
  so pictures arriving, receipts, reactions, upserts, older history and resizes
  never move it. Updates replace only the messages that changed, and a click on
  a message's text no longer scrolls the conversation.
- Clock says where its full layout begins (narrow_width), because its
  breakpoints sit on the bin inside its window where the kit cannot read them.
  Without it the window could open inside its own narrow layout. Every other
  core application's breakpoints are read by the kit as they are added
  (ADR-042).
- Messages: an incoming picture whose full-size file has not been published yet
  is asked for again instead of waiting behind a spinner. A part still on its
  way now keeps the attachment pass armed; only failed parts used to, so a
  picture left pending was asked for once and then had no timer at all
- Messages: a picture whose file the phone never published is still re-read
  quietly every half hour, and at once on reconnecting or when the conversation
  is opened, for a day after it arrived, and turns into the picture if one of
  those reads finds it. Nobody has to press Try Again for a picture that was
  only waiting on the phone being awake. A file Google no longer holds, or one
  too large to fetch, is never re-read on a schedule
- Messages: a picture that cannot be fetched says so plainly and offers Try
  Again, and an incoming picture is never described as something the phone is
  sending. A person's Try Again asks the helper to read the conversation again
  ("reread"); the old "force", which older helpers documented as asking the
  phone to send the file again, is never sent
* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.40~preview.20260917.1
- Messages and Phone agents publish the standard `badge` value for their dock
  icons: Messages its unread count, Phone the missed calls not yet seen. Acting
  on a missed-call notification clears Phone's. Both declarations list `badge`.

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.39~preview.20260916.1
- Messages: a network account delivers only what a person sent, exactly once. Pressing Send, confirming Retry in Review Send, replying from a notification or choosing a reaction authorizes a request with a token stored before anything is asked; the request is claimed once in the store before the helper is asked, and a claimed request is never delivered again
- Messages: reconnecting no longer re-sends messages that were still "sending"; it reads their conversations to confirm them and marks any it cannot confirm for review. A send that timed out or whose helper stopped is marked Needs Review and only the person's Retry sends it again. Messages from before this release that never confirmed are never sent automatically
- Messages: a kill switch stops all sending from accounts; too many deliveries in a minute, or a delivery without a person's request, trips it and tells the person, and Accounts offers Turn Sending On
- Messages: Try Again on a picture only downloads again; it never asks the phone for anything. Requires luma-messages-bridges 0.9 (older helpers conflict)

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.38~preview.20260916.1
- Messages: clicking a picture opens it in the window's lightbox (luma-developer-platform 1.luma.68) instead of Viewer or another app: it grows from the thumbnail, zooms and pans, steps through every picture in the conversation with sender and time, and shows downloading and no-longer-available pictures with Try Again; Save As…, Copy and Open With… are in its bar. Closing returns focus to the picture
- Requires prairie-icon-theme 1.luma.28 for its pan chevrons

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.37~preview.20260916.1
- Messages: a picture Google Messages no longer holds says so and offers Try Again, which asks your phone to send it, instead of retrying forever as "didn't download yet"; it is not asked for again on a schedule (needs luma-messages-bridges 0.8)

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.36~preview.20260916.1
- Messages: the Accounts button is gone from the desktop sidebar header; Accounts… stays in the app menu with Ctrl+, and a handset, which has no app menu, keeps the button

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.35~preview.20260916.1
- Build: the build script produces Source2 (Luma Continuity's source) and checks every declared Source exists; the BuildRequires cycle with luma-continuity is gone, so a clean builder can build this package from the repository
- Messages: messages you send from your phone are never unread; a message stored as incoming by an older helper is corrected when seen again, and reading on the phone clears unread here (needs luma-messages-bridges 0.7)
- Messages: pictures show in full at their own shape, rounded, with a spinner over the preview while they download, Try Again when they fail and their real size; a kilobyte preview saved as the picture is replaced by the full file; pixels decode off the main thread into a bounded cache and are released out of view; clicking opens the photo in Viewer
- Messages: the conversation no longer jumps to the bottom on redraws, reconnects, receipts or picture updates; it follows new messages only when you are at the end, or after you send
- Messages: every conversation row is the same roomier height with one line of name and one of preview; no service callout; unread rows are bold with an accent time and dot, read rows are not; avatars show initials, or a person, business or group glyph, never a stray character; a wider sidebar
- Messages: the right-click, long-press and Menu-key menu is the kit menu at the pointer with Open, Call (when there is a number to call), Mark as Read or Unread, and Delete with confirmation
- Messages: the list, recipients and conversation scroll with the kit's overlay scrollbar, with no gutter reserved for it
- Messages: the header names the service alone ("Google Messages", not "Chat · Google Messages") and the three-dot button, which did nothing useful, is gone; Call works for a Google Messages conversation with one other person
- Messages: every glyph is Prairie's Lucide set by standard name (attachment, send, call, video, microphone, compose, accounts, back); none are drawn by hand

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.34~preview.20260916.1
- Messages: the window no longer leaks every conversation row and message bubble it replaces. Each row's right-click gesture and each bubble's action gestures held a closure over the widget that owned them, a cycle Python's collector cannot see through GTK, so every re-render (each time an account dropped and reconnected, every few seconds on a flaky network) kept the whole list, pictures included; Messages left in the background grew by about 360 MB an hour, to 8 GB overnight. Handlers now reach their widget through the gesture, and context menus are released once closed
- Messages: a runtime test re-renders the list and an open conversation with pictures 150 times and checks the widget count and resident memory stay flat

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.33~preview.20260915.1
- Clock: the computer is woken a minute before the next alarm, snooze or timer when it is asleep, through luma-background's wake helper (org.projectluma.BackgroundWake1); the wake-up moves or is cleared whenever the plan changes
- Clock: from two minutes before an alarm or timer until it is answered, sleep and idle are held off with systemd-logind's block inhibitor and GNOME's session inhibitor, in the windowless agent too, and taken again if logind restarts; the critical ringing notification wakes a dark screen
- Clock: with luma-background running its agent, Clock no longer asks the Background portal for an autostart entry of its own, so it cannot start twice at login

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.32~preview.20260915.1
- Background agents (ADR-033): Messages, Phone, Calendar and Clock keep working with their windows closed through small windowless agents declared in /usr/share/luma/background for luma-background, which runs them under the category's limits; single-instance by D-Bus name, publishing values on org.projectluma.BackgroundAgent1 for live extensions, using luma_appkit.background when installed; autostart entries start them at login until the service is installed
- Messages: the agent keeps network accounts connected, announces new messages with Reply, a reaction and Mark as Read, keeps sender and text off a locked screen, downloads pictures on the media queue and publishes the unread count; the window is its client, so an account is never connected twice, and closing the window now really closes it
- Messages: messages that arrived while the computer slept, was offline or was off are announced when the account reconnects
- Messages: a message sent while an account connects is no longer sent twice
- Phone: with a phone chosen for calls in Luma Connect, incoming calls ring with Answer and Decline while Phone is closed, and unanswered ones become Missed Call with Call Back and Message
- Calendar: reminders for every calendar in Evolution Data Server, including Luma Connect's shared calendars, with Open, Snooze and Dismiss, shown after resume or at the next login when they fell due meanwhile; evolution-alarm-notify is held off so no reminder shows twice; a reminder opens Calendar on its event
- Clock: prairie-clock --agent is a windowless alarms agent without GTK that rings alarms and timers under luma-background, publishes the next one, and exits when nothing is scheduled; while it runs, Clock windows leave ringing to it; without luma-background Clock rings alarms itself as before
- Luma Connect sync runs at wall-clock times and catches up at once after sleep
- The flaky picture tests in the Messages accounts suite wait for the account's connect pass

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.31~preview.20260915.1
- Clock: the window opens again when world clocks are saved
- Clock: alarms and timers ring from Clock itself while it holds itself open, started at login through the Background portal, so they work in a Flatpak too; the old systemd alarm units are withdrawn and hand over on first use
- Clock: missed and late alarms are reported, one-time alarms turn off instead of disappearing, snoozes survive a restart, and a week-ahead alarm keeps its time across a daylight-saving change
- Luma Connect: Notes, Weather places, world clocks and Leaf reading sync with whichever install of each app is in use, the system app or its Flatpak

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.30~preview.20260915.1
- Messages accounts: pictures that were still on their way when their message arrived now join it, including messages with text and group MMS; before, they were dropped for good
- Messages accounts: each network attachment keeps its state; one that hasn't arrived shows Downloading, Waiting for your phone, or Try Again instead of vanishing, and is asked for again when the account reconnects, when a retry falls due, when its conversation opens and when tapped
- Messages accounts: pictures earlier Google Messages helpers replaced with "couldn't be downloaded" text are fetched again on the next start
- Messages: a photo this system can't decode (HEIC without a HEVC decoder) shows the network's thumbnail or says so, instead of a blank card; pictures are decoded once at display size
- Messages accounts: after sleep or a network change, conversations that changed meanwhile are fetched again

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.29~preview.20260915.1
- Make Depot the default handler for appstream:// and luma-depot:// links in the system mimeapps.list, so Open in Depot links and appstream links from other software open Depot rather than GNOME Software

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.28~preview.20260914.1
- Luma Connect sync: `luma-connect-sync profile` shows the account's name, email, phone number and sign-in, and `profile set` changes them; typed addresses and numbers are reported as not verified
- The status report carries the profile, and the hub's own reason is shown when it refuses a change

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.27~preview.20260914.1
- Messages: right-click or press and hold a message to react or reply; the message text no longer swallows those gestures
- Messages: react with the reactions a network offers, change the reaction or take it back; a modem keeps the heart-as-text option

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.26~preview.20260914.1
- Messages: an account still connecting after a minute is restarted, so Messages no longer waits at Connecting after sign-in
- Messages: the background start at sign-in runs again; the autostart entry no longer names a session phase GNOME ignores

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.25~preview.20260914.1
- Messages: accounts reconnect by themselves after sleep, when the network returns, and when a connection stays broken, so messages no longer wait at Sending for Try Again
- Messages: sent messages from network accounts show Delivered and Read
- Messages: a message arriving in the conversation on screen is marked read for its sender

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.24~preview.20260914.1
- Messages: closing the window keeps accounts connected in the background so new messages are announced by the system; Quit Messages ends it
- Messages: starts hidden at login when a messaging account is set up

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.23~preview.20260914.1
- Messages: every network conversation loads, not only the newest twenty, and opening one fetches its latest messages
- Messages: reactions from network accounts appear, including on messages received before, and removals are reflected

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.22~preview.20260914.1
- Account attachments keep the type the network reports, stored pictures without one are recognised by their first bytes, and every image type the system can decode previews (HEIC with a HEIF loader)
- Opening the same message store from Messages, its daemon and account providers at once no longer fails with database is locked

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.21~preview.20260914.1
- Messages desktop notifications work again: both this device's texts and network accounts called a GLib method name PyGObject does not expose, so every notification raised AttributeError

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.20~preview.20260914.1
- Messages accounts: retry a failed first conversation sync, show helper warnings in the journal, and never wait for a helper when quitting

* Sun Sep 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.19~preview.20260913.1
- Messages accounts (ADR-023): an Accounts dialog to add, reconnect and remove Google Messages and WhatsApp accounts run by local helpers, with keyring-only sessions, group senders and network notifications
- The QR encoder moves here as prairie_apps.qr_code

* Sun Sep 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.18~preview.20260913.1
- Messages shows this device's messages and every Luma Connect phone in one inbox; each conversation names and sends through its own service, and a new message offers Send with (ADR-022)
- Package checks build against luma-continuity 0.50

* Sun Sep 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.17~preview.20260913.1
- Leaf reading position and library sync through Luma Hub, with a Books switch (Luma coordinator 292c4106)
- Package checks build against luma-continuity 0.49

* Sat Sep 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.16~preview.20260912.1
- Receive other devices' messages and call history from Luma Hub into a private read-only cache; forget it when the switch goes off or on sign-out
- Messages shows Luma Cloud history beside local threads without writing it into the local store
- Phone on a paired computer: calls placed or answered there use the computer's microphone and speakers by default
- Phone Recents shows other devices' call history from Luma Cloud, read-only
- Messages keeps retrying a sleeping phone or a login-time keyring race, offers Retry, and revalidates after Unlock
- Weather: avoid the libgweather use-after-free in package checks (Luma coordinator 1d5dbd95)

* Sat Sep 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.15~preview.20260912.1
- Messages and call history switches (off by default) from the Luma coordinator branch bb88ac7d, wired to the device log adapter

* Sat Sep 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.14~preview.20260912.1
- Contacts sync two-way over CardDAV with one per-device DAV login (Luma coordinator branch c90ebbc2)
- Messages and call history change logs to Luma Hub, off until explicitly enabled
- Run the Connect sync unit suites in package checks

* Sat Sep 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.74.connect.13~preview.20260912.1
- Converge the ThinkPad sync branch (Luma Hub connect sync, Clock, Weather, Calendar editor) with the installed connect.12 Phone/Messages lineage
- Ship luma-connect-sync and its user units on desktop and handheld from one package

* Sun Sep 06 2026 Project Luma <build@projectluma.invalid> - 0.1.0-1.luma.71~preview.20260906.1
- Share the Notes formatting toolbar with external-file windows
- Keep file Save and Save As in the application menu

* Sun Sep 06 2026 Project Luma <build@projectluma.invalid> - 0.1.0-1.luma.68~preview.20260906.1
- Open text files in independent Notes windows without library or history writes
- Make Notes the default plain text and Markdown handler

* Fri Sep 04 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.58
- Notes, Contacts, Messages and Tasks use the kit's island split views

* Fri Sep 04 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.57
- Clock, Weather, Tasks and Voice Memos are the Application Kit's window; prairie_ui is a shim over luma_appkit; Photos and Camera name their palette once

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.33
- Consume the shared native chrome and split-island boundary in Notes.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.32
- Align the Prairie runtime smoke contract with AppKit native decoration.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31
- Recompose Notes against the simulator-parity AppKit elevation contract.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.30
- Consume the corrected shared AppKit window, island, title, and toolbar contract

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.29
- Align Notes with the authoritative Application Kit composition and controls.
- Restore the persistent bottom New note action and canonical formatting glyphs.

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.28
- Restore authenticated Notes semantic publication over stable SQLite IDs.

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.27
- Render the Notes link editor through the shared Luma popover material.

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.26
- Complete the Notes desktop foundation with grouped formatting controls,
  inline rename, durable cross-folder moves, constrained reorder, and links.

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.25
- Rebuild Notes on the shared Luma Application Kit with durable rich editing,
  folders, favorites, ordering, autosave, and recoverable deletion

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.24
- Keep Luma MIME defaults in the system XDG configuration layer

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.23
- Route every supported application package through the shared Luma installer

* Thu Aug 27 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.22
- Select the live voice transport at dial time so recovered IMS service is
  used without restarting Phone
- Keep each call bound to its selected transport through hangup

* Thu Aug 27 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.21
- Publish Notes semantic application and document actions through Luma Semantics
- Require the shared Luma Developer Platform on desktop and handheld

* Wed Aug 19 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.20
- Fall back to the immutable device-class marker when a launcher drops the
  explicit session environment, preserving shared mobile chrome and touch mode

* Tue Aug 18 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.19
- Suspend the FP6 preview outside the foreground and bound compositor pressure
- Bind Main, Wide, and Front labels and default order to physical sensor IDs
- Cancel pending switch/still work safely when Camera loses the foreground

* Tue Aug 18 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.18
- Select the longer-exposure FP6 Wide sensor mode below the shared Camera UI
- Make camera switching wait for release and shorten rear autofocus scans

* Mon Aug 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.17
- Replace the Camera placeholder with a shared adaptive low-latency preview
- Negotiate a bounded 960x540 camera stream and provide switching and refocus

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.16
- Promote the converged application suite as a complete desktop product surface
- Complete desktop launcher metadata while retaining the same adaptive binaries

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.15
- Add full-thread search, EDS Phone contacts, saved-place switching, and live clocks
- Harden Weather request shutdown and lower unavailable-modem polling cost

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.14
- Remove fired one-shot alarm records and units after notification
- Finalize runtime-only smoke coverage and production-readiness documentation

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.13
- Add the idempotent background incoming-SMS user service
- Serialize UI modem polling and recover interrupted outgoing delivery state

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.12
- Complete contact and event edit/delete plus Luma URI handoffs
- Add task notes/dates, photo date grouping/swipes, seven-day weather, and full-note search

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.11
- Add idempotent inbound SMS import and ModemManager voice-call operations
- Add Luma call history, saved weather places, and the Voice Memos media lifecycle

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.10
- Replace Messages sample data with a private real store and ModemManager send adapter
- Add persistent world clock, alarm, stopwatch-lap, and timer implementations

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.9
- Finish the first Notes lifecycle and add real Photos/Weather backends

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.8
- Add real EDS CRUD adapters and functional Contacts, Calendar, and Tasks flows

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.7
- Complete the eleven-app responsive proof suite with Photos, Weather, and Clock

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.6
- Add portable Markdown Notes and hardware-gated Voice Memos

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.5
- Add the EDS-backed Contacts, Calendar, and Tasks responsive proof surfaces

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4
- Add truthful Camera and Phone hardware-risk proof surfaces

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.3
- Make expanded navigation explicit and add wide/compact runtime smoke lanes

* Wed Aug 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- Compose the empty-state wrapper around final AdwStatusPage

* Wed Aug 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- Add the shared responsive toolkit and Messages Stage 0 proof
