#!/bin/sh
# SPDX-License-Identifier: Apache-2.0

set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
source_file="$repo_root/src/luma-greeter/luma-greeter.c"
session_source="$repo_root/src/luma-greeter/luma-greeter-session.c"
css="$repo_root/src/luma-greeter/luma-greeter.css"
fake_greetd="$repo_root/tests/mobile/luma-greeter-fake-greetd.py"
greetd="$repo_root/config/mobile/fp6-physical/overlay/etc/greetd/config.toml"
spec="$repo_root/packaging/rpm/luma-greeter.spec"
brightness_rule="$repo_root/packaging/polkit/60-luma-greeter-brightness.rules"

check() {
  label=$1
  shift
  if "$@"; then
    printf 'PASS  %s\n' "$label"
  else
    printf 'FAIL  %s\n' "$label" >&2
    exit 1
  fi
}

check 'compiled native greeter package' grep -Fq '%{__cc}' "$spec"
check 'no browser or user service dependency' sh -c \
  '! grep -Eiq "web(view|kit)|waydroid|systemd.*user|phosh.*extension" "$1" && ! sed -n "1,/^%changelog/p" "$2" | grep -Eiq "web(view|kit)|waydroid|systemd.*user|phosh.*extension"' _ \
  "$source_file" "$spec"
check 'greetd PAM IPC is the sole credential path' grep -Fq 'create_session' "$source_file"
check 'credential worker wipes its bounded buffer' grep -Fq \
  'secure_clear(request->pin, sizeof(request->pin))' "$source_file"
check 'exact four-digit transient storage' grep -Fq '#define LUMA_PIN_DIGITS 4' "$source_file"
check 'actual account name source' grep -Fq 'getpwnam(username)' "$source_file"
check 'actual AccountsService avatar source' grep -Fq \
  '/var/lib/AccountsService/icons/%s' "$source_file"
check 'both clock states use the actual local time with meridiem' sh -c \
  'grep -Fq "g_date_time_new_now_local" "$1" && grep -Fq "%-I:%M %p" "$1"' _ "$source_file"
check 'quiet and passcode clocks share the 46px reference size' sh -c \
  'test "$(grep -c "font-size: 46px" "$1")" -eq 2' _ "$css"
check 'account avatar has an exact square cover crop' sh -c \
  'grep -Fq "gtk_widget_set_size_request(picture, 68, 68)" "$1" && grep -Fq "gtk_widget_set_halign(frame, GTK_ALIGN_CENTER)" "$1" && grep -Fq "gtk_widget_set_hexpand(frame, FALSE)" "$1" && grep -Fq "GTK_CONTENT_FIT_COVER" "$1" && grep -Fq "max-width: 68px" "$2" && grep -Fq "max-height: 68px" "$2"' _ "$source_file" "$css"
check 'quiet unlock hint is bottom-center and outside the clock block' sh -c \
  'grep -Fq "gtk_widget_set_halign(hint, GTK_ALIGN_CENTER)" "$1" && grep -Fq "gtk_box_append(GTK_BOX(page), hint)" "$1" && grep -Fq "margin-bottom: 36px" "$2"' _ "$source_file" "$css"
check 'PIN screen supports a native left-edge back gesture' sh -c \
  'grep -Fq "gtk_gesture_drag_new" "$1" && grep -Fq "start_x <= 36.0" "$1" && grep -Fq "offset_x >= 64.0" "$1" && grep -Fq "show_clock(greeter)" "$1"' _ "$source_file"
check 'quiet state excludes identity controls' sh -c \
  'start=$(grep -n "build_clock_page" "$1" | head -1 | cut -d: -f1); end=$(grep -n "build_avatar" "$1" | head -1 | cut -d: -f1); sed -n "${start},${end}p" "$1" | grep -Fq "Tap to unlock" && ! sed -n "${start},${end}p" "$1" | grep -Eq "Emergency call|passcode-dot|avatar-frame"' _ "$source_file"
check 'identity zone expands between date and keypad' grep -Fq \
  'gtk_widget_set_vexpand(zone, TRUE)' "$source_file"
check 'lower-left keypad position is absent' sh -c \
  '! grep -Fq "GTK_GRID(grid), " "$1" || true; ! grep -Fq "grid), 0, 3" "$1"' _ "$source_file"
check 'correct backspace accessible name' grep -Fq 'Delete last digit' "$source_file"
check 'one visible emergency label' sh -c \
  '[ "$(grep -F "gtk_label_new(\"Emergency call\")" "$1" | wc -l | tr -d " ")" = 1 ]' _ "$source_file"
check 'emergency pill opens a confirmation keypad without dialing' sh -c \
  'start=$(grep -n "^static void emergency_clicked" "$1" | head -1 | cut -d: -f1); end=$(grep -n "^static gboolean emergency_service_available(void) {" "$1" | head -1 | cut -d: -f1); block=$(sed -n "${start},${end}p" "$1"); printf "%s" "$block" | grep -Fq "visible_child_name(greeter->stack, \"emergency\")" && ! printf "%s" "$block" | grep -Fq "DialEmergency"' _ "$source_file"
check 'pre-session ABI exposes emergency calls only' sh -c \
  'grep -Fq "DialEmergency" "$1" && grep -Fq "GetEmergencyCallState" "$1" && grep -Fq "HangUpEmergency" "$1" && ! grep -Fq "GetCalls" "$1"' _ "$source_file"
check 'no password field or software keyboard' sh -c \
  '! grep -Eq "GtkEntry|GtkPasswordEntry|InputPurpose|osk|keyboard" "$1"' _ "$source_file"
check 'Figtree and reference keypad geometry' sh -c \
  'grep -Fq "font-family: \"Figtree\"" "$1" && grep -Fq "min-width: 288px" "$1" && grep -Fq "min-height: 64px" "$1"' _ "$css"
check 'production handheld boots into its native locked Phosh session' sh -c \
  'grep -Fq "[initial_session]" "$1" && grep -Fq "command = \"/usr/local/bin/luma-phosh-session\"" "$1" && grep -Fq "user = \"luma\"" "$1"' _ "$greetd"
check 'production greetd retains Presence only as recovery' grep -Fq \
  'command = "/usr/libexec/luma-presence-compositor"' "$greetd"
check 'successful authentication paints a wallpaper-only handoff frame' sh -c \
  'grep -Fq "begin_graphical_handoff" "$1" && grep -Fq "_exit(EXIT_SUCCESS)" "$1"' _ "$source_file"
check 'physical FP6 recovery greeter retains one compositor across authentication' sh -c \
  'grep -Fq "persistent_compositor = true" "$1" && grep -Fq "/usr/libexec/luma-presence-compositor" "$1"' _ "$greetd"
check 'native compositor initializer uses no shell or user setting' sh -c \
  'grep -Fq "execl(WLR_RANDR" "$1" && grep -Fq "execl(LUMA_GREETER" "$1" && ! grep -Eq "system\\(|/bin/(ba)?sh|gsettings|dconf" "$1"' _ "$session_source"
check 'pre-session Presence blanks after bounded inactivity' sh -c \
  'grep -Fq "LUMA_IDLE_SECONDS 30" "$1" && grep -Fq "blank_idle_display" "$1" && grep -Fq "activity_event" "$1"' _ "$source_file"
check 'first contact only wakes the idle display' grep -Fq \
  'The first contact wakes the display' "$source_file"
check 'greetd receives only the logind brightness action' sh -c \
  'grep -Fq "org.freedesktop.login1.set-brightness" "$1" && grep -Fq "subject.user" "$1" && grep -Fq "greetd" "$1" && test "$(grep -c "action.id" "$1")" -eq 1' _ "$brightness_rule"
check 'behavioral peer records only message-type events' grep -Fq \
  'args.result.write_text("\n".join(events)' "$fake_greetd"
check 'behavioral peer validates the authenticated session command' grep -Fq \
  '/opt/luma/phosh/bin/phosh-session' "$fake_greetd"
