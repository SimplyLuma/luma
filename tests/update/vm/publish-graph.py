#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""publish-graph.py CHANNEL RELEASES_JSON — write and minisign-sign a rig graph.

RELEASES_JSON is a list of {version, commit, ...overrides}; timestamps default
to now. generated_at is always now, so each publication is newer than the last.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/usr/lib/python3.14/site-packages")
from luma_update import minisign  # noqa: E402

rig = Path("/var/lib/luma-update-test")
channel, spec = sys.argv[1], json.loads(sys.argv[2])
now = datetime.now(timezone.utc)
stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
releases = []
for item in spec:
    release = {"version": item["version"], "commit": item["commit"], "released_at": stamp,
               "rollout": {"start_at": stamp, "start_percentage": 1.0, "duration_minutes": 0},
               "paused": False, "deadend": False, "deadend_reason": None, "barrier": False,
               "importance": "normal", "notes_url": f"https://simplyluma.com/releases/{item['version']}",
               "summary": f"Rig release {item['version']}.", "download_bytes_estimate": 1000000}
    release.update({k: v for k, v in item.items() if k not in ("version", "commit")})
    releases.append(release)
graph = {"schema_version": 1, "channel": channel, "arch": "x86_64", "generated_at": stamp, "releases": releases}
data = json.dumps(graph, indent=2).encode()
_, signature = minisign.sign_for_tests((rig / "minisign.secret").read_bytes(), b"LUMARIG1", data,
                                       f"timestamp:{int(time.time())}\tfile:{channel}.json\thashed")
(rig / "graph").mkdir(exist_ok=True)
(rig / "graph" / f"{channel}.json").write_bytes(data)
(rig / "graph" / f"{channel}.json.minisig").write_text(signature)
print(data.decode())
