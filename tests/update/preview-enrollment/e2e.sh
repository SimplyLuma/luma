#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
#
# End-to-end: choosing Official, Beta or Nightly on real Luma installs, in
# disposable VMs on the build host, against a stand-in for the public download
# server (every channel public, ADR-030 section 4) and, for the compatibility
# path, a stand-in for Hub's enrollment service. Never the live Hub or dl.
#
#   e2e.sh --from-new COMMIT --from-old COMMIT --to COMMIT --rpms DIR [--only new|old] [--keep]
#
# new: install --from-new, replace luma-update with the RPM under test (test VM
#      only; the pre-override deployment is cleaned up so rollback means the
#      update), follow Official, then choose Nightly exactly as Depot does
#      (SetChannel, then Check): --to is found and staged from the public path,
#      no credential -> restart -> booted --to -> rollback -> booted
#      --from-new -> back to Official. Then the compatibility path: EnrollPreview
#      through the Hub stand-in, a device removed at Hub is refused by the
#      download server with an error luma-update names, LeavePreview revokes.
# old: install --from-old (a nightly .9-era install with luma-update .4), run
#      the documented one-time step (rpm-ostree upgrade from the public
#      repository) -> restart -> booted --to; with the RPM under test in place
#      (standing in for tonight's nightly), the agent follows Nightly with no
#      credential and Depot's choice (SetChannel stable/nightly) works.
#
# Prints PASS/FAIL lines and writes everything to the work directory; exits 1
# on any FAIL. Tokens and credentials are generated here and never printed.

set -uo pipefail
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH='' cd -- "$here/../../.." && pwd)
luma_os_repo_root=$repo_root
# shellcheck disable=SC1091
. "$repo_root/scripts/os/lib/common.sh"
luma_os_require_root

from_new="" from_old="" to="" rpms="" only="" keep=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --from-new) from_new=$2; shift 2 ;;
    --from-old) from_old=$2; shift 2 ;;
    --to) to=$2; shift 2 ;;
    --rpms) rpms=$2; shift 2 ;;
    --only) only=$2; shift 2 ;;
    --keep) keep=1; shift ;;
    *) printf 'usage: see the header of %s\n' "$0" >&2; exit 2 ;;
  esac
done
[ -n "$to" ] && [ -n "$rpms" ] || { printf 'need --to and --rpms\n' >&2; exit 2; }

run_id=$(date -u +%Y%m%dT%H%M%SZ)
work=${LUMA_E2E_WORK:-$LUMA_OS_ROOT/vm/preview-enroll-$run_id}     # repository view: hard links, same filesystem
disks=${LUMA_E2E_DISKS:-/mnt/luma-secondary/luma-preview-enroll-e2e/$run_id}
install -d -m 0755 "$work" "$disks"
chcon -t virt_image_t "$disks" 2>/dev/null || true
exec > >(tee -a "$work/e2e.log") 2>&1
channel=nightly
ref="luma/1/x86_64/$channel"
candidate_repo="$LUMA_OS_ROOT/ostree/candidate-repo"
iso=$LUMA_OS_ROOT/vm/media/Fedora-Silverblue-ostree-x86_64-44-1.7.iso
failures=0
pass() { printf 'PASS %s\n' "$*"; }
fail() { printf 'FAIL %s\n' "$*"; failures=$((failures + 1)); }
check() { local what=$1; shift; if "$@"; then pass "$what"; else fail "$what"; fi; }
note() { printf '== %s\n' "$*"; }

# ── Host side ───────────────────────────────────────────────────────────────
note "repository view in $work/repo"
ostree init --repo="$work/repo" --mode=archive --collection-id="$LUMA_OS_COLLECTION_ID"
for commit in $from_new $from_old $to; do
  if ! ostree pull-local --repo="$work/repo" --untrusted "$candidate_repo" "$commit" >/dev/null ||
     ! ostree --repo="$work/repo" show "$commit" >/dev/null 2>&1; then
    printf 'FAIL commit %s is not in %s\nE2E FAIL (setup)\n' "$commit" "$candidate_repo"; exit 1
  fi
done
luma_os_gpg_unlock   # never luma_os_gpg_lock here: it would stop a gate signing with the same agent
point_ref() {
  ostree refs --repo="$work/repo" --create="$ref" --force "$1" >/dev/null
  ostree summary --repo="$work/repo" --update --gpg-sign="$(luma_os_gpg_fingerprint)" \
    --gpg-homedir="$(luma_os_gpg_home)" >/dev/null
}
version_of() { ostree --repo="$work/repo" show "$1" | sed -n 's/^Version: *//p'; }

free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
dl_port=$(free_port); hub_port=$(free_port)
gateway=$(ip -4 route show default | awk '{ print $3; exit }')
install -d -m 0700 "$work/secrets" "$work/graph-key"
install -d -m 0755 "$work/graphs" "$work/export"
: >"$work/staff-credentials"

# A test Hub database with Hub's own devices table, and one synthetic signed-in device.
token=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
printf '%s\n' "$token" >"$work/secrets/device-token"; chmod 0600 "$work/secrets/device-token"
python3 - "$work/hub.sqlite" "$work/secrets/device-token" <<'PY'
import hashlib, sqlite3, sys, time
db = sqlite3.connect(sys.argv[1])
db.executescript("""CREATE TABLE users(id INTEGER PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL, name TEXT NOT NULL);
CREATE TABLE devices(id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
  registered_at INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
INSERT INTO users VALUES(1, 'https://e2e.invalid', 'e2e-test-account', 'E2E test account');""")
token = open(sys.argv[2]).read().strip()
db.execute("INSERT INTO devices VALUES('e2e-vm', 1, 'E2E VM', ?, ?, 0)", (hashlib.sha256(token.encode()).hexdigest(), int(time.time())))
db.commit()
PY
unset token

python3 "$repo_root/ops/update-preview-credentials/luma-update-preview-hub.py" --listen "127.0.0.1:$hub_port" \
  --hub-database "$work/hub.sqlite" --database "$work/preview.sqlite" --export-dir "$work/export" \
  >"$work/hub.log" 2>&1 &
hub_pid=$!
LUMA_DL_PREVIEW_CREDENTIAL_FILES="$work/staff-credentials:$work/export/hub-preview-credentials" \
  python3 "$here/dl-test-server.py" "$dl_port" "$work/repo" "$work/graphs" "$work/dl-requests.jsonl" \
  >"$work/dl.log" 2>&1 &
dl_pid=$!
domains=()
cleanup() {
  kill "$hub_pid" "$dl_pid" 2>/dev/null
  if [ "$keep" = 0 ]; then
    for d in "${domains[@]}"; do
      virsh destroy "$d" >/dev/null 2>&1; virsh undefine --nvram "$d" >/dev/null 2>&1
    done
    rm -rf "$disks"
  fi
  rm -rf "/root/.ssh/luma-preview-enroll-$run_id"
}
trap cleanup EXIT

LUMA_OS_TOOLS_MOUNTS="$work/graph-key" luma_os_tools \
  minisign -G -W -p "$work/graph-key/test.pub" -s "$work/graph-key/test.key" >/dev/null
sign_graph() { # sign_graph CHANNEL COMMIT...
  local name=$1; shift
  python3 - "$work/graphs/$name.json" "$name" "$work/repo" "$@" <<'PY'
import datetime, json, subprocess, sys
path, channel, repo, commits = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
releases = []
for commit in commits:
    version = next(l.split(":", 1)[1].strip() for l in subprocess.run(
        ["ostree", f"--repo={repo}", "show", commit], capture_output=True, text=True, check=True).stdout.splitlines()
        if l.startswith("Version:"))
    releases.append({"version": version, "commit": commit, "released_at": "2026-01-01T00:00:00Z",
        "rollout": {"start_at": "2026-01-01T00:00:00Z", "start_percentage": 1.0, "duration_minutes": 0},
        "paused": False, "deadend": False, "deadend_reason": None, "barrier": False, "importance": "normal",
        "notes_url": None, "summary": f"E2E {version}", "download_bytes_estimate": 0})
json.dump({"schema_version": 1, "channel": channel, "arch": "x86_64",
           "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "releases": releases}, open(path, "w"), indent=2)
PY
  LUMA_OS_TOOLS_MOUNTS="$work/graph-key $work/graphs" luma_os_tools minisign -S -s "$work/graph-key/test.key" \
    -m "$work/graphs/$name.json" -t "e2e channel=$name" >/dev/null
  sleep 1
}
for _ in $(seq 1 30); do curl -sf "http://127.0.0.1:$hub_port/healthz" >/dev/null && break; sleep 1; done
check "test Hub enrollment service is up" curl -sf "http://127.0.0.1:$hub_port/healthz" -o /dev/null

# Confined ssh may only read keys below /root/.ssh (ssh_home_t), not the pipeline volume.
ssh_dir=/root/.ssh/luma-preview-enroll-$run_id
install -d -m 0700 "$ssh_dir"
ssh-keygen -q -t ed25519 -N '' -C "luma-preview-enroll-$run_id" -f "$ssh_dir/ssh-key"
release_key_b64=$(base64 -w0 "$LUMA_OS_KEYS/luma-os-release.gpg")

install_vm() { # install_vm NAME COMMIT
  local name=$1 commit=$2 domain port
  domain="luma-preview-enroll-$name-$run_id"
  port=$(free_port)
  printf '%s\n' "$port" >"$work/$domain.ssh-port"
  point_ref "$commit"
  sed -e "s#@SSH_PUBLIC_KEY@#$(cat "$ssh_dir/ssh-key.pub")#" -e "s#@RELEASE_KEY_BASE64@#$release_key_b64#" \
      -e "s#@STATEROOT@#$LUMA_OS_STATEROOT#g" -e "s#@REMOTE@#$LUMA_OS_REMOTE#g" \
      -e "s#@REPO_URL@#http://$gateway:$dl_port/os/repo#g" -e "s#@REF@#$ref#g" -e "s#@CHANNEL@#$channel#g" \
      -e "s|@FIRSTBOOT@|firstboot --disable|" \
      "$repo_root/tests/os/gate/luma-gate.ks.in" >"$work/$name.ks"
  qemu-img create -q -f qcow2 "$disks/$domain.qcow2" 48G
  chcon -t virt_image_t "$disks/$domain.qcow2" 2>/dev/null || true
  domains+=("$domain")
  note "installing $(version_of "$commit") into $domain"
  virt-install --connect qemu:///system --name "$domain" --memory 4608 --vcpus 4 --cpu host-passthrough \
    --machine q35 --boot uefi \
    --disk "path=$disks/$domain.qcow2,format=qcow2,bus=virtio,cache=unsafe,discard=unmap" \
    --location "$iso,kernel=images/pxeboot/vmlinuz,initrd=images/pxeboot/initrd.img" \
    --initrd-inject "$work/$name.ks" \
    --extra-args "inst.ks=file:/$name.ks inst.text console=ttyS0 inst.notmux" \
    --network "passt,portForward0.proto=tcp,portForward0.range0.start=$port,portForward0.range0.to=22" \
    --graphics vnc,listen=127.0.0.1 --video virtio --rng /dev/urandom \
    --serial "file,path=/var/log/libvirt/qemu/$domain-serial.log" --os-variant fedora-unknown \
    --noreboot --noautoconsole --wait 60 >"$work/virt-install-$name.log" 2>&1 || return 1
  for _ in $(seq 1 120); do [ "$(virsh domstate "$domain" 2>/dev/null)" = "shut off" ] && break; sleep 5; done
  virsh start "$domain" >/dev/null || return 1
  wait_ssh "$domain" 900
}
guest() { local port; port=$(cat "$work/$1.ssh-port"); shift
  ssh -q -p "$port" -i "$ssh_dir/ssh-key" -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR root@127.0.0.1 "$@"; }
guest_copy() { local port; port=$(cat "$work/$1.ssh-port")
  scp -q -P "$port" -i "$ssh_dir/ssh-key" -o BatchMode=yes -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "$2" "root@127.0.0.1:$3"; }
wait_ssh() { local deadline=$((SECONDS + ${2:-900})); sleep 20
  while [ "$SECONDS" -lt "$deadline" ]; do guest "$1" true 2>/dev/null && return 0; sleep 5; done; return 1; }
restart_vm() { guest "$1" 'systemctl reboot' >/dev/null 2>&1 || true; sleep 30; wait_ssh "$1" 900 &&
  guest "$1" 'systemctl is-system-running --wait >/dev/null 2>&1; true'; }
field() { guest "$1" 'luma-update status --json' 2>/dev/null |
  python3 -c 'import json,sys; v=json.load(sys.stdin).get(sys.argv[1]); print("" if v is None else v)' "$2"; }
booted() { guest "$1" 'rpm-ostree status --json' | python3 -c 'import json,sys; print(next(d["checksum"] for d in json.load(sys.stdin)["deployments"] if d["booted"]))'; }
dl_count() { python3 -c 'import json,sys; print(sum(1 for l in open(sys.argv[1]) if (lambda r: r["kind"]==sys.argv[2] and r["status"]==int(sys.argv[3]))(json.loads(l))))' "$work/dl-requests.jsonl" "$1" "$2" 2>/dev/null || echo 0; }
hub_events() { grep -c "\"event\": \"$1\"" "$work/hub.log" || true; }
rig_config() { # the device's update.conf, pointed at this rig (allow_insecure_urls: test VMs only)
  guest "$1" "install -d -m 0755 /etc/luma/update-graph-keys.d && cat > /etc/luma/update.conf <<EOF
[update]
graph_url = http://$gateway:$dl_port/os/graph/{channel}.json
stable_repo_url = http://$gateway:$dl_port/os/repo
preview_repo_url = http://$gateway:$dl_port/os/preview/{credential}/repo
preview_credentials_url = http://$gateway:$hub_port/api/updates/preview-credentials
events_url = http://$gateway:$dl_port/events
allow_insecure_urls = true
EOF
systemctl stop luma-updated.timer >/dev/null 2>&1; true"
  guest_copy "$1" "$work/graph-key/test.pub" /etc/luma/update-graph-keys.d/e2e-test.pub
  guest "$1" 'systemctl restart luma-updated.service >/dev/null 2>&1; true'
}
sign_graph stable ${from_new:-$from_old}
sign_graph nightly $from_new $from_old $to
to_version=$(version_of "$to")

# ── new: a recent nightly with the luma-update under test ──────────────────
override_agent() { # replace luma-update with the RPM under test; rollback then means the update, not this
  local vm=$1 rpm
  for rpm in "$rpms"/luma-update-*.noarch.rpm; do guest_copy "$vm" "$rpm" /var/tmp/; done
  guest "$vm" 'rpm-ostree override replace /var/tmp/luma-update-*.noarch.rpm' >>"$work/override.log" 2>&1
  restart_vm "$vm"
  guest "$vm" 'rpm-ostree cleanup -r' >>"$work/override.log" 2>&1
  check "luma-update under test is running ($(guest "$vm" 'rpm -q luma-update'))" \
    guest "$vm" "rpm -q luma-update | grep -Fq \"$(basename "$rpms"/luma-update-*.noarch.rpm .noarch.rpm)\""
}
wait_staged() { # automatic download may already be running; ask for it if not, then wait (up to 90 minutes)
  local vm=$1 want=$2 deadline=$((SECONDS + 5400)) state
  while [ "$SECONDS" -lt "$deadline" ]; do
    state=$(field "$vm" state)
    case "$state" in
      checking|downloading|'') sleep 20 ;;
      available) guest "$vm" 'luma-update download' || sleep 20 ;;
      *) [ "$(field "$vm" staged_commit)" = "$want" ] && return 0; guest "$vm" 'luma-update download' || sleep 20
         [ "$(field "$vm" staged_commit)" = "$want" ] && return 0; sleep 20 ;;
    esac
  done
  return 1
}
dbus_call() { guest "$1" "busctl --system --timeout=1500 call org.projectluma.Update1 /org/projectluma/Update1 org.projectluma.Update1 $2"; }

if [ -n "$from_new" ] && [ "$only" != old ]; then
  vm=luma-preview-enroll-new-$run_id
  if install_vm new "$from_new"; then
    point_ref "$to"   # the channel's head is the newest release, as on dl
    pass "installed $(version_of "$from_new")"
    override_agent "$vm"
    rig_config "$vm"
    guest "$vm" 'luma-update channel stable' >"$work/new-official.log" 2>&1
    check "follows Official" test "$(field "$vm" channel)" = stable
    check "every channel on offer, no account" test "$(field "$vm" available_channels)" = "['stable', 'beta', 'nightly']"
    before_preview=$(dl_count preview 200); before_public=$(dl_count public 200)
    # Depot's choice: SetChannel, then Check.
    dbus_call "$vm" 'SetChannel s nightly' >"$work/new-pick.log" 2>&1
    guest "$vm" 'luma-update check' >>"$work/new-pick.log" 2>&1
    wait_staged "$vm" "$to" >>"$work/new-pick.log" 2>&1
    check "chose Nightly with no credential" test "$(field "$vm" channel)/$(field "$vm" preview_enrolled)" = nightly/False
    check "$to_version found and staged" test "$(field "$vm" staged_version)" = "$to_version"
    check "it came from the public path" test "$(dl_count public 200)" -gt "$before_public"
    check "no credential path was used" test "$(dl_count preview 200)" = "$before_preview"
    check "no credential on the computer" guest "$vm" '! test -e /etc/luma/update-preview-credential'
    restart_vm "$vm"
    check "restarted into $to_version" test "$(booted "$vm")" = "$to"
    check "follows Nightly after the restart" test "$(field "$vm" channel)" = nightly
    guest "$vm" 'luma-update rollback' >"$work/new-rollback.log" 2>&1
    restart_vm "$vm"
    check "rollback restarted into $(version_of "$from_new")" test "$(booted "$vm")" = "$from_new"
    dbus_call "$vm" 'SetChannel s stable' >"$work/new-back.log" 2>&1
    check "back to Official" test "$(field "$vm" channel)" = stable
    check "the mirror list still names the public repository" guest "$vm" "grep -Fxq 'http://$gateway:$dl_port/os/repo' /etc/luma/update-mirrorlist"

    # Compatibility: a computer enrolled through Hub before channels were public.
    guest_copy "$vm" "$work/secrets/device-token" /root/.e2e-device-token
    dbus_call "$vm" 'EnrollPreview ss nightly "$(cat /root/.e2e-device-token)"' >"$work/new-enroll.log" 2>&1
    check "compat: EnrollPreview through Hub" test "$(field "$vm" preview_enrolled)/$(field "$vm" preview_source)" = True/hub
    check "compat: credential and mirror list root-only" guest "$vm" \
      'test "$(stat -c %a /etc/luma/update-preview-credential)" = 600 && test "$(stat -c %a /etc/luma/update-mirrorlist)" = 600 && grep -q /os/preview/ /etc/luma/update-mirrorlist'
    check "compat: the credential path answers" guest "$vm" 'ostree remote summary luma >/dev/null 2>&1'
    python3 -c 'import sqlite3,sys; d=sqlite3.connect(sys.argv[1]); d.execute("UPDATE devices SET revoked=1"); d.commit()' "$work/hub.sqlite"
    sleep 6
    check "compat: Hub revoked the removed device's credential" grep -q device-removed "$work/hub.log"
    guest "$vm" "ostree remote summary luma 2>&1; true" >"$work/new-revoked.log" 2>&1
    check "compat: the download server refuses it" test "$(dl_count preview 403)" -ge 1
    check "compat: the refusal is one luma-update classifies (access-denied)" \
      grep -Eq '\b(HTTP 40[13]|40[13] (Forbidden|Unauthorized))\b|No valid mirrors were found in mirrorlist' "$work/new-revoked.log"
    python3 -c 'import sqlite3,sys; d=sqlite3.connect(sys.argv[1]); d.execute("UPDATE devices SET revoked=0"); d.commit()' "$work/hub.sqlite"
    dbus_call "$vm" 'EnrollPreview ss nightly "$(cat /root/.e2e-device-token)"' >>"$work/new-enroll.log" 2>&1
    guest "$vm" 'rm -f /root/.e2e-device-token; luma-update leave-preview' >"$work/new-leave.log" 2>&1
    check "compat: Leave revoked at Hub" grep -q '"reason": "left"' "$work/hub.log"
    check "compat: Leave is back on Official from the public repository" test "$(field "$vm" preview_enrolled)/$(field "$vm" channel)" = False/stable
    check "compat: mirror list public again" guest "$vm" "grep -Fxq 'http://$gateway:$dl_port/os/repo' /etc/luma/update-mirrorlist"
    check "no credential in luma-updated's journal" guest "$vm" \
      "! journalctl -b --no-pager -o cat -u luma-updated | grep -Eq '/os/preview/[A-Za-z0-9_-]{16,}/'"
  else
    fail "install of $(version_of "$from_new")"
  fi
fi

# ── old: a nightly .9-era install, brought up once ─────────────────────────
if [ -n "$from_old" ] && [ "$only" != new ]; then
  vm=luma-preview-enroll-old-$run_id
  if install_vm old "$from_old"; then
    point_ref "$to"
    pass "installed $(version_of "$from_old") ($(guest "$vm" 'rpm -q luma-update'))"
    rig_config "$vm"
    guest "$vm" 'luma-update check' >"$work/old-check-before.log" 2>&1
    check "before: the old agent refuses Nightly without a credential (why the one-time step exists)" \
      test "$(field "$vm" last_error_class)" = preview
    before_preview=$(dl_count preview 200)
    guest "$vm" 'rpm-ostree upgrade' >"$work/old-upgrade.log" 2>&1
    check "rpm-ostree upgrade staged $to_version from the public repository" \
      guest "$vm" "rpm-ostree status --json | python3 -c 'import json,sys; d=json.load(sys.stdin)[\"deployments\"]; sys.exit(0 if any(x.get(\"staged\") and x[\"checksum\"]==\"$to\" for x in d) else 1)'"
    check "no credential path was used" test "$(dl_count preview 200)" = "$before_preview"
    restart_vm "$vm"
    check "restarted into $to_version" test "$(booted "$vm")" = "$to"
    override_agent "$vm"   # stands in for tonight's nightly, which carries this agent
    guest "$vm" 'luma-update check' >"$work/old-check-after.log" 2>&1
    check "after: the agent follows Nightly with no credential and no error" \
      test "$(field "$vm" channel)/$(field "$vm" last_error_class)/$(field "$vm" preview_enrolled)" = nightly//False
    dbus_call "$vm" 'SetChannel s stable' >"$work/old-pick.log" 2>&1
    check "Depot's choice works: Official" test "$(field "$vm" channel)" = stable
    dbus_call "$vm" 'SetChannel s nightly' >>"$work/old-pick.log" 2>&1
    check "Depot's choice works: Nightly again" test "$(field "$vm" channel)" = nightly
  else
    fail "install of $(version_of "$from_old")"
  fi
fi

note "work directory: $work"
[ "$failures" = 0 ] && { printf 'E2E PASS\n'; exit 0; }
printf 'E2E FAIL (%s)\n' "$failures"; exit 1
