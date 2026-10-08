#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
#
# Updates out of the box (ADR-030 sections 4, 6 and 9): every channel is
# public, and a person picks Official, Beta or Nightly with no account and no
# credential. Run as root, with network, inside a disposable VM installed from
# a nightly's installer medium; one call per phase, the harness restarts the VM
# between phases. Prints one JSON line per check ({"check", "result":
# "pass"|"fail"|"skip", "detail"}) and exits 1 if any check failed.
#
#   update-out-of-box.sh --medium public [--expect-channel stable|nightly]
#       A fresh install: luma-update runs, follows a Luma channel from the
#       public repository (--expect-channel, default stable), offers Official,
#       Beta and Nightly, and carries no credential.
#   update-out-of-box.sh --phase pick-and-stage --channel nightly --expect-version V
#       Pick the channel as Depot does (SetChannel, then check and download) and
#       require V staged. An agent older than luma-update 1.0.0-1.luma.10 still
#       refuses a preview channel without a credential; such an install takes
#       the documented one-time path (rpm-ostree upgrade, or rebase to the
#       channel), recorded as pick-and-stage-legacy-path.
#   update-out-of-box.sh --phase booted --expect-version V
#       V is booted and follows the channel; a rollback is requested.
#   update-out-of-box.sh --phase rolled-back --expect-version PREV
#       PREV is booted again; switch back to Official and require it.
#
# Every phase also takes --repo-url URL (the repository the remote's mirror
# list names; plain http only for a loopback test server) and --graph-url
# TEMPLATE (update graphs, "{channel}" in it), so the same script runs against
# the build host's content server or the public download server.
set -uo pipefail
medium="" phase="" pick="" expect_version="" expect_channel=stable repo_url="" graph_url=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --medium) medium=${2:-}; shift 2 ;;
    --phase) phase=${2:-}; shift 2 ;;
    --channel) pick=${2:-}; shift 2 ;;
    --expect-version) expect_version=${2:-}; shift 2 ;;
    --expect-channel) expect_channel=${2:-}; shift 2 ;;
    --repo-url) repo_url=${2:-}; shift 2 ;;
    --graph-url) graph_url=${2:-}; shift 2 ;;
    *) printf 'usage: see the header of %s\n' "$0" >&2; exit 2 ;;
  esac
done
case "$medium/$phase" in
  public/) ;;
  /pick-and-stage) case "$pick" in stable|beta|nightly) ;; *) printf 'need --channel\n' >&2; exit 2 ;; esac
                   [ -n "$expect_version" ] || { printf 'need --expect-version\n' >&2; exit 2; } ;;
  /booted|/rolled-back) [ -n "$expect_version" ] || { printf 'need --expect-version\n' >&2; exit 2; } ;;
  *) printf 'need --medium public, or --phase pick-and-stage|booted|rolled-back\n' >&2; exit 2 ;;
esac
failed=0
arch=$(uname -m)
public_repo=https://dl.simplyluma.com/os/repo

emit() {
  python3 -c 'import json, sys; print(json.dumps({"check": sys.argv[1], "result": sys.argv[2], "detail": sys.argv[3][-600:]}))' "$1" "$2" "$3"
  [ "$2" = fail ] && failed=1
  return 0
}
field() { luma-update status --json 2>/dev/null |
  python3 -c 'import json,sys; v=json.load(sys.stdin).get(sys.argv[1]); print(json.dumps(v) if isinstance(v,(list,bool)) else ("" if v is None else v))' "$1"; }
expect() { # expect CHECK ACTUAL WANTED DETAIL
  if [ "$2" = "$3" ]; then emit "$1" pass "$4: $2"; else emit "$1" fail "$4: got '$2', want '$3'"; fi
}
redact() { sed -E 's#/os/preview/[^/[:space:]]+/#/os/preview/<credential>/#g'; }
deployment() { # deployment booted|staged|default FIELD
  rpm-ostree status --json | python3 -c '
import json, sys
d = json.load(sys.stdin)["deployments"]
which, field = sys.argv[1], sys.argv[2]
pick = next((x for x in d if x.get("booted")), None) if which == "booted" else \
       next((x for x in d if x.get("staged")), None) if which == "staged" else (d[0] if d else None)
print("" if pick is None else pick.get(field, ""))' "$1" "$2"
}
modern_agent() { # luma-update 1.0.0-1.luma.10+: preview channels need no enrollment
  ! grep -qs "enroll in early updates before choosing" /usr/lib/python3*/site-packages/luma_update/engine.py
}
idle() { # wait (up to 30 minutes) until the agent is not checking or downloading
  local deadline=$((SECONDS + 1800))
  while [ "$SECONDS" -lt "$deadline" ]; do
    case "$(field state)" in checking|downloading) sleep 5 ;; *) return 0 ;; esac
  done
  return 1
}
dbus() { busctl --system --timeout=1500 call org.projectluma.Update1 /org/projectluma/Update1 org.projectluma.Update1 "$@"; }

command -v luma-update >/dev/null 2>&1 || { emit luma-update-installed fail "luma-update is not installed"; exit 1; }
systemctl stop luma-updated.timer >/dev/null 2>&1 || true   # the harness decides when checks happen

# Test-server overrides, applied the same way in every phase.
if [ -n "$repo_url" ] || [ -n "$graph_url" ]; then
  if [ -n "$repo_url" ]; then
    (umask 077; printf '%s\n' "$repo_url" >/etc/luma/update-mirrorlist)
    public_repo=$repo_url
  fi
  {
    printf '[update]\n'
    [ -z "$repo_url" ] || printf 'stable_repo_url = %s\n' "$repo_url"
    [ -z "$graph_url" ] || printf 'graph_url = %s\n' "$graph_url"
    case "$repo_url$graph_url" in *http://*) printf 'allow_insecure_urls = true\n' ;; esac
  } >/etc/luma/update.conf
  systemctl restart luma-updated.service >/dev/null 2>&1 || true
  rpm-ostree reload >/dev/null 2>&1 || true
fi

case "$medium/$phase" in
public/)
  emit luma-update-installed pass "$(rpm -q luma-update)"
  expect managed "$(field managed)" true "follows a Luma channel"
  expect default-channel "$(field channel)" "$expect_channel" "the channel a fresh install follows"
  channels=$(field available_channels)
  case "$channels" in
    *'"stable"'*'"beta"'*'"nightly"'*) emit channel-picker pass "available_channels $channels" ;;
    *) emit channel-picker fail "available_channels $channels (Official, Beta and Nightly must all be on offer)" ;;
  esac
  expect no-account-needed "$(field preview_enrolled)" false "no enrollment on a fresh install"
  if [ ! -e /etc/luma/update-preview-credential ]; then emit no-credential pass ""; else emit no-credential fail "a credential record exists"; fi
  if [ "$(stat -c '%U %a' /etc/luma/update-mirrorlist 2>/dev/null)" = "root 600" ]; then emit mirrorlist-private pass "root 600"
  else emit mirrorlist-private fail "$(stat -c '%U %a' /etc/luma/update-mirrorlist 2>&1)"; fi
  expect public-repository "$(head -n 1 /etc/luma/update-mirrorlist 2>/dev/null | redact)" "$public_repo" "the remote's mirror list"
  if grep -Fxq 'url=mirrorlist=file:///etc/luma/update-mirrorlist' /etc/ostree/remotes.d/luma.conf 2>/dev/null; then
    emit remote-reads-mirrorlist pass ""
  else
    emit remote-reads-mirrorlist fail "$(grep -h '^url' /etc/ostree/remotes.d/*.conf 2>&1 | redact)"
  fi
  if out=$(ostree remote summary luma 2>&1 >/dev/null); then
    emit repository-answers pass "the public repository's signed summary verified"
  else
    emit repository-answers fail "$(printf '%s' "$out" | redact | tail -n 3)"
  fi
  ;;

/pick-and-stage)
  if modern_agent; then
    idle   # a check the agent started by itself (boot, network up) must finish first
    out=$(dbus SetChannel s "$pick" 2>&1) || emit pick fail "SetChannel $pick: $(printf '%s' "$out" | tail -n 2)"
    expect pick "$(field channel)" "$pick" "SetChannel $pick, as Depot calls it"
    idle; luma-update check >/tmp/luma-oob-check.log 2>&1 || true
    idle
    if [ "$(field staged_version)" != "$expect_version" ]; then
      luma-update download >>/tmp/luma-oob-check.log 2>&1 || true
      idle
    fi
    expect pick-and-stage "$(field staged_version)" "$expect_version" \
      "staged by the agent (state $(field state), error class '$(field last_error_class)')"
    expect pick-no-credential "$(field preview_enrolled)" false "no enrollment was needed"
  else
    origin=$(deployment booted origin)
    if [ "$origin" = "luma:luma/1/$arch/$pick" ]; then
      out=$(rpm-ostree upgrade 2>&1); code=$?
    else
      out=$(rpm-ostree rebase "luma:luma/1/$arch/$pick" 2>&1); code=$?
    fi
    staged=$(deployment staged version)
    [ -n "$staged" ] || staged=$(deployment default version)
    if [ "$code" = 0 ] && [ "$staged" = "$expect_version" ]; then
      emit pick-and-stage-legacy-path pass "$(rpm -q luma-update) predates public channels; rpm-ostree staged $staged"
    else
      emit pick-and-stage-legacy-path fail "exit $code, staged '$staged': $(printf '%s' "$out" | redact | tail -n 4)"
    fi
  fi
  if journalctl -b --no-pager -o cat -u rpm-ostreed 2>/dev/null | grep -qi 'delta'; then
    emit delta-used pass "rpm-ostreed fetched a static delta"
  else
    emit delta-used skip "no delta mentioned in rpm-ostreed's journal (a delta may not exist for this pair)"
  fi
  ;;

/booted)
  expect booted-version "$(deployment booted version)" "$expect_version" "booted deployment"
  if modern_agent; then
    expect booted-channel "$(field channel)" nightly "follows the channel it was moved to"
    [ -z "$(field last_error_class)" ] && emit booted-agent-healthy pass "" ||
      emit booted-agent-healthy fail "error class $(field last_error_class)"
  else
    emit booted-channel skip "$(rpm -q luma-update) predates public channels"
  fi
  idle
  out=$(luma-update rollback 2>&1) || out="$out; $(rpm-ostree rollback 2>&1)"
  default=$(deployment default checksum); booted=$(deployment booted checksum)
  if [ -n "$default" ] && [ "$default" != "$booted" ]; then
    emit rollback-requested pass "the previous deployment starts next"
  else
    emit rollback-requested fail "$(printf '%s' "$out" | tail -n 3)"
  fi
  ;;

/rolled-back)
  expect rolled-back-version "$(deployment booted version)" "$expect_version" "booted deployment after the rollback"
  idle
  out=$(luma-update channel stable 2>&1) || emit back-to-official fail "$(printf '%s' "$out" | tail -n 2)"
  expect back-to-official "$(field channel)" stable "status after choosing Official"
  if [ ! -e /etc/luma/update-preview-credential ]; then emit official-no-credential pass ""; else emit official-no-credential fail "a credential record exists"; fi
  ;;
esac
exit "$failed"
