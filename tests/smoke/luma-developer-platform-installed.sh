#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

for tool in dbus-run-session gdbus luma python3 rpm systemd-analyze timeout xvfb-run; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required installed-platform test tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

rpm -q luma-developer-platform luma-developer-platform-sdk
luma semantic --help >/dev/null

test -x /usr/libexec/luma-semantic-broker
test -x /usr/libexec/luma-semantic-consent
test -f /usr/share/dbus-1/interfaces/org.projectluma.SemanticBroker1.xml
test -f /usr/share/dbus-1/services/org.projectluma.SemanticBroker1.service
test -f /usr/lib/systemd/user/luma-semantic-broker.service
systemd-analyze verify /usr/lib/systemd/user/luma-semantic-broker.service

python3 - <<'PY'
import gi

gi.require_version("LumaSemantics", "1")
gi.require_version("LumaUI", "1")
from gi.repository import LumaSemantics, LumaUI

root = LumaSemantics.SemanticObject.new(
    "installed-acceptance", "application", "Installed acceptance"
)
root.add_action(LumaSemantics.Action.new("accept", "Accept"))
wire = root.to_variant()
assert wire.lookup_value("id", None).get_string() == "installed-acceptance"

activity = LumaSemantics.LiveExtension.new(
    "acceptance.activity",
    "org.projectluma.Calendar",
    LumaSemantics.LiveCategory.EVENT,
    "Acceptance event",
)
activity.set_expires_at("2026-08-28T12:00:00+00:00")
assert activity.to_variant().lookup_value("expires_at", None).get_string()

context = LumaUI.Context.new_from_environment()
assert context is not None
print("Installed Luma GIR/typelib acceptance passed")
PY

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT INT TERM

XDG_STATE_HOME="$work_dir/state" dbus-run-session -- bash -eu -c '
  /usr/libexec/luma-semantic-broker &
  broker_pid=$!
  trap '\''kill "$broker_pid" >/dev/null 2>&1 || true'\'' EXIT
  for attempt in $(seq 1 40); do
    if gdbus introspect --session \
      --dest org.projectluma.SemanticBroker1 \
      --object-path /org/projectluma/SemanticBroker1 \
      >"$1/introspection.txt" 2>/dev/null; then
      break
    fi
    sleep 0.05
  done
  grep -Fq org.projectluma.SemanticBroker1 "$1/introspection.txt"
  grep -Fq RequestAccess "$1/introspection.txt"
  grep -Fq InvokeAction "$1/introspection.txt"
  grep -Fq RegisterLiveExtension "$1/introspection.txt"
  grep -Fq ListLiveExtensions "$1/introspection.txt"
  grep -Fq LiveExtensionsChanged "$1/introspection.txt"
' _ "$work_dir"
printf 'Installed Semantic Broker startup and introspection acceptance passed\n'

XDG_STATE_HOME="$work_dir/inspector-state" dbus-run-session -- bash -eu -c '
  /usr/libexec/luma-semantic-broker &
  broker_pid=$!
  trap '\''kill "$broker_pid" >/dev/null 2>&1 || true'\'' EXIT
  for attempt in $(seq 1 40); do
    # This lane deliberately owns a private dbus-run-session rather than a
    # graphical systemd user session. Exercise the already-managed inner CLI;
    # the real standardized app-scope transition is covered by unit tests and
    # the interactive installed-RPM acceptance lane.
    if LUMA_SEMANTIC_INSPECTOR_MANAGED=1 \
      luma semantic list >"$1/inspector.json" 2>/dev/null; then
      exit 0
    fi
    sleep 0.05
  done
  exit 1
' _ "$work_dir"
python3 - "$work_dir/inspector.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    assert json.load(stream) == []
print("Installed SDK Semantic Inspector broker acceptance passed")
PY

set +e
timeout 3s xvfb-run -a dbus-run-session -- \
  /usr/libexec/luma-semantic-consent access \
  --client 'Acceptance Agent' --target Notes --scopes observe-public \
  >"$work_dir/consent.log" 2>&1
consent_status=$?
set -e
if [ "$consent_status" -ne 124 ]; then
  cat "$work_dir/consent.log" >&2
  printf 'error: broker-owned consent UI exited unexpectedly: %s\n' \
    "$consent_status" >&2
  exit 1
fi
printf 'Installed Semantic Broker consent UI acceptance passed\n'

luma new Acceptance --id org.projectluma.Acceptance --destination "$work_dir/app"
luma lint "$work_dir/app/luma-app.toml"
luma inspect "$work_dir/app/luma-app.toml" >/dev/null

set +e
timeout 6s xvfb-run -a dbus-run-session -- "$work_dir/app/run-app" \
  >"$work_dir/app.log" 2>&1
app_status=$?
set -e

if [ "$app_status" -ne 124 ]; then
  cat "$work_dir/app.log" >&2
  printf 'error: generated application exited unexpectedly: %s\n' "$app_status" >&2
  exit 1
fi

printf 'Installed Luma SDK generated-application acceptance passed\n'
