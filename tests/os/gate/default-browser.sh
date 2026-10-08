#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Release gate checks: Viola (the viola-browser-stable image RPM) is Luma's
# built-in default web browser. Run as root inside a disposable VM installed
# from the candidate image or from a nightly medium. Needs no display and no
# network. Prints one JSON line per check ({"check", "result":
# "pass"|"fail"|"skip", "detail"}) and exits 1 if any check failed. The
# throwaway accounts it creates are removed again.
#
#   default-browser.sh
#
# Every check runs as a fresh account that never chose a browser, in the
# environment its GNOME session would have: XDG_CURRENT_DESKTOP=GNOME plus
# what systemd's environment.d generators give a user session (the launcher
# policy's XDG_DATA_DIRS and DCONF_PROFILE).
#
# Native-only deployments keep the hidden canonical MIME identity and visible
# native alias. On Luma OS the immutable signed role instead makes the canonical
# desktop the visible launcher, default dock favorite and GTK application ID.
# Invalid ownership cannot silently fall back to the native profile.
set -uo pipefail
default_id=com.rhyme.viola.desktop
launcher_id=viola-browser.desktop
# Viola with the Luma native integration presents its windows as the native
# host's application ID; the standalone engine window uses com.rhyme.viola.
native_host=/usr/share/viola-browser/luma-host/integrated_window.py
if [ -f "$native_host" ]; then wm_class=org.projectluma.Viola.NativeIntegration; else wm_class=com.rhyme.viola; fi
# A malformed immutable role is a failure, never a native fallback.
if ! browser_role=$(python3 -c "from luma_installer.native_app_roles import required; print('signed' if required('com.rhyme.viola') else 'native')"); then
  printf '%s\n' '{"check":"browser-ownership","result":"fail","detail":"invalid immutable browser role"}'
  exit 1
fi
if [ "$browser_role" = signed ]; then
  launcher_id=com.rhyme.viola.desktop
  wm_class=com.rhyme.viola
fi

user=luma-gate-browser
chooser=luma-gate-browser-chose
failed=0
work=$(mktemp -d /var/tmp/luma-gate-browser.XXXXXX)
chmod 0755 "$work"

emit() {
  python3 -c 'import json, sys; print(json.dumps({"check": sys.argv[1], "result": sys.argv[2], "detail": sys.argv[3][-600:]}))' "$1" "$2" "$3"
  [ "$2" = fail ] && failed=1
  return 0
}
cleanup() {
  for u in "$user" "$chooser"; do
    pkill -KILL -u "$u" >/dev/null 2>&1 || true
  done
  sleep 1
  for u in "$user" "$chooser"; do
    userdel -r "$u" >/dev/null 2>&1 || true
  done
  rm -rf "$work"
}
trap cleanup EXIT

if ! rpm -q viola-browser-stable >/dev/null 2>&1; then
  emit viola-installed fail 'viola-browser-stable is not installed'
  exit 1
fi
emit viola-installed pass "$(rpm -q viola-browser-stable)"

# session_env USER: the environment a GNOME session of USER starts apps with.
session_env() {
  local u=$1 home runtime line gen
  home=$(getent passwd "$u" | cut -d: -f6)
  runtime="$work/run-$u"
  install -d -m 0700 -o "$u" -g "$u" "$runtime"
  env_lines=("HOME=$home" "USER=$u" "LOGNAME=$u" "PATH=/usr/local/bin:/usr/bin:/bin"
             "XDG_CURRENT_DESKTOP=GNOME" "XDG_SESSION_DESKTOP=gnome" "XDG_SESSION_TYPE=wayland"
             "XDG_RUNTIME_DIR=$runtime" "DBUS_SESSION_BUS_ADDRESS=unix:path=$runtime/no-session-bus")
  for gen in /usr/lib/systemd/user-environment-generators/*; do
    [ -x "$gen" ] || continue
    while IFS= read -r line; do
      case $line in [A-Za-z_]*=*) env_lines+=("$line") ;; esac
    done < <(cd /tmp && runuser -u "$u" -- env -i "${env_lines[@]}" "$gen" 2>/dev/null)
  done
}
as_session() {
  # as_session USER COMMAND...: run COMMAND in USER's session environment.
  local u=$1
  shift
  session_env "$u"
  (cd /tmp && runuser -u "$u" -- env -i "${env_lines[@]}" "$@")
}

useradd -m "$user" || { emit fresh-account fail "useradd $user failed"; exit 1; }
home=$(getent passwd "$user" | cut -d: -f6)
if [ -e "$home/.config/mimeapps.list" ] || [ -e "$home/.local/share/applications/mimeapps.list" ]; then
  emit fresh-account fail 'a new account already carries a personal mimeapps.list'
else
  emit fresh-account pass "$user has no personal browser choice"
fi

# 1. xdg-settings, as Viola itself asks.
out=$(as_session "$user" xdg-settings get default-web-browser 2>&1)
if [ "$out" = "$default_id" ]; then emit xdg-settings-default-web-browser pass "$out"
else emit xdg-settings-default-web-browser fail "expected $default_id, got: $out"; fi
out=$(as_session "$user" xdg-settings check default-web-browser "$default_id" 2>&1)
if [ "$out" = yes ]; then emit viola-sees-itself-default pass "xdg-settings check default-web-browser $default_id: yes"
else emit viola-sees-itself-default fail "xdg-settings check default-web-browser $default_id: $out"; fi

# 2. gio, for every web type the image maps.
bad=
for type in x-scheme-handler/https x-scheme-handler/http text/html application/xhtml+xml; do
  got=$(as_session "$user" gio mime "$type" 2>&1 | sed -n 's/^Default application for .*: //p' | head -n 1)
  [ "$got" = "$default_id" ] || bad="$bad $type=${got:-none}"
done
if [ -z "$bad" ]; then emit gio-mime-web-types pass "https, http, text/html and application/xhtml+xml default to $default_id"
else emit gio-mime-web-types fail "not $default_id:$bad"; fi

# 3. Exactly one visible Viola launcher, and the dock shows it.
launchers=$(as_session "$user" python3 -W ignore -c '
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio
hits = []
for app in Gio.AppInfo.get_all():
    ident = (app.get_id() or "") + " " + (app.get_executable() or "") + " " + (app.get_name() or "")
    if "viola" in ident.lower() and app.should_show():
        hits.append(app.get_id())
print(" ".join(sorted(hits)))
' 2>"$work/launchers.err" || tail -n 2 "$work/launchers.err")
if [ "$launchers" = "$launcher_id" ]; then emit single-visible-launcher pass "$launchers"
else emit single-visible-launcher fail "visible Viola launchers: ${launchers:-none}"; fi

favorites=$(as_session "$user" dconf read /org/gnome/shell/favorite-apps 2>&1)
case $favorites in
  *"'$launcher_id'"*) emit dock-favorite pass "favorite-apps carries $launcher_id" ;;
  *) emit dock-favorite fail "favorite-apps: ${favorites:-unset}" ;;
esac
# GNOME Shell's own rule (shell-app-system.c scan_startup_wm_class_to_id):
# which desktop id owns Viola's window class. It must be the dock launcher, or
# a running Viola shows as a second dock icon.
owner=$(as_session "$user" python3 -W ignore -c '
import sys
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio
table, no_show = {}, set()
for info in Gio.AppInfo.get_all():
    ident = info.get_id()
    get_wm_class = getattr(info, "get_startup_wm_class", None)
    wm_class = get_wm_class() if get_wm_class else None
    if wm_class is None:
        continue
    show = info.should_show()
    if not show:
        no_show.add(ident)
    old = table.get(wm_class)
    if old and ident.removesuffix(".desktop") == wm_class:
        old = None
    if old and show and old in no_show:
        old = None
    if not old:
        table[wm_class] = ident
owner = table.get(sys.argv[1])
if owner is None:
    direct = Gio.DesktopAppInfo.new(sys.argv[1] + ".desktop")
    owner = direct.get_id() if direct and direct.should_show() else "none"
print(owner)
' "$wm_class" 2>"$work/owner.err" || tail -n 2 "$work/owner.err")
if [ "$owner" = "$launcher_id" ]; then emit window-matches-dock-launcher pass "window class $wm_class belongs to $owner"
else emit window-matches-dock-launcher fail "window class $wm_class belongs to $owner, not $launcher_id"; fi

# 4. Viola renders a page headless (DevTools: the page title only changes once
#    the renderer has parsed the document).
profile="$home/viola-gate-profile"
token="luma-gate-rendered-$$"
as_session "$user" timeout 90 viola-browser-stable --headless=new --disable-gpu --no-first-run \
  --user-data-dir="$profile" --remote-debugging-port=0 \
  "data:text/html,<title>$token</title><p>Luma</p>" >"$work/viola-headless.log" 2>&1 &
viola_pid=$!
rendered=
for _ in $(seq 60); do
  port=$(head -n 1 "$profile/DevToolsActivePort" 2>/dev/null)
  if [ -n "$port" ] && curl -fsS --max-time 3 "http://127.0.0.1:$port/json/list" 2>/dev/null | grep -Fq "\"title\": \"$token\""; then
    rendered=1
    break
  fi
  sleep 1
done
pkill -KILL -u "$user" >/dev/null 2>&1 || true
wait "$viola_pid" 2>/dev/null
if [ -n "$rendered" ]; then emit viola-renders-headless pass "an unprivileged account's Viola rendered a page (DevTools port $port)"
else emit viola-renders-headless fail "no rendered page within 60s: $(grep -v -e dbus -e gcm "$work/viola-headless.log" | tail -n 4)"; fi

# Signed desktop launch needs a real session bus, compositor and window. A
# Flatpak launcher connecting to a synthetic socket cannot qualify a browser.
if [ "$browser_role" = signed ]; then
  if ! bash "$(dirname "$0")/browser-opens.sh"; then
    failed=1
  fi
else
# 5. xdg-open of an https URL launches Viola. Headless-safe: the session's
#    display sockets are a listener that accepts and never answers, so the
#    launched browser blocks connecting to its display; the listener records
#    the process that connected (kernel peer credentials) and it is killed.
session_env "$user"
runtime="$work/run-$user"
cat >"$work/display-trap.py" <<'PY'
import json, os, select, socket, struct, sys, time
runtime, out, wait_s = sys.argv[1], sys.argv[2], float(sys.argv[3])
listeners = {}
w = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
w.bind(os.path.join(runtime, "wayland-luma-gate")); w.listen(16); listeners[w] = "wayland"
x = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
x.bind("\0/tmp/.X11-unix/X77"); x.listen(16); listeners[x] = "x11"
open(os.path.join(runtime, "trap-ready"), "w").close()
held, deadline = [], time.monotonic() + wait_s
while time.monotonic() < deadline:
    ready, _, _ = select.select(list(listeners), [], [], 0.5)
    for s in ready:
        conn, _ = s.accept(); held.append(conn)
        pid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[0]
        rec = {"socket": listeners[s], "pid": pid, "exe": "", "argv": [], "launched_desktop_file": ""}
        try: rec["exe"] = os.readlink("/proc/%d/exe" % pid)
        except OSError as e: rec["exe"] = "unreadable: %s" % e
        try: rec["argv"] = [a.decode(errors="replace") for a in open("/proc/%d/cmdline" % pid, "rb").read().split(b"\0") if a]
        except OSError: pass
        # Chromium rewrites its own process title, which overwrites the
        # environment block /proc shows; its helper processes (zygote,
        # crashpad) were started with the real environment.
        family = [pid] + [int(d) for d in os.listdir("/proc") if d.isdigit() and
                          open("/proc/%s/stat" % d).read().rsplit(")", 1)[-1].split()[1] == str(pid)]
        for member in family:
            try:
                env = dict(v.split(b"=", 1) for v in open("/proc/%d/environ" % member, "rb").read().split(b"\0") if b"=" in v)
            except OSError:
                continue
            if env.get(b"GIO_LAUNCHED_DESKTOP_FILE"):
                rec["launched_desktop_file"] = env[b"GIO_LAUNCHED_DESKTOP_FILE"].decode(errors="replace")
                break
        with open(out, "a") as f: f.write(json.dumps(rec) + "\n")
        sys.exit(0)
sys.exit(1)
PY
chmod 0644 "$work/display-trap.py"
url="https://gate.invalid/luma-gate-xdg-open-$$"
(cd /tmp && runuser -u "$user" -- python3 "$work/display-trap.py" "$runtime" "$runtime/trap.jsonl" 60) &
trap_pid=$!
for _ in $(seq 50); do [ -e "$runtime/trap-ready" ] && break; sleep 0.1; done
(cd /tmp && runuser -u "$user" -- env -i "${env_lines[@]}" WAYLAND_DISPLAY=wayland-luma-gate DISPLAY=:77 \
  timeout 30 xdg-open "$url") >"$work/xdg-open.log" 2>&1
open_code=$?
wait "$trap_pid"
record=$(cat "$runtime/trap.jsonl" 2>/dev/null)
pkill -KILL -u "$user" >/dev/null 2>&1 || true
verdict=$(python3 -c '
import json, os, sys
url, default_id, native_host = sys.argv[2], sys.argv[3], sys.argv[4]
try:
    rec = json.loads(sys.argv[1])
except ValueError:
    print("fail no process connected to the session display"); sys.exit()
problems = []
native = os.path.basename(rec["exe"]).startswith("python3") and rec["argv"][1:2] == [native_host]
if not (rec["exe"].endswith("/viola/browser/chrome") or native):
    problems.append("process is %s %s" % (rec["exe"], rec["argv"][:2]))
if url not in " ".join(rec["argv"]).split():
    problems.append("the URL is not in its arguments %s" % rec["argv"])
if not rec["launched_desktop_file"].endswith("/" + default_id):
    problems.append("launched from %r" % rec["launched_desktop_file"])
print(("fail " + "; ".join(problems)) if problems else ("pass xdg-open started %s with the URL from %s" % (rec["exe"], rec["launched_desktop_file"])))
' "$record" "$url" "$default_id" "$native_host")
if [ "${verdict%% *}" = pass ] && [ "$open_code" = 0 ]; then emit xdg-open-https-launches-viola pass "${verdict#pass }"
else emit xdg-open-https-launches-viola fail "xdg-open exit $open_code; ${verdict#* }; $(tail -n 3 "$work/xdg-open.log")"; fi

fi

# 6. A person's own choice wins over the image default.
useradd -m "$chooser" || emit personal-choice-kept fail "useradd $chooser failed"
chooser_home=$(getent passwd "$chooser" | cut -d: -f6)
install -d -o "$chooser" -g "$chooser" "$chooser_home/.config" "$chooser_home/.local/share/applications"
printf '[Desktop Entry]\nType=Application\nName=Other Browser\nExec=/usr/bin/true %%u\nMimeType=x-scheme-handler/http;x-scheme-handler/https;text/html;\n' \
  >"$chooser_home/.local/share/applications/luma-gate-other-browser.desktop"
printf '[Default Applications]\nx-scheme-handler/http=luma-gate-other-browser.desktop\nx-scheme-handler/https=luma-gate-other-browser.desktop\ntext/html=luma-gate-other-browser.desktop\n' \
  >"$chooser_home/.config/mimeapps.list"
chown -R "$chooser:$chooser" "$chooser_home/.config" "$chooser_home/.local"
out=$(as_session "$chooser" xdg-settings get default-web-browser 2>&1)
if [ "$out" = luma-gate-other-browser.desktop ]; then emit personal-choice-kept pass "an account that chose another browser keeps it"
else emit personal-choice-kept fail "an account that chose another browser gets: $out"; fi

exit "$failed"
