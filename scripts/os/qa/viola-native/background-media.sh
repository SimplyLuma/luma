#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Background media regression: a playing video whose tab goes to the background
# keeps playing, feeds the sidebar preview and answers when its tab returns.
#
#   background-media.sh FIXTURE.webm [HOST_DIR] [ENGINE]
#
# QA only, as root in a disposable VM. The engine needs a GPU-backed display
# (for example virtio-vga-gl): the deadlock this guards against (engine patch
# 0005) is reached only when the preview's WebRTC encoder reads back GPU video
# frames. Runs the installed host's own media qualification (headless_qa.py
# --representative --media-fixture, VIOLA_QA_MEDIA_CHECKS=1) from a private copy
# whose startup deadlines are raised for slow virtual GPUs, then judges the
# report. Prints one JSON line per check and exits non-zero on any failure.
set -u
fixture=${1:?usage: background-media.sh FIXTURE.webm [HOST_DIR] [ENGINE]}
host=${2:-/usr/share/viola-browser/luma-host}
engine=${3:-/opt/viola/browser/chrome}
user=luma-qa-background-media
[ "$(id -u)" = 0 ] || { echo 'run as root in a disposable VM' >&2; exit 2; }
[ -f "$fixture" ] && [ -f "$host/headless_qa.py" ] && [ -x "$engine" ] || { echo 'missing fixture, host or engine' >&2; exit 2; }
work=$(mktemp -d /var/tmp/luma-qa-background-media.XXXXXX)
created=0
cleanup() {
  if [ "$created" = 1 ]; then
    loginctl disable-linger "$user" >/dev/null 2>&1
    loginctl terminate-user "$user" >/dev/null 2>&1
    for _ in $(seq 50); do pgrep -u "$user" >/dev/null || break; sleep 0.2; done
    userdel -r "$user" >/dev/null 2>&1
  fi
}
trap cleanup EXIT
getent passwd "$user" >/dev/null && { echo "$user already exists" >&2; exit 2; }
useradd -m "$user" && created=1
uid=$(id -u "$user")
loginctl enable-linger "$user"
for _ in $(seq 60); do [ -S "/run/user/$uid/bus" ] && break; sleep 1; done
cp -a "$host" "$work/host"
cp "$fixture" "$work/fixture.webm"
# Raise only startup deadlines; every check and its threshold stays the host's.
python3 - "$work/host" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
edits = {
    'headless_qa.py': [('time.monotonic() + 15', 'time.monotonic() + 180'),
                       ('time.monotonic() + 45', 'time.monotonic() + 400'),
                       ('end = time.monotonic() + 5\n', 'end = time.monotonic() + 60\n'),
                       ('timeout=7, stdout=log', 'timeout=60, stdout=log'),
                       ("timeout=5).strip()", "timeout=60).strip()"),
                       ('application.wait(timeout=75 if args.qualify_controls else 55)',
                        'application.wait(timeout=600)')],
    'browser_services.py': [('def wait_state(self, predicate, timeout=10)',
                             'def wait_state(self, predicate, timeout=60)'),
                            ('def connect(self, timeout=10', 'def connect(self, timeout=60')],
}
for name, pairs in edits.items():
    path = root / name
    text = path.read_text()
    for old, new in pairs:
        if old not in text:
            raise SystemExit(f'{name}: deadline {old!r} not found')
        text = text.replace(old, new)
    path.write_text(text)
PY
chown -R "$user:" "$work"
for _ in $(seq 30); do
  runuser -u "$user" -- env XDG_RUNTIME_DIR=/run/user/$uid DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$uid/bus \
    timeout 5 pactl info >/dev/null 2>&1 && break
  sleep 2
done
runuser -u "$user" -- env -i HOME=/home/$user USER=$user PATH=/usr/bin:/bin \
  XDG_RUNTIME_DIR=/run/user/$uid DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$uid/bus \
  VIOLA_QA_MEDIA_CHECKS=1 timeout -k 10 900 python3 "$work/host/headless_qa.py" \
  --engine "$engine" --work-root "$work/runs" --representative --media-fixture "$work/fixture.webm" \
  >"$work/qa.log" 2>&1
report=$(ls "$work"/runs/headless-*/native-window-*/integration-report.json 2>/dev/null | head -1)
python3 - "${report:-}" "$work" <<'PY'
import json, sys
path, work = sys.argv[1:]
failed = False
def check(name, ok, detail):
    global failed
    failed |= not ok
    print(json.dumps({'check': name, 'result': 'pass' if ok else 'fail', 'detail': detail}))
if not path:
    check('background-media-report', False, f'no integration report; see {work}/qa.log')
    raise SystemExit(1)
report = json.load(open(path))
layout = report.get('layout_fixture', {})
qualification = layout.get('media_qualification', {})
checks = {k: v for item in qualification.get('checks', []) for k, v in item.items()}
media = report.get('native_media', {})
timeouts = report.get('command_timeouts', {})
source = qualification.get('source') or {}
video = source.get('video') or {}
check('media-page-answers', timeouts.get('count') == 0,
      json.dumps(timeouts.get('recent', [])[-2:]))
check('background-preview-frames', media.get('frames', 0) > 0 and not media.get('errors'),
      json.dumps({k: media.get(k) for k in ('offers', 'answers', 'frames', 'errors')}))
check('background-video-plays', video.get('paused') is False and video.get('time', 0) > 0
      and video.get('visibility') == 'hidden', json.dumps(video))
check('foreground-stops-preview', checks.get('foreground_source_hides_and_stops_preview') is True,
      json.dumps(qualification.get('checks')))
check('leaving-source-restarts-preview', media.get('offers', 0) >= 2,
      json.dumps({'offers': media.get('offers')}))
check('engine-exit', report.get('engine_exit_code') == 0, str(report.get('engine_exit_code')))
raise SystemExit(1 if failed else 0)
PY
status=$?
[ "$status" = 0 ] && rm -rf "$work" || echo "LUMA_QA_DIAGNOSTICS=$work" >&2
exit "$status"
