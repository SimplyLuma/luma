#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Signed browser launch gate using Depot's ordinary user-manager/Shell setup.
set -uo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
user=luma-gate-browser-opens
failed=0
work=$(mktemp -d /tmp/luma-gate-browser-opens.XXXXXX)
chmod 0755 "$work"
emit() {
  python3 -c 'import json,sys;print(json.dumps(dict(check=sys.argv[1],result=sys.argv[2],detail=sys.argv[3])))' "$1" "$2" "$3"
  [ "$2" = fail ] && failed=1
  return 0
}
created=0
page_runner=0
page_runner_start=
# Return 0 only while the exact background runuser created below still owns
# this PID/starttime/argv. A reused PID never receives a signal.
owned_page_runner() {
  python3 -B - "$page_runner" "$page_runner_start" "$user" "$work/page.py" "$out" <<'PROCESS'
import os,sys
from pathlib import Path
pid,start,user,script,out=sys.argv[1:]
p=Path('/proc')/pid
try:
 fields=(p/'stat').read_text().rsplit(')',1)[1].split()
 args=[v.decode() for v in (p/'cmdline').read_bytes().split(bytes([0])) if v]
except (FileNotFoundError,ProcessLookupError):raise SystemExit(3)
if fields[19]!=start or p.stat().st_uid!=0:raise SystemExit(2)
if fields[0]=='Z':raise SystemExit(3)
if len(args)!=7 or Path(args[0]).name!='runuser' or args[1:]!=['-u',user,'--','python3',script,out]:raise SystemExit(2)
raise SystemExit(0)
PROCESS
}
cleanup() {
  local original_status=$? cleanup_failed=0 page_closed=1
  if [ "$page_runner" != 0 ]; then
    local identity_status=0
    owned_page_runner || identity_status=$?
    if [ "$identity_status" = 0 ]; then
      kill -TERM "$page_runner" || cleanup_failed=1
      for _ in $(seq 50); do
        identity_status=0
        owned_page_runner || identity_status=$?
        [ "$identity_status" = 0 ] || break
        sleep 0.1
      done
    fi
    if [ "$identity_status" = 3 ]; then
      wait "$page_runner" 2>/dev/null || true
    else
      emit signed-browser-cleanup fail 'owned page server did not close with unchanged PID identity within 5 seconds'
      cleanup_failed=1; page_closed=0
    fi
  fi
  if [ "$original_status" != 0 ] && [ "$created" = 1 ]; then
    journalctl -b --no-pager -n 100 "_UID=$uid" >"$work/launch-journal.log" 2>&1
    printf 'LUMA_BROWSER_DIAGNOSTICS=%s UID=%s\n' "$work" "$uid"
  fi
  if [ "$created" = 1 ]; then
    loginctl disable-linger "$user" >/dev/null 2>&1 || { emit signed-browser-cleanup fail 'owned user linger could not be disabled'; cleanup_failed=1; }
    loginctl terminate-user "$user" >/dev/null 2>&1 || { emit signed-browser-cleanup fail 'owned user manager did not accept termination'; cleanup_failed=1; }
    local remaining=unknown
    for _ in $(seq 50); do
      remaining=$(python3 -B - "$uid" <<'UIDPROCESSES'
from pathlib import Path
import sys
uid=int(sys.argv[1]);found=[]
for p in Path('/proc').iterdir():
 if not p.name.isdecimal():continue
 try:
  if p.stat().st_uid==uid:found.append(p.name)
 except (FileNotFoundError,ProcessLookupError):pass
print(' '.join(sorted(found)))
UIDPROCESSES
) || { cleanup_failed=1; break; }
      [ -n "$remaining" ] || break
      sleep 0.1
    done
    if [ -n "$remaining" ]; then
      emit signed-browser-cleanup fail "owned QA UID still has processes after bounded wait: $remaining"
      cleanup_failed=1
    fi
    if [ "$page_closed" = 1 ] && [ -z "$remaining" ]; then
      if ! userdel -r "$user" >"$work/userdel.log" 2>&1; then
        emit signed-browser-cleanup fail "owned QA account cleanup failed: $(tail -n 2 "$work/userdel.log")"
        cleanup_failed=1
      fi
    fi
  fi
  if [ "$page_closed" = 1 ] && [ "$cleanup_failed" = 0 ] && [ "$original_status" = 0 ]; then rm -rf "$work"; fi
  if [ "$cleanup_failed" != 0 ]; then exit 1; fi
  exit "$original_status"
}
trap cleanup EXIT
if getent passwd "$user" >/dev/null; then
  emit signed-browser-session fail 'the disposable browser gate account already exists'
  exit 1
fi
useradd -m "$user" || { emit signed-browser-session fail 'cannot create disposable browser account'; exit 1; }
created=1
uid=$(id -u "$user")
loginctl enable-linger "$user" || { emit signed-browser-session fail 'cannot start the ordinary user manager'; exit 1; }
for _ in $(seq 30); do [ -S "/run/user/$uid/bus" ] && break; sleep 1; done
[ -S "/run/user/$uid/bus" ] || { emit signed-browser-session fail 'the ordinary user manager supplied no session bus'; exit 1; }
# Stage actor files in the disposable account; automation is QA only and adds
# no app permissions or production flags. The actual packaged browser runs.
cp "$here/browser-opens.js" "$here/browser-process-proof.py" "$work/"
out=$work/out
install -d -o "$user" -g "$user" -m 0755 "$out"
cat >"$work/page.py" <<'PY'
import http.server,json,secrets,sys
from pathlib import Path
out=Path(sys.argv[1]); token=secrets.token_hex(24); path='/luma-browser-gate-'+token
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != path:
            self.send_error(404); return
        body=('<!doctype html><title>Luma browser gate '+token+'</title><p>'+token+'</p>').encode()
        self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        (out/'page-requested').write_text(json.dumps({'path':self.path,'user_agent':self.headers.get('User-Agent'),'token':token})+'\n')
    def log_message(self,*args): pass
server=http.server.HTTPServer(('127.0.0.1',0),Handler)
(out/'url').write_text('http://127.0.0.1:'+str(server.server_port)+path+'\n')
server.serve_forever()
PY
chown -R "$user:" "$work"
runuser -u "$user" -- python3 "$work/page.py" "$out" >"$work/page.log" 2>&1 &
page_runner=$!
page_runner_start=$(python3 -B - "$page_runner" <<'START'
from pathlib import Path
import sys
print((Path('/proc')/sys.argv[1]/'stat').read_text().rsplit(')',1)[1].split()[19])
START
) || { emit signed-browser-page fail 'the owned page-server identity could not be recorded'; exit 1; }
owned_page_runner || { emit signed-browser-page fail 'the owned page-server argv differs'; exit 1; }
for _ in $(seq 50); do [ -s "$out/url" ] && break; sleep 0.1; done
[ -s "$out/url" ] || { emit signed-browser-page fail 'the isolated local page fixture did not start'; exit 1; }
url=$(cat "$out/url")
https="https://gate.invalid/luma-browser-https-$(cat /proc/sys/kernel/random/uuid)"
session_env=("HOME=/home/$user" "USER=$user" "LOGNAME=$user" "PATH=/usr/local/bin:/usr/bin:/bin"
  "XDG_RUNTIME_DIR=/run/user/$uid" "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$uid/bus"
  "XDG_CURRENT_DESKTOP=GNOME" "XDG_SESSION_DESKTOP=gnome" "XDG_SESSION_TYPE=wayland")
# Use the installed environment generators, including Flatpak export precedence
# and the native launcher policy, with the actual user-manager bus.
for generator in /usr/lib/systemd/user-environment-generators/*; do
  [ -x "$generator" ] || continue
  while IFS= read -r line; do
    case $line in [A-Za-z_]*=*) session_env+=("$line") ;; esac
  done < <(runuser -u "$user" -- env -i "${session_env[@]}" "$generator")
done
runuser -u "$user" -- env -i -C "/home/$user" "${session_env[@]}" \
  GSETTINGS_BACKEND=memory LIBGL_ALWAYS_SOFTWARE=1 \
  BROWSER_GATE_OUT="$out" BROWSER_GATE_URL="$url" BROWSER_GATE_HTTPS="$https" \
  BROWSER_GATE_OBSERVER="$work/browser-process-proof.py" \
  timeout -k 30 360 gnome-shell --headless --no-x11 --virtual-monitor 1280x800 \
  --automation-script="$work/browser-opens.js" >"$work/shell.log" 2>&1
shell_status=$?
if [ -f "$out/results.json" ]; then
  while IFS=$'\t' read -r name status detail; do emit "$name" "$status" "$detail"; done < <(
    python3 -c 'import json,sys
for row in json.load(open(sys.argv[1])):
 print("\t".join((row["name"],"pass" if row["ok"] else "fail",row["detail"].replace("\n"," ").replace("\t"," "))))' "$out/results.json")
  if ! python3 -c 'import json,sys
rows=json.load(open(sys.argv[1]));assert {r["name"] for r in rows if r["ok"]} == {"signed-browser-loads-requested-page","xdg-open-https-launches-viola"}' "$out/results.json"; then
    emit signed-browser-complete fail 'the complete HTTP-page and HTTPS-window proofs are required'
  fi
else
  emit signed-browser-graphical-launch fail "the real Shell supplied no browser results (exit $shell_status): $(tail -n 8 "$work/shell.log" | tr '\n' ' ')"
fi
[ "$shell_status" = 0 ] || emit signed-browser-session fail "the graphical session exited $shell_status"
exit "$failed"
