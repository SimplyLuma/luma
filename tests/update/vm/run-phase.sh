#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# run-phase.sh PHASE — one step of the luma-update VM integration test.
# Run as root on the rig VM after prepare-rig.sh. Phases that end in a restart
# print RESTART; drive-vm.sh restarts the VM and runs the next phase.
# Evidence is appended to /var/lib/luma-update-test/evidence/PHASE.log.
set -euo pipefail
rig=/var/lib/luma-update-test
phase=${1:?phase}
log=$rig/evidence/$phase.log
exec > >(tee -a "$log") 2>&1
stable=luma/1/x86_64/stable
beta=luma/1/x86_64/beta
S=${RIG_SERIES:-1}   # release series: 1 for the first run, 2 for a rerun with a rebuilt agent

say() { printf '\n== %s\n' "$*"; }
run() { printf '$ %s\n' "$*"; "$@"; }
commit_of() { ostree --repo="$rig/repo" rev-parse "$1"; }
field() { luma-update status --json | python3 -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }
expect() { local name=$1 want=$2 got; got=$(field "$name"); if [ "$got" != "$want" ]; then echo "FAIL: $name=$got, expected $want"; exit 1; fi; echo "ok: $name=$got"; }
wait_state() { for _ in $(seq 1 600); do case "$(field state)" in checking|downloading) sleep 1 ;; *) return 0 ;; esac; done; }
graph() { python3 "$rig/publish-graph.py" "$@" >/dev/null; echo "published $1 graph: $2"; }
events() { tail -n "${1:-5}" "$rig/evidence/hub-requests.jsonl" 2>/dev/null || :; }
release_commit() { cat "$rig/release-$1"; }
make_release() { local c; c=$("$rig/make-release.sh" "$@" | tail -1); echo "$c" > "$rig/release-$1"; echo "release $1 = $c"; }

printf '#### phase %s at %s\n' "$phase" "$(date -u +%FT%TZ)"
case "$phase" in
  migrate)
    say "Release 1.0.0 is the booted tree; follow luma:$stable as the migration script would"
    [ -f "$rig/release-1.0.0" ] || make_release 1.0.0 "$stable" booted
    graph stable "[{\"version\":\"1.0.0\",\"commit\":\"$(release_commit 1.0.0)\"}]"
    run rpm-ostree reset
    run rpm-ostree rebase "luma:$stable" "$(release_commit 1.0.0)"
    run rpm-ostree status
    echo RESTART ;;

  self-update)
    say "Series $S: $S.0.0 carries the rebuilt agent (RIG_PAYLOAD); the running agent updates to it"
    [ -n "${RIG_PAYLOAD:-}" ] || { echo "set RIG_PAYLOAD to the extracted rebuilt RPM" >&2; exit 2; }
    base=$(rpm-ostree status --json | python3 -c 'import json,sys; print(next(d.get("base-checksum") or d["checksum"] for d in json.load(sys.stdin)["deployments"] if d["booted"]))')
    make_release $S.0.0 "$stable" "$base"
    graph stable "[{\"version\":\"$S.0.0\",\"commit\":\"$(release_commit $S.0.0)\"}]"
    run rpm-ostree cleanup -p
    run luma-update check
    wait_state
    expect staged_version $S.0.0
    say "Rig only: log updateqa in automatically so the session notifier and polkit's active-session rules can be observed"
    if ! grep -q '^AutomaticLogin=updateqa' /etc/gdm/custom.conf 2>/dev/null; then
      printf '[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin=updateqa\nWaylandEnable=true\n' > /etc/gdm/custom.conf
      install -d -m 0700 -o updateqa -g updateqa /var/home/updateqa/.config
      runuser -u updateqa -- touch /var/home/updateqa/.config/gnome-initial-setup-done
    fi
    echo RESTART-VIA-APPLY ;;

  notifier)
    say "The session notifier posted the ready notification (screenshot taken from the host)"
    uid=$(id -u updateqa)
    run loginctl list-sessions --no-legend
    run runuser -u updateqa -- env XDG_RUNTIME_DIR=/run/user/$uid systemctl --user is-enabled luma-update-notifier.path luma-update-notifier.timer || :
    run runuser -u updateqa -- env XDG_RUNTIME_DIR=/run/user/$uid systemctl --user status luma-update-notifier.path --no-pager || :
    journalctl _UID="$uid" --since -15min --no-pager | grep -i 'luma-update-notifier\|Show Luma system update' | tail -8 || :
    run cat /var/home/updateqa/.local/state/luma-update/notifier.json || : ;;

  up-to-date)
    say "Booted $S.0.0 from the Luma channel"
    run rpm-ostree status --booted
    run luma-update check || :
    expect state idle
    expect channel stable
    expect booted_version $S.0.0
    expect managed True
    run systemctl is-enabled luma-updated.timer luma-update-boot.service greenboot-healthcheck.service
    say "With nothing staged, Apply() and Cancel() do nothing and say so"
    out=$(luma-update apply 2>&1); echo "$out"
    case "$out" in *"nothing is waiting for a restart"*) echo "ok: Apply NothingToDo" ;; *) echo "FAIL: apply: $out"; exit 1 ;; esac
    out=$(luma-update cancel 2>&1); echo "$out"
    case "$out" in *"no download is running"*) echo "ok: Cancel NothingToDo" ;; *) echo "FAIL: cancel: $out"; exit 1 ;; esac ;;

  apply-guards)
    say "A block inhibitor stops Apply() even though luma-updated is root (RebootWithFlags, ROOT_CHECK_INHIBITORS)"
    expect state staged
    systemd-inhibit --what=shutdown --mode=block --who=luma-update-rig --why="rig: hold restart" sleep 120 &
    holder=$!
    sleep 3
    run systemd-inhibit --list --no-pager
    out=$(luma-update apply 2>&1) && { echo "FAIL: apply returned success past a block inhibitor: $out"; kill "$holder"; exit 1; }
    echo "$out"
    case "$out" in *"preventing the restart"*) echo "ok: Apply refused with Inhibited" ;; *) echo "FAIL: unexpected: $out"; kill "$holder"; exit 1 ;; esac
    kill "$holder"; wait "$holder" 2>/dev/null || :
    expect state staged
    journalctl -u luma-updated --since -2min --no-pager | grep -E "allowed org.freedesktop.login1|preventing" | tail -3 || : ;;

  stage-good)
    say "$S.0.1 is published; the agent stages exactly that commit"
    make_release $S.0.1 "$stable" "$(release_commit $S.0.0)"
    graph stable "[{\"version\":\"$S.0.0\",\"commit\":\"$(release_commit $S.0.0)\"},{\"version\":\"$S.0.1\",\"commit\":\"$(release_commit $S.0.1)\"}]"
    run luma-update check
    wait_state
    expect state staged
    expect staged_version $S.0.1
    expect staged_commit "$(release_commit $S.0.1)"
    run rpm-ostree status
    say "rpm-ostree knows luma-update drives updates; a manual upgrade is refused"
    rpm-ostree status | grep -i 'driver' || echo "(no driver line in status)"
    manual=$(rpm-ostree upgrade 2>&1 || :)
    echo "$manual"
    case "$manual" in *"driven by Luma Update"*) echo "ok: manual upgrade refused while Luma Update drives updates" ;; *) echo "FAIL: manual upgrade was not refused"; exit 1 ;; esac
    say "Reports so far"
    events 3
    say "Restart through the agent (logind)"
    run luma-update status
    echo RESTART-VIA-APPLY ;;

  verify-good)
    say "After restarting into $S.0.1: greenboot trial boot, then booted report"
    run rpm-ostree status --booted
    journalctl -b -u greenboot-healthcheck --no-pager | grep -E 'luma|GREEN|passed|failed' || :
    for _ in $(seq 1 60); do grep -q '"result": "booted"' "$rig/evidence/hub-requests.jsonl" 2>/dev/null && break; luma-update check >/dev/null 2>&1 || :; sleep 5; done
    expect booted_version $S.0.1
    expect state idle
    events 4
    grep -q '"result": "booted"' "$rig/evidence/hub-requests.jsonl" && echo "ok: booted transition reported"
    run grub2-editenv /boot/grub2/grubenv list ;;

  stage-bad)
    say "$S.0.2 breaks luma-updated; the agent stages it"
    make_release $S.0.2 "$stable" "$(release_commit $S.0.1)" break-agent
    graph stable "[{\"version\":\"$S.0.1\",\"commit\":\"$(release_commit $S.0.1)\"},{\"version\":\"$S.0.2\",\"commit\":\"$(release_commit $S.0.2)\"}]"
    run luma-update check
    wait_state
    expect staged_version $S.0.2
    run rpm-ostree status
    echo RESTART-VIA-APPLY ;;

  verify-rollback)
    say "greenboot failed the trial boots of $S.0.2 and rolled back to $S.0.1"
    run rpm-ostree status
    journalctl --list-boots --no-pager | tail -6
    for boot in -4 -3 -2 -1 0; do
      echo "--- boot $boot"
      journalctl -b "$boot" -u greenboot-healthcheck --no-pager 2>/dev/null | grep -E 'luma|Rollback|boot_counter|reboot|RED|GREEN|FALLBACK' | tail -8 || :
    done
    run systemctl status luma-update-boot.service --no-pager || :
    expect booted_version $S.0.1
    expect rolled_back_version $S.0.2
    python3 -c "import json; s=json.load(open('/var/lib/luma-update/state.json')); print('do_not_retry:', s['do_not_retry'])"
    grep -q '"result": "rolled_back"' "$rig/evidence/hub-requests.jsonl" || luma-update check >/dev/null 2>&1 || :
    sleep 3
    grep '"result": "rolled_back"' "$rig/evidence/hub-requests.jsonl" | grep -q '"error_class": "health-check"' && echo "ok: rolled_back transition reported"
    events 4
    say "$S.0.2 is not retried"
    run luma-update check || :
    expect state idle
    expect available_version "" ;;

  skip-failed)
    say "$S.0.3 is published; the agent passes over $S.0.2 and stages $S.0.3"
    make_release $S.0.3 "$stable" "$(release_commit $S.0.1)"
    graph stable "[{\"version\":\"$S.0.1\",\"commit\":\"$(release_commit $S.0.1)\"},{\"version\":\"$S.0.2\",\"commit\":\"$(release_commit $S.0.2)\"},{\"version\":\"$S.0.3\",\"commit\":\"$(release_commit $S.0.3)\"}]"
    run luma-update check
    wait_state
    expect staged_version $S.0.3
    say "$S.0.3 is pulled before restart; the staged deployment is removed"
    graph stable "[{\"version\":\"$S.0.1\",\"commit\":\"$(release_commit $S.0.1)\"},{\"version\":\"$S.0.3\",\"commit\":\"$(release_commit $S.0.3)\",\"deadend\":true,\"deadend_reason\":\"rig: pulled\"}]"
    run luma-update check || :
    wait_state
    expect staged_version ""
    run rpm-ostree status ;;

  signed-rollback)
    say "$S.0.1 (booted) is pulled with a signed rollback_to $S.0.0: an older commit is staged"
    graph stable "[{\"version\":\"$S.0.0\",\"commit\":\"$(release_commit $S.0.0)\"},{\"version\":\"$S.0.1\",\"commit\":\"$(release_commit $S.0.1)\",\"deadend\":true,\"deadend_reason\":\"rig: bad\",\"rollback_to\":{\"version\":\"$S.0.0\",\"commit\":\"$(release_commit $S.0.0)\"}}]"
    run luma-update check
    wait_state
    expect staged_version $S.0.0
    expect booted_deadend_reason ""
    run rpm-ostree status
    say "Without rollback_to, nothing older is staged"
    run rpm-ostree cleanup -p
    graph stable "[{\"version\":\"$S.0.0\",\"commit\":\"$(release_commit $S.0.0)\"},{\"version\":\"$S.0.1\",\"commit\":\"$(release_commit $S.0.1)\",\"deadend\":true,\"deadend_reason\":\"rig: bad\"}]"
    run luma-update check || :
    wait_state
    expect staged_version ""
    expect booted_deadend_reason "rig: bad"
    graph stable "[{\"version\":\"$S.0.0\",\"commit\":\"$(release_commit $S.0.0)\"},{\"version\":\"$S.0.1\",\"commit\":\"$(release_commit $S.0.1)\"}]"
    run luma-update check || : ;;

  metered)
    say "On a metered connection a new release is offered, not downloaded"
    conn=$(nmcli -g NAME,DEVICE connection show --active | awk -F: '$2!="lo"{print $1; exit}')
    run nmcli connection modify "$conn" connection.metered yes
    run nmcli connection up "$conn" >/dev/null
    sleep 5
    make_release $S.0.4 "$stable" "$(release_commit $S.0.1)"
    graph stable "[{\"version\":\"$S.0.1\",\"commit\":\"$(release_commit $S.0.1)\"},{\"version\":\"$S.0.4\",\"commit\":\"$(release_commit $S.0.4)\"}]"
    run luma-update check || :
    wait_state
    expect state available
    expect metered True
    say "The person downloads it anyway"
    run luma-update download
    expect state staged
    expect staged_version $S.0.4
    run nmcli connection modify "$conn" connection.metered unknown
    run nmcli connection up "$conn" >/dev/null
    run rpm-ostree cleanup -p ;;

  busy)
    say "Another rpm-ostree transaction is running: the agent refuses to start one"
    python3 - <<'PY' &
import time, gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib
bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
os_path = bus.call_sync("org.projectatomic.rpmostree1", "/org/projectatomic/rpmostree1/Sysroot",
    "org.freedesktop.DBus.Properties", "Get", GLib.Variant("(ss)", ("org.projectatomic.rpmostree1.Sysroot", "Booted")),
    None, 0, -1, None).unpack()[0]
address = bus.call_sync("org.projectatomic.rpmostree1", os_path, "org.projectatomic.rpmostree1.OS", "RefreshMd",
    GLib.Variant("(a{sv})", ({},)), None, 0, -1, None).unpack()[0]
print("holding transaction", address, flush=True)
time.sleep(25)
PY
    holder=$!
    sleep 3
    run rpm-ostree status | head -3
    run luma-update download || :
    expect last_error_class busy
    wait "$holder" || :
    run luma-update check || : ;;

  preview)
    say "Enroll in early updates (beta) with a Connect device token; the luma remote points at the root-only mirror list"
    make_release $S.1.0-beta.1 "$beta" "$(release_commit $S.0.1)"
    graph beta "[{\"version\":\"$S.1.0-beta.1\",\"commit\":\"$(release_commit $S.1.0-beta.1)\"}]"
    run busctl --system call org.projectluma.Update1 /org/projectluma/Update1 org.projectluma.Update1 EnrollPreview ss beta rig-connect-device-token
    run ls -l /etc/luma/update-preview-credential /etc/luma/update-preview-mirrorlist /etc/ostree/remotes.d/luma.conf
    run grep -n '^url=' /etc/ostree/remotes.d/luma.conf
    grep -q rigpreviewcredential /etc/ostree/remotes.d/luma.conf && { echo "FAIL: credential in world-readable remote"; exit 1; } || echo "ok: no credential in the remote file"
    grep -q rig-connect-device-token /etc/luma/update-preview-credential && { echo "FAIL: token stored"; exit 1; } || echo "ok: Connect token not stored"
    wait_state; sleep 2; wait_state
    expect channel beta
    expect preview_enrolled True
    expect staged_version $S.1.0-beta.1
    run rpm-ostree status
    rpm-ostree status | grep -q 'luma:luma/1/x86_64/beta' && echo "ok: staged origin luma:luma/1/x86_64/beta"
    say "An unprivileged user cannot read the credential; rpm-ostree still works for them"
    runuser -u updateqa -- cat /etc/luma/update-preview-mirrorlist 2>&1 | head -1 || :
    runuser -u updateqa -- rpm-ostree status >/dev/null && echo "ok: unprivileged rpm-ostree status still works"
    say "Leave early updates"
    run busctl --system call org.projectluma.Update1 /org/projectluma/Update1 org.projectluma.Update1 LeavePreview
    wait_state; sleep 2; wait_state
    expect channel stable
    expect preview_enrolled False
    expect staged_version $S.0.4
    test ! -e /etc/luma/update-preview-credential && test ! -e /etc/luma/update-preview-mirrorlist && echo "ok: credential and mirror list removed"
    run grep -n '^url=' /etc/ostree/remotes.d/luma.conf
    events 6
    run rpm-ostree cleanup -p ;;

  kargs-add)
    say "$S.0.7 declares kernel arguments in /usr/lib/bootc/kargs.d; the agent stages it with them"
    RIG_KARGS="luma.update_rig=1 efi_pstore.pstore_disable=0" make_release $S.0.7 "$stable" "$(release_commit $S.0.0)"
    graph stable "[{\"version\":\"$S.0.0\",\"commit\":\"$(release_commit $S.0.0)\"},{\"version\":\"$S.0.7\",\"commit\":\"$(release_commit $S.0.7)\"}]"
    run luma-update check
    wait_state
    expect state staged
    expect staged_commit "$(release_commit $S.0.7)"
    pending=$(rpm-ostree kargs)
    echo "pending kernel arguments: $pending"
    for karg in luma.update_rig=1 efi_pstore.pstore_disable=0; do
      case " $pending " in *" $karg "*) echo "ok: pending deployment has $karg" ;; *) echo "FAIL: pending lacks $karg"; exit 1 ;; esac
    done
    [ "$(printf '%s\n' $pending | grep -cx luma.update_rig=1)" = 1 ] && echo "ok: added once"
    python3 -c "import json; print('pending kargs_added:', json.load(open('/var/lib/luma-update/state.json'))['pending']['kargs_added'])"
    journalctl -u luma-updated --since -5min --no-pager | grep -E "kernel arguments" | tail -3 || :
    echo RESTART-VIA-APPLY ;;

  kargs-verify)
    say "Booted $S.0.7: the kernel command line carries every argument its kargs.d declares"
    run cat /proc/cmdline
    for karg in luma.update_rig=1 efi_pstore.pstore_disable=0; do
      grep -qw -- "$karg" /proc/cmdline && echo "ok: /proc/cmdline has $karg" || { echo "FAIL: /proc/cmdline lacks $karg"; exit 1; }
    done
    for _ in $(seq 1 30); do python3 -c "import json,sys; sys.exit(0 if json.load(open('/var/lib/luma-update/state.json'))['pending'] is None else 1)" && break; sleep 2; done
    expect booted_version $S.0.7
    python3 -c "import json; print('kargs_added:', json.load(open('/var/lib/luma-update/state.json'))['kargs_added'])" ;;

  kargs-drop)
    say "$S.0.8 no longer declares luma.update_rig=1: the argument the agent added is removed, the rest kept"
    RIG_KARGS="efi_pstore.pstore_disable=0" make_release $S.0.8 "$stable" "$(release_commit $S.0.0)"
    graph stable "[{\"version\":\"$S.0.7\",\"commit\":\"$(release_commit $S.0.7)\"},{\"version\":\"$S.0.8\",\"commit\":\"$(release_commit $S.0.8)\"}]"
    run luma-update check
    wait_state
    expect staged_commit "$(release_commit $S.0.8)"
    pending=$(rpm-ostree kargs)
    echo "pending kernel arguments: $pending"
    case " $pending " in *" luma.update_rig=1 "*) echo "FAIL: luma.update_rig=1 still pending"; exit 1 ;; *) echo "ok: luma.update_rig=1 removed" ;; esac
    case " $pending " in *" efi_pstore.pstore_disable=0 "*) echo "ok: efi_pstore.pstore_disable=0 kept" ;; *) echo "FAIL: efi_pstore removed"; exit 1 ;; esac
    case " $pending " in *" rhgb "*|*" root="*) echo "ok: installer arguments kept" ;; *) echo "FAIL: installer arguments lost"; exit 1 ;; esac
    echo RESTART-VIA-APPLY ;;

  kargs-verify-drop)
    say "Booted $S.0.8 without the dropped argument"
    run cat /proc/cmdline
    grep -qw -- luma.update_rig=1 /proc/cmdline && { echo "FAIL: luma.update_rig=1 still on the command line"; exit 1; } || echo "ok: luma.update_rig=1 gone"
    grep -qw -- efi_pstore.pstore_disable=0 /proc/cmdline && echo "ok: efi_pstore.pstore_disable=0 still there"
    expect booted_version $S.0.8
    for _ in $(seq 1 30); do python3 -c "import json,sys; sys.exit(0 if json.load(open('/var/lib/luma-update/state.json'))['pending'] is None else 1)" && break; sleep 2; done
    python3 -c "import json; print('kargs_added:', json.load(open('/var/lib/luma-update/state.json'))['kargs_added'])" ;;

  preview-mirrorlist)
    say "Early updates on an image whose luma remote always reads /etc/luma/update-mirrorlist"
    cp /etc/ostree/remotes.d/luma.conf "$rig/luma.conf.url-layout"
    printf 'http://127.0.0.1:8471/os/repo\n' > /etc/luma/update-mirrorlist
    chmod 0600 /etc/luma/update-mirrorlist
    sed -i 's|^url=.*|url=mirrorlist=file:///etc/luma/update-mirrorlist|' /etc/ostree/remotes.d/luma.conf
    run cat /etc/ostree/remotes.d/luma.conf
    before=$(sha256sum < /etc/ostree/remotes.d/luma.conf)
    make_release $S.1.0-beta.1 "$beta" "$(release_commit $S.0.1)"
    graph beta "[{\"version\":\"$S.1.0-beta.1\",\"commit\":\"$(release_commit $S.1.0-beta.1)\"}]"
    since=$(date -u +'%F %T')
    run busctl --system call org.projectluma.Update1 /org/projectluma/Update1 org.projectluma.Update1 EnrollPreview ss beta rig-connect-device-token
    [ "$(sha256sum < /etc/ostree/remotes.d/luma.conf)" = "$before" ] && echo "ok: the remote file is unchanged"
    run stat -c '%a %U %n' /etc/luma/update-mirrorlist /etc/luma/update-preview-credential
    grep -q '/os/preview/' /etc/luma/update-mirrorlist && echo "ok: the root-only list names the preview repository"
    test ! -e /etc/luma/update-preview-mirrorlist && echo "ok: no second mirror list"
    wait_state; sleep 2; wait_state
    expect channel beta
    expect preview_enrolled True
    expect staged_version $S.1.0-beta.1
    rpm-ostree status | grep -q 'luma:luma/1/x86_64/beta' && echo "ok: staged origin luma:luma/1/x86_64/beta"
    say "The credential stays out of luma-updated's journal"
    count=$(journalctl -u luma-updated --since "$since" --no-pager | grep -c rigpreviewcredential || :)
    echo "journal lines with the credential: $count"; [ "$count" = 0 ] && echo "ok: none"
    journalctl -u luma-updated --since "$since" --no-pager | grep -m3 '<credential>' || echo "(no redacted lines needed)"
    say "Leave early updates: the list points back at the public repository"
    run busctl --system call org.projectluma.Update1 /org/projectluma/Update1 org.projectluma.Update1 LeavePreview
    wait_state; sleep 2; wait_state
    expect channel stable
    expect preview_enrolled False
    [ "$(sha256sum < /etc/ostree/remotes.d/luma.conf)" = "$before" ] && echo "ok: the remote file is still unchanged"
    run cat /etc/luma/update-mirrorlist
    grep -qx 'http://127.0.0.1:8471/os/repo' /etc/luma/update-mirrorlist && echo "ok: public repository restored in the list"
    test ! -e /etc/luma/update-preview-credential && echo "ok: credential removed"
    run rpm-ostree cleanup -p
    say "Rig: back to the url layout for later phases"
    cp "$rig/luma.conf.url-layout" /etc/ostree/remotes.d/luma.conf
    rm -f /etc/luma/update-mirrorlist ;;

  polkit)
    say "Policy: an inactive (SSH) session may not change channels without an administrator"
    runuser -u updateqa -- busctl --system call org.projectluma.Update1 /org/projectluma/Update1 org.projectluma.Update1 SetChannel s beta 2>&1 || :
    runuser -u updateqa -- busctl --system call org.projectluma.Update1 /org/projectluma/Update1 org.projectluma.Update1 Automatic 2>&1 || :
    runuser -u updateqa -- luma-update status --json >/dev/null && echo "ok: anyone may read status"
    run pkaction --action-id org.projectluma.update.download --verbose
    if loginctl list-sessions --no-legend | grep -q updateqa; then
      say "Policy: the person at the computer (active graphical session) may check without a password"
      session_user="runuser -u updateqa -- env DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u updateqa)/bus XDG_RUNTIME_DIR=/run/user/$(id -u updateqa)"
      # Started by the user's service manager, the call is outside the SSH session and
      # polkit judges it by the user's active graphical session.
      run $session_user systemd-run --user --wait --pipe --quiet busctl --system call org.projectluma.Update1 /org/projectluma/Update1 org.projectluma.Update1 Check
      echo "ok: Check authorized for the active session"
      say "and, as an administrator (wheel), change channels without retyping a password"
      wait_state
      run $session_user systemd-run --user --wait --pipe --quiet busctl --system call org.projectluma.Update1 /org/projectluma/Update1 org.projectluma.Update1 SetChannel s stable
      echo "ok: SetChannel authorized for an active local administrator"
    fi ;;

  removed-by-hand)
    say "A staged update removed by hand in the same boot is not reported as a failure"
    make_release $S.0.6 "$stable" "$(release_commit $S.0.1)"
    graph stable "[{\"version\":\"$S.0.1\",\"commit\":\"$(release_commit $S.0.1)\"},{\"version\":\"$S.0.6\",\"commit\":\"$(release_commit $S.0.6)\"}]"
    run luma-update check
    wait_state
    expect staged_version $S.0.6
    python3 -c "import json; p=json.load(open('/var/lib/luma-update/state.json'))['pending']; print('pending boot id recorded:', bool(p and p.get('staged_boot_id')))"
    before=$(grep -c '"result": "failed"' "$rig/evidence/hub-requests.jsonl" || :)
    run rpm-ostree cleanup -p
    run luma-update status --json >/dev/null
    systemctl restart luma-updated.service
    sleep 2
    expect staged_version ""
    python3 -c "import json; print('pending after:', json.load(open('/var/lib/luma-update/state.json'))['pending'])"
    after=$(grep -c '"result": "failed"' "$rig/evidence/hub-requests.jsonl" || :)
    [ "$before" = "$after" ] && echo "ok: no failed report ($after failed reports in total)" ;;

  driver-idle)
    say "After luma-updated exits when idle, rpm-ostree still names it as the driver"
    systemctl stop luma-updated.service
    rpm-ostree status | sed -n 1,4p
    manual=$(rpm-ostree upgrade 2>&1 || :)
    echo "$manual" | head -4 ;;

  statistics-off)
    say "Statistics off: nothing is sent"
    before=$(wc -l < "$rig/evidence/hub-requests.jsonl")
    printf '[statistics]\nenabled = false\n' > /etc/luma/statistics.conf
    python3 -c "import json; p='/var/lib/luma-update/state.json'; s=json.load(open(p)); s['countme']['counted_window']=None; json.dump(s, open(p,'w'))"
    run luma-update check || :
    sleep 3
    after=$(wc -l < "$rig/evidence/hub-requests.jsonl")
    [ "$before" = "$after" ] && echo "ok: no reports while statistics are off ($after requests)"
    printf '[statistics]\nenabled = true\n' > /etc/luma/statistics.conf ;;

  *) echo "unknown phase $phase" >&2; exit 2 ;;
esac
printf '#### phase %s passed\n' "$phase"
