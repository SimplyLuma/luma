#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
# Disposable test VM only. Point luma-update at a local signed nightly graph
# listing COMMIT=VERSION pairs, the way the release gate's agent_setup does.
#   guest-graph.sh COMMIT=VERSION [COMMIT=VERSION...]
set -euo pipefail
rig=/var/lib/luma-nohurdles-graph
install -d -m 0700 "$rig"
[ -f "$rig/secret" ] || head -c 32 /dev/urandom > "$rig/secret"
PYTHONPATH=/usr/lib/python3.14/site-packages python3 - "$rig" "$@" <<'PY'
import json, sys, time
from datetime import datetime, timezone
from pathlib import Path
from luma_update import minisign
rig = Path(sys.argv[1])
stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
releases = []
for item in sys.argv[2:]:
    commit, version = item.split("=", 1)
    releases.append({"version": version, "commit": commit, "released_at": "2026-01-01T00:00:00Z",
                     "rollout": {"start_at": "2026-01-01T00:00:00Z", "start_percentage": 1.0, "duration_minutes": 0},
                     "paused": False, "deadend": False, "deadend_reason": None, "barrier": False,
                     "importance": "normal", "notes_url": None, "summary": f"Test release {version}.",
                     "download_bytes_estimate": 0})
data = json.dumps({"schema_version": 1, "channel": "nightly", "arch": "x86_64", "generated_at": stamp,
                   "releases": releases}, indent=2).encode()
public, signature = minisign.sign_for_tests(rig.joinpath("secret").read_bytes(), b"LUMANOH1", data,
                                            f"timestamp:{int(time.time())}\tfile:nightly.json\thashed")
Path("/etc/luma/update-graph-keys.d").mkdir(parents=True, exist_ok=True)
Path("/etc/luma/update-graph-keys.d/luma-nohurdles.pub").write_text(public)
rig.joinpath("nightly.json").write_bytes(data)
rig.joinpath("nightly.json.minisig").write_text(signature)
PY
repo_url=$(head -n 1 /etc/luma/update-mirrorlist)
printf '%s\n' '[update]' 'graph_url = http://127.0.0.1:8479/{channel}.json' \
  'events_url = http://127.0.0.1:8479/events' "preview_repo_url = $repo_url" 'allow_insecure_urls = true' \
  > /etc/luma/update.conf
{ umask 077; printf '{"credential": "luma-nohurdles-test-vm", "channel": "nightly", "channels": ["nightly"], "issued_at": %s}\n' "$(date +%s)" > /etc/luma/update-preview-credential; }
systemctl is-active --quiet luma-nohurdles-graph || systemd-run --unit=luma-nohurdles-graph /usr/bin/python3 -m http.server --bind 127.0.0.1 --directory "$rig" 8479 >/dev/null
systemctl stop luma-updated.timer >/dev/null 2>&1 || true
systemctl restart luma-updated.service >/dev/null 2>&1 || true
sleep 1
