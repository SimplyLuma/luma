#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Settings › Notifications › app: the Badge App Icon switch, driven in Xvfb.
# It opens the app's page, reads the accessibility tree (so the row's name and
# subtitle are recorded), toggles the switch and reads
# org.gnome.shell dock-badges-disabled-apps back after each change.
#
#   $1: light|dark      APP: the app's name in the list (default Messages)
#   OTHER: another app already switched off, to prove the list is preserved.
#
# GTK 4 reports no usable accessibility coordinates and no actions on rows, so
# the clicks are XTEST clicks at the rows' places in a fixed 1000x1100 screen.
set -u
TAG=${1:-light}; OUT=${OUT:-/oracle/out/settings}; mkdir -p "$OUT"
APP=${APP:-Messages}; OTHER=${OTHER:-org.projectluma.Notes}; APP_ID=${APP_ID:-org.projectluma.Messages}
ROW_X=${ROW_X:-570}; ROW_Y=${ROW_Y:-412}          # the app's row in the list
SWITCH_X=${SWITCH_X:-807}; SWITCH_Y=${SWITCH_Y:-197}  # Badge App Icon's switch
export HOME=/tmp/settings-home-$TAG XDG_RUNTIME_DIR=/tmp/settings-run-$TAG
rm -rf "$HOME" "$XDG_RUNTIME_DIR"; mkdir -p "$HOME" "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
export XDG_CURRENT_DESKTOP=GNOME GDK_BACKEND=x11 GSK_RENDERER=cairo LIBGL_ALWAYS_SOFTWARE=1 GTK_A11Y=atspi
case "$TAG" in dark) export ADW_DEBUG_COLOR_SCHEME=prefer-dark ;; *) export ADW_DEBUG_COLOR_SCHEME=prefer-light ;; esac
Xvfb :82 -screen 0 1000x1100x24 >/dev/null 2>&1 & XVFB=$!
export DISPLAY=:82; sleep 2
export OUT TAG APP OTHER APP_ID ROW_X ROW_Y SWITCH_X SWITCH_Y
dbus-run-session -- bash -c '
  set -u
  fail=0
  say() { printf "%s %s %s\n" "$1" "$2" "${3:-}"; [ "$1" = PASS ] || fail=1; }
  get() { gsettings get org.gnome.shell dock-badges-disabled-apps; }
  /usr/libexec/at-spi-bus-launcher --launch-immediately >/dev/null 2>&1 &
  sleep 1
  # Another app already has its badge off: its choice must survive.
  gsettings set org.gnome.shell dock-badges-disabled-apps "[\"$OTHER\"]"
  gnome-control-center notifications > "$OUT/$TAG-settings.log" 2>&1 &
  sleep 8
  xdotool mousemove "$ROW_X" "$ROW_Y" click 1; sleep 2
  import -window root "$OUT/$TAG-app-page.png"
  python3 /oracle/t/atspi-tree.py > "$OUT/$TAG-tree-app-page.txt" 2>/dev/null
  grep -q "switch: Badge App Icon" "$OUT/$TAG-tree-app-page.txt" \
    && say PASS "the Badge App Icon row is on the app page" \
    || say FAIL "no Badge App Icon row on the app page"
  grep -q "dock icon" "$OUT/$TAG-tree-app-page.txt" \
    && say PASS "the row says what it does" || say FAIL "the row has no subtitle"
  before=$(get)
  case "$before" in *"$APP_ID"*) say FAIL "$APP starts switched off" "$before" ;;
                    *) say PASS "$APP starts switched on" "$before" ;; esac
  xdotool mousemove "$SWITCH_X" "$SWITCH_Y" click 1; sleep 2
  import -window root "$OUT/$TAG-off.png"
  off=$(get)
  case "$off" in *"$APP_ID"*) say PASS "switching it off lists $APP" "$off" ;;
                 *) say FAIL "switching it off did not list $APP" "$off" ;; esac
  case "$off" in *"$OTHER"*) say PASS "the other app stays off" "$off" ;;
                 *) say FAIL "the other app lost its setting" "$off" ;; esac
  xdotool mousemove "$SWITCH_X" "$SWITCH_Y" click 1; sleep 2
  import -window root "$OUT/$TAG-on.png"
  on=$(get)
  case "$on" in *"$APP_ID"*) say FAIL "switching it on left $APP listed" "$on" ;;
                *) say PASS "switching it on removes $APP" "$on" ;; esac
  case "$on" in *"$OTHER"*) say PASS "the other app is still off at the end" "$on" ;;
                *) say FAIL "the other app lost its setting" "$on" ;; esac
  # A change made elsewhere must leave the row off; the screenshot shows it.
  gsettings set org.gnome.shell dock-badges-disabled-apps "[\"$OTHER\", \"$APP_ID\"]"
  sleep 1.5
  import -window root "$OUT/$TAG-followed.png"
  python3 /oracle/t/atspi-tree.py > "$OUT/$TAG-tree-followed.txt" 2>/dev/null
  case "$(get)" in *"$APP_ID"*) say PASS "the page follows a change made elsewhere" "$(get)" ;;
                   *) say FAIL "the key did not keep the app off" "$(get)" ;; esac
  exit $fail
' | tee "$OUT/$TAG-drive.log"
status=${PIPESTATUS[0]}
kill $XVFB 2>/dev/null
echo "== $TAG (exit $status)"
exit $status
