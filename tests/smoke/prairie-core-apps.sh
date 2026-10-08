#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

failures=0
check() {
  description=$1
  shift
  if "$@"; then
    printf 'PASS  %s\n' "$description"
  else
    printf 'FAIL  %s\n' "$description"
    failures=$((failures + 1))
  fi
}

check 'Prairie core apps package' rpm -q prairie-core-apps
check 'Messages launcher' test -x /usr/bin/prairie-messages
check 'Messages background importer' test -x /usr/bin/prairie-messages-daemon
check 'Messages native notification publisher' bash -c \
  'grep -Fq "org.freedesktop.Notifications" /usr/bin/prairie-messages-daemon && grep -Fq '\''"mark-read", "Mark read"'\'' /usr/bin/prairie-messages-daemon && ! grep -Fq "notify-send" /usr/bin/prairie-messages-daemon'
check 'Camera launcher' test -x /usr/bin/prairie-camera
check 'Phone launcher' test -x /usr/bin/prairie-phone
check 'Contacts launcher' test -x /usr/bin/prairie-contacts
check 'Calendar launcher' test -x /usr/bin/prairie-calendar
check 'Tasks launcher' test -x /usr/bin/prairie-tasks
check 'Notes launcher' test -x /usr/bin/prairie-notes
check 'Voice Memos launcher' test -x /usr/bin/prairie-voice-memos
check 'Photos launcher' test -x /usr/bin/prairie-photos
check 'Weather launcher' test -x /usr/bin/prairie-weather
check 'Clock launcher' test -x /usr/bin/prairie-clock
check 'Clock alarm helper' test -x /usr/bin/prairie-clock-alarm
check 'Clock alarm service activation' grep -Fxq 'Exec=/usr/bin/prairie-clock --gapplication-service' /usr/share/dbus-1/services/org.projectluma.Clock.service
check 'Messages desktop entry command' grep -Fxq \
  'Exec=prairie-messages' \
  /usr/share/applications/org.projectluma.Messages.desktop
check 'shared Prairie style' test -r /usr/share/prairie-core/prairie.css
check 'Messages user service' test -r /usr/lib/systemd/user/prairie-messages-daemon.service
check 'shared toolkit imports' python3 -c \
  'from prairie_ui.context import PrairieContext; assert PrairieContext.from_environment({"LUMA_DEVICE_CLASS":"handheld"}).input_mode.value == "touch"'
check 'Messages module imports' python3 -c \
  'from prairie_apps.messages import MessagesApplication; assert MessagesApplication'
check 'Camera module imports' python3 -c \
  'from prairie_apps.camera import CameraApplication; assert CameraApplication'
check 'Phone module imports' python3 -c \
  'from prairie_apps.phone import PhoneApplication; assert PhoneApplication'
check 'EDS module imports' python3 -c \
  'from prairie_apps.eds_backend import inspect_eds_inventory; assert inspect_eds_inventory'
check 'Stage 2 modules import' python3 -c \
  'from prairie_apps.contacts import ContactsApplication; from prairie_apps.calendar import CalendarApplication; from prairie_apps.tasks import TasksApplication'
check 'functional backend modules import' python3 -c \
  'from prairie_apps.messages_backend import MessageStore, ModemMessagingTransport; from prairie_apps.phone_backend import CallStore, ModemVoiceTransport; from prairie_apps.audio_backend import list_recordings; from prairie_apps.clock_backend import ClockStore; from prairie_apps.weather_backend import PlaceStore'
check 'Messages claims SMS URI ownership' grep -Fxq \
  'MimeType=x-scheme-handler/sms;' \
  /usr/share/applications/org.projectluma.Messages.desktop
check 'Phone claims tel URI ownership' grep -Fxq \
  'MimeType=x-scheme-handler/tel;' \
  /usr/share/applications/org.projectluma.Phone.desktop
check 'one app binary declares both form factors' grep -Fq \
  'X-Purism-FormFactor=Workstation;Mobile;' \
  /usr/share/applications/org.projectluma.Messages.desktop

for app_id in Messages Phone Contacts Calendar Weather Tasks VoiceMemos Notes Photos Clock Camera; do
  check "${app_id} is a desktop-visible shared launcher" \
    bash -c "grep -Fxq 'X-Purism-FormFactor=Workstation;Mobile;' '/usr/share/applications/org.projectluma.${app_id}.desktop' && ! grep -Eq '^(NoDisplay=true|OnlyShowIn=.*Mobile)' '/usr/share/applications/org.projectluma.${app_id}.desktop'"
done

# desktop-file-validate belongs to the package build lane. Runtime images are
# intentionally not required to carry desktop-file-utils solely for smoke.

exit "$failures"
