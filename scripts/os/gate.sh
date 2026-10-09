#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# The Luma OS release gate (ADR-030 section 3). A candidate commit may be
# published to its channel only after this passes for that exact commit.
#
#   gate.sh --build-id YYYYMMDD.N [--keep-vms] [--skip-rollback] [--skip-fresh]
#
# --skip-rollback and --skip-fresh are for diagnosing one stage; a gate run
# with either can never publish.
#
# --update-from COMMIT runs the update and rollback stages from an older
# retained release instead of the channel head (for example across a
# migration rule such as the kargs.d transition). It is additional evidence:
# its result goes to <build>/gate-update-from-<commit>.json and never replaces
# gate-result.json or gate/latest, which publication reads. With --skip-fresh
# it is judged on the update and rollback stages alone.
#
# Stages, each in a disposable VM on this host, every result recorded under
# <build>/gate/:
#
#   fresh     Anaconda installs the candidate from the candidate repository
#             over HTTP with the Luma OS Release key as the only trust anchor,
#             the machine boots, greenboot must judge the boot healthy, then
#             the installer's boot policy is applied, the machine reboots, and
#             tests/os/image-contract.sh and tests/smoke/desktop.sh must pass,
#             and tests/os/gate/depot-opens.sh must open Depot's window from its
#             desktop entry, with fwupd running, on this new install.
#   no-account
#             Anaconda installs the candidate without an account and with
#             first-boot setup left on: GDM starts the first-boot setup
#             session, which must stay up with GNOME Shell answering on its
#             bus (the image's display and Shell health checks run enforced),
#             greenboot must judge the boot healthy and no unit may fail.
#   update    Anaconda installs the channel's current published head; after
#             the same checks, the channel ref is moved to the candidate and
#             the VM stages it with `rpm-ostree upgrade`, reboots, and the
#             same checks must pass on the candidate, with /var data intact.
#   rollback  In the updated VM: a gate-only build of the candidate with a
#             health check that always fails is deployed from a separate,
#             gate-only remote and key; greenboot must return the machine to
#             the candidate by itself. Then `rpm-ostree rollback` must return
#             to the previous release and back again.
#
# With no published head (the first release of a channel) the update and
# rollback stages are recorded as not applicable; publication accepts that
# only while the channel is empty.

set -euo pipefail
. "$(dirname -- "$0")/lib/common.sh"

build_id=
keep_vms=0
skip_rollback=0
skip_fresh=0
update_from=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --skip-fresh) skip_fresh=1; shift ;;
    --build-id) build_id=${2:?}; shift 2 ;;
    --keep-vms) keep_vms=1; shift ;;
    --skip-rollback) skip_rollback=1; shift ;;
    --update-from) update_from=${2:?}; shift 2 ;;
    *) printf 'usage: %s --build-id ID [--keep-vms] [--skip-rollback] [--skip-fresh] [--update-from COMMIT]\n' "$0" >&2; exit 2 ;;
  esac
done

luma_os_require_root
luma_os_require_tools virsh virt-install qemu-img ssh ssh-keygen openssl ostree python3 base64 curl
luma_os_check_host
luma_os_vm_lock
# Room for every VM this gate installs, not just the first: the fresh, the
# no-account and the update machines take about 13.5 GiB each as they fill,
# and the gate repository grows beside them. On 2026-09-20 a gate that
# passed this check at 25 GiB filled the volume while the update machine
# was booting, and the stage failed as a 15-minute boot timeout with no
# mention of disk. 30 GiB covers two live disks plus the repository.
luma_os_check_space 30

build_dir="$LUMA_OS_ROOT/builds/$build_id"
[ -s "$build_dir/export.env" ] || luma_os_die "build has no candidate commit: $build_dir"
luma_os_load_env "$build_dir/build.env"
luma_os_load_env "$build_dir/export.env"
channel=$LUMA_OS_CHANNEL
ref=$LUMA_EXPORT_REF
candidate=$LUMA_EXPORT_CANDIDATE
previous=$LUMA_EXPORT_PARENT
if [ -n "$update_from" ]; then
  [[ "$update_from" =~ ^[0-9a-f]{64}$ ]] || luma_os_die "--update-from needs a full commit checksum: $update_from"
  ostree ls --repo="$LUMA_OS_ROOT/publish/preview-repo" "$update_from" / >/dev/null 2>&1 ||
    ostree ls --repo="$(luma_os_channel_repo "$LUMA_OS_CHANNEL")" "$update_from" / >/dev/null 2>&1 ||
    luma_os_die "--update-from $update_from is not a retained published release"
  previous=$update_from
fi
candidate_repo="$LUMA_OS_ROOT/ostree/candidate-repo"
iso=${LUMA_OS_GATE_ISO:-$LUMA_OS_ROOT/vm/media/Fedora-Silverblue-ostree-x86_64-44-1.7.iso}
[ -f "$iso" ] || luma_os_die "installer ISO is missing: $iso"

run_id="$(date -u +%Y%m%dT%H%M%SZ)"
gate_dir="$build_dir/gate/$run_id"
vm_dir="$LUMA_OS_ROOT/vm/gate-$build_id-$run_id"
install -d -m 0755 "$gate_dir" "$vm_dir"   # qemu must reach the disks in vm_dir
[ -n "$update_from" ] || ln -sfn "$run_id" "$build_dir/gate/latest"
log="$gate_dir/gate.log"
exec > >(tee -a "$log") 2>&1
luma_os_log "gate $run_id for $candidate ($LUMA_OS_VERSION) on $ref; previous ${previous:-none}"

exec 7>"$LUMA_OS_ROOT/locks/gate.lock"
flock -n 7 || luma_os_die 'another gate is running'

# The candidate is judged by the checks committed with its own source.
release_files="$gate_dir/release-checks"
luma_os_release_files "$LUMA_SOURCE_REVISION" "$release_files" "$build_dir"
printf '%s\n' "$LUMA_SOURCE_REVISION" >"$gate_dir/release-checks-revision"
printf '%s\n' "${LUMA_SOURCE_SNAPSHOT_SHA256:-}" >"$gate_dir/release-checks-snapshot-sha256"

results="$gate_dir/stages.jsonl"
: >"$results"
record() {
  # record STAGE RESULT DETAIL
  python3 -c 'import json, sys; print(json.dumps({"stage": sys.argv[1], "result": sys.argv[2], "detail": sys.argv[3], "at": sys.argv[4]}))' \
    "$1" "$2" "$3" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$results"
  luma_os_log "stage $1: $2 ($3)"
}

# Gate-only credentials for the disposable VMs.
# The key lives below /root/.ssh: confined ssh and ssh-keygen may only use
# ssh_home_t locations, not the unlabeled pipeline volume or /run.
key_dir="/root/.ssh/luma-os-gate-$run_id"
install -d -m 0700 "$key_dir"
ssh-keygen -q -t ed25519 -N '' -C "luma-os-gate-$run_id" -f "$key_dir/ssh-key"
release_key_b64=$(base64 -w0 "$LUMA_OS_KEYS/luma-os-release.gpg")

# Gate repository: a view of the candidate repository whose channel ref can be
# moved independently (hard links, no second copy of the content).
gate_repo="$vm_dir/repo"
ostree init --repo="$gate_repo" --mode=archive --collection-id="$LUMA_OS_COLLECTION_ID"
ostree pull-local --repo="$gate_repo" --untrusted "$candidate_repo" "$candidate" >/dev/null
[ -z "$previous" ] || ostree pull-local --repo="$gate_repo" --untrusted "$LUMA_OS_ROOT/publish/preview-repo" "$previous" >/dev/null 2>&1 ||
  ostree pull-local --repo="$gate_repo" --untrusted "$(luma_os_channel_repo "$channel")" "$previous" >/dev/null

luma_os_gpg_unlock
point_gate_ref() {
  ostree refs --repo="$gate_repo" --create="$ref" --force "$1" >/dev/null
  # Re-seed the passphrase before every signature: the agent's socket lives
  # under /run/user/0, which logind removes whenever the last root login ends,
  # taking the preset with it (20260917.7 and .8: "GPG Agent: No pinentry").
  luma_os_gpg_unlock
  ostree summary --repo="$gate_repo" --update \
    --gpg-sign="$(luma_os_gpg_fingerprint)" --gpg-homedir="$(luma_os_gpg_home)" >/dev/null
}

# HTTP server for the gate repository, on the host loopback only. VMs use
# passt user networking, which maps their default gateway to this host's
# loopback, so nothing is exposed on any real interface and no firewall
# change is needed.
http_port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
http_name="luma-os-gate-http-$run_id"
LUMA_OS_TOOLS_MOUNTS="$gate_repo" luma_os_tools true
luma_os_podman run --detach --rm --name "$http_name" --network host --security-opt label=disable \
  --volume "$gate_repo:/srv/repo:ro" "$(luma_os_tools_image)" \
  python3 -m http.server --bind 127.0.0.1 --directory /srv/repo "$http_port" >/dev/null
guest_gateway=$(ip -4 route show default | awk '{ print $3; exit }')
repo_url="http://$guest_gateway:$http_port"
for _ in $(seq 1 30); do
  curl --fail --silent "http://127.0.0.1:$http_port/config" >/dev/null && break
  sleep 1
done

domains=()
cleanup() {
  local status=$?
  set +e
  luma_os_gpg_lock
  # Keep the VMs' key only while their disks are kept for diagnosis.
  if [ "$keep_vms" -eq 0 ] && [ "$status" -eq 0 ]; then
    rm -rf "$key_dir"
  fi
  luma_os_podman stop --time 2 "$http_name" >/dev/null 2>&1
  for domain in "${domains[@]}"; do
    virsh destroy "$domain" >/dev/null 2>&1
    if [ "$keep_vms" -eq 0 ]; then
      virsh undefine "$domain" --nvram >/dev/null 2>&1
    fi
  done
  if [ "$keep_vms" -eq 0 ] && [ "$status" -eq 0 ]; then
    rm -rf "$vm_dir"
  else
    rm -rf "$gate_repo"
    luma_os_log "VM disks kept for diagnosis: $vm_dir"
  fi
  exit "$status"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# VM helpers
# ---------------------------------------------------------------------------

ssh_port_for() {
  python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

guest() {
  # guest DOMAIN COMMAND...
  local port
  port=$(cat "$vm_dir/$1.ssh-port")
  shift
  ssh -q -p "$port" -i "$key_dir/ssh-key" -o BatchMode=yes -o ConnectTimeout=10 \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
    root@127.0.0.1 "$@"
}

guest_copy() {
  # guest_copy DOMAIN LOCAL REMOTE
  local port
  port=$(cat "$vm_dir/$1.ssh-port")
  scp -q -P "$port" -i "$key_dir/ssh-key" -o BatchMode=yes -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "$2" "root@127.0.0.1:$3"
}

# gate_release_check DOMAIN STAGE NAME [ARGS...]: tests/os/gate/NAME.sh from
# the release's own source, run as root in the VM; one JSON line per check. A
# missing script or any failing check fails the stage.
gate_release_check() {
  local domain=$1 stage=$2 name=$3 log
  shift 3
  log="$gate_dir/$stage-$name.jsonl"
  if [ ! -f "$release_files/tests/os/gate/$name.sh" ]; then
    record "$stage-$name" fail "tests/os/gate/$name.sh is not in the release source"
    return 1
  fi
  # The signed-browser check invokes three source-owned sibling helpers.
  # Deliver them from the same admitted release tree, never from host drafts.
  if [ "$name" = default-browser ]; then
    local helper
    for helper in browser-opens.sh browser-opens.js browser-process-proof.py; do
      if [ ! -f "$release_files/tests/os/gate/$helper" ]; then
        record "$stage-$name" fail "tests/os/gate/$helper is not in the release source"
        return 1
      fi
      if ! guest_copy "$domain" "$release_files/tests/os/gate/$helper" "/var/tmp/$helper"; then
        record "$stage-$name" fail "could not deliver release helper $helper"
        return 1
      fi
    done
  fi
  guest_copy "$domain" "$release_files/tests/os/gate/$name.sh" "/var/tmp/luma-check-$name.sh"
  if guest "$domain" "bash /var/tmp/luma-check-$name.sh $*" >"$log" 2>&1 && ! grep -q '"result": "fail"' "$log"; then
    record "$stage-$name" pass "$(grep -c '"result": "pass"' "$log") checks"
    return 0
  fi
  record "$stage-$name" fail "failing: $(python3 -c 'import json,sys
names=[]
for line in open(sys.argv[1]):
    try: d=json.loads(line)
    except ValueError: continue
    if d.get("result") == "fail": names.append(d.get("check", "?"))
print(", ".join(names) or "script error")' "$log") (see $stage-$name.jsonl)"
  return 1
}

# gate_admin_prompt DOMAIN STAGE: the administrator prompt (GNOME Shell's
# polkit agent) must let an administrator through in a real logged-in session,
# and only with the right password. tests/os/gate/admin-prompt.sh logs a
# wheel member into GNOME through GDM; this host types the password into the
# prompt as key presses (virsh send-key) for: `pkexec true` (a wrong password
# first, which must be refused and asked again), Depot's system Flatpak
# install, and the owned gate USB drive (attached here) opened through UDisks. A
# missing script fails the stage: a release that cannot show its prompt works
# must not publish.
gate_type() {
  # gate_type DOMAIN TEXT: [a-z0-9] only, one key press each, then Enter.
  local domain=$1 text=$2 i c
  for ((i = 0; i < ${#text}; i++)); do
    c=${text:i:1}
    virsh send-key "$domain" "KEY_${c^^}" >/dev/null 2>&1 || return 1
    sleep 0.15
  done
  sleep 0.5
  virsh send-key "$domain" KEY_ENTER >/dev/null 2>&1
}
gate_admin_prompt() {
  local domain=$1 stage=$2 script=/var/tmp/luma-check-admin-prompt.sh log password wrong opened c result=0
  log="$gate_dir/$stage-admin-prompt.jsonl"
  : >"$log"
  if [ ! -f "$release_files/tests/os/gate/admin-prompt.sh" ]; then
    record "$stage-admin-prompt" fail 'tests/os/gate/admin-prompt.sh is not in the release source'
    return 1
  fi
  guest_copy "$domain" "$release_files/tests/os/gate/admin-prompt.sh" "$script"
  password="lg$(tr -dc 'a-z0-9' </dev/urandom | head -c 12)"
  wrong="wrong$(tr -dc 'a-z0-9' </dev/urandom | head -c 8)"
  if ! printf '%s\n' "$password" | guest "$domain" "bash $script setup" >>"$log" 2>&1; then
    record "$stage-admin-prompt" fail "no logged-in administrator session with GNOME Shell as its agent (see $stage-admin-prompt.jsonl)"
    return 1
  fi
  screenshot "$domain" "$stage-admin-session"
  for c in pkexec flatpak-system udisks-open; do
    case "$c" in
      flatpak-system)
        if ! guest "$domain" "bash $script flatpak-ref" >>"$log" 2>&1; then
          printf '{"check": "admin-flatpak-system", "result": "fail", "detail": "no Flathub Adwaita-dark theme ref to install (network or remote)"}\n' >>"$log"
          continue
        fi ;;
      udisks-open)
        truncate -s 64M "$vm_dir/$domain-usb.img"
        if ! python3 -c 'import libvirt, sys
conn = libvirt.open("qemu:///system")
domain = conn.lookupByName(sys.argv[1])
xml = "<disk type=\"file\" device=\"disk\"><driver name=\"qemu\" type=\"raw\"/><source file=\"%s\"/><target dev=\"sdz\" bus=\"usb\" removable=\"on\"/><serial>LUMAGATEUSB</serial></disk>" % sys.argv[2]
try:
    domain.attachDeviceFlags(xml, libvirt.VIR_DOMAIN_AFFECT_LIVE)
except libvirt.libvirtError:
    # Already plugged in (a repeated run in the same VM) is no failure.
    if "sdz" not in domain.XMLDesc():
        raise
    print("the gate USB drive was already attached")' "$domain" "$vm_dir/$domain-usb.img" >>"$gate_dir/$stage-admin-prompt-usb.log" 2>&1; then
          printf '{"check": "admin-udisks-open", "result": "fail", "detail": "could not attach the gate USB drive (see %s-admin-prompt-usb.log)"}\n' "$stage" >>"$log"
          continue
        fi
        sleep 5 ;;
    esac
    opened=$(guest "$domain" "bash $script start $c" 2>>"$log" | tail -n 1)
    case "$c:$opened" in
      pkexec:prompt|flatpak-system:prompt|udisks-open:prompt)
        sleep 3
        screenshot "$domain" "$stage-admin-prompt-$c"
        if [ "$c" = pkexec ]; then
          gate_type "$domain" "$wrong"
          sleep 4
          guest "$domain" "bash $script pending $c" >>"$log" 2>&1
          sleep 2
        fi
        gate_type "$domain" "$password" ;;
      flatpak-system:done|udisks-open:done)
        : ;; # allowed by policy without a prompt; finish judges the result
      *)
        printf '{"check": "admin-%s-prompt", "result": "fail", "detail": "the prompt never started a password conversation (%s)"}\n' "$c" "${opened:-no answer}" >>"$log"
        guest "$domain" "bash $script finish $c 5" >/dev/null 2>&1
        continue ;;
    esac
    guest "$domain" "bash $script finish $c" >>"$log" 2>&1
  done
  screenshot "$domain" "$stage-admin-prompt-end"
  guest "$domain" 'journalctl -b --no-pager _COMM=polkitd + _COMM=gnome-shell + SYSLOG_IDENTIFIER=polkit-agent-helper-1' >"$gate_dir/$stage-admin-prompt-journal.log" 2>&1 || true
  rm -f "$vm_dir/$domain-usb.img"
  if grep -q '"result": "pass"' "$log" && ! grep -q '"result": "fail"' "$log" &&
     [ "$(grep -c '"result": "pass"' "$log")" -ge 6 ]; then
    record "$stage-admin-prompt" pass "$(grep -c '"result": "pass"' "$log") checks: pkexec with a wrong then the right password, Depot system Flatpak install, owned gate USB UDisks open, through the real prompt"
  else
    record "$stage-admin-prompt" fail "failing: $(python3 -c 'import json,sys
names=[]
for line in open(sys.argv[1]):
    try: d=json.loads(line)
    except ValueError: continue
    if d.get("result") == "fail": names.append(d.get("check", "?"))
print(", ".join(names) or "too few checks ran")' "$log") (see $stage-admin-prompt.jsonl)"
    result=1
  fi
  return "$result"
}

wait_ssh() {
  local domain=$1 timeout=${2:-900} deadline
  deadline=$((SECONDS + timeout))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if guest "$domain" true 2>/dev/null; then
      return 0
    fi
    sleep 5
  done
  return 1
}

wait_shutoff() {
  local domain=$1 timeout=${2:-600} deadline
  deadline=$((SECONDS + timeout))
  while [ "$SECONDS" -lt "$deadline" ]; do
    [ "$(virsh domstate "$domain" 2>/dev/null)" = 'shut off' ] && return 0
    sleep 5
  done
  return 1
}

# Wait until greenboot has finished judging the current boot. Prints
# "healthy", "unhealthy" or "unknown".
wait_greenboot() {
  local domain=$1 timeout=${2:-900} deadline state
  deadline=$((SECONDS + timeout))
  while [ "$SECONDS" -lt "$deadline" ]; do
    state=$(guest "$domain" 'systemctl show -p ActiveState --value greenboot-healthcheck.service; systemctl show -p Result --value greenboot-healthcheck.service' 2>/dev/null | tr '\n' ' ' || true)
    case "$state" in
      'active success '*) echo healthy; return 0 ;;
      'failed '*) echo unhealthy; return 0 ;;
    esac
    sleep 5
  done
  echo unknown
}

booted_commit() {
  guest "$1" "rpm-ostree status --json" |
    python3 -c 'import json,sys; print([d for d in json.load(sys.stdin)["deployments"] if d.get("booted")][0]["checksum"])'
}

screenshot() {
  local domain=$1 name=$2
  # libvirt chooses the screenshot format (PNG with libvirt 12 and QEMU 10);
  # captured through its Python binding (virsh may not write here under systemd).
  python3 "$luma_os_repo_root/scripts/os/lib/screen_image.py" capture "$domain" "$gate_dir/$name.img" >/dev/null 2>&1 &&
    python3 "$luma_os_repo_root/scripts/os/lib/screen_image.py" to-png "$gate_dir/$name.img" "$gate_dir/$name.png" &&
    rm -f "$gate_dir/$name.img" || true
}

# install_vm NAME COMMIT: Anaconda installs COMMIT (the gate ref points at it).
# Sets installed_domain; runs in the current shell so cleanup knows the domain.
installed_domain=
install_vm() {
  local name=$1 commit=$2 accounts=${3:-none} domain port firstboot='firstboot --disable'
  [ "$accounts" = none ] || firstboot='# first-boot setup stays on (no-account stage)'
  domain="luma-os-gate-$name-$run_id"
  port=$(ssh_port_for)
  printf '%s\n' "$port" >"$vm_dir/$domain.ssh-port"
  point_gate_ref "$commit"
  sed -e "s#@SSH_PUBLIC_KEY@#$(cat "$key_dir/ssh-key.pub")#" \
      -e "s#@RELEASE_KEY_BASE64@#$release_key_b64#" \
      -e "s#@STATEROOT@#$LUMA_OS_STATEROOT#g" \
      -e "s#@REMOTE@#$LUMA_OS_REMOTE#g" \
      -e "s#@REPO_URL@#$repo_url#g" \
      -e "s#@REF@#$ref#g" \
      -e "s#@CHANNEL@#$channel#g" \
      -e "s|@FIRSTBOOT@|$firstboot|" \
      "$luma_os_repo_root/tests/os/gate/luma-gate.ks.in" >"$vm_dir/luma-gate.ks"
  qemu-img create -q -f qcow2 "$vm_dir/$domain.qcow2" 48G
  domains+=("$domain")
  luma_os_log "installing $commit into $domain"
  local started=$SECONDS
  virt-install --connect qemu:///system \
    --name "$domain" \
    --memory 6144 --vcpus 4 --cpu host-passthrough \
    --machine q35 --boot uefi \
    --disk "path=$vm_dir/$domain.qcow2,format=qcow2,bus=virtio,cache=unsafe,discard=unmap" \
    --location "$iso,kernel=images/pxeboot/vmlinuz,initrd=images/pxeboot/initrd.img" \
    --initrd-inject "$vm_dir/luma-gate.ks" \
    --extra-args "inst.ks=file:/luma-gate.ks inst.text console=ttyS0 inst.notmux" \
    --network "passt,portForward0.proto=tcp,portForward0.range0.start=$port,portForward0.range0.to=22" \
    --graphics vnc,listen=127.0.0.1 --video virtio \
    --rng /dev/urandom \
    --channel unix,target.type=virtio,target.name=org.qemu.guest_agent.0 \
    --serial "file,path=/var/log/libvirt/qemu/$domain-serial.log" \
    --os-variant fedora-unknown \
    --noreboot --noautoconsole --wait 60 >>"$gate_dir/virt-install-$name.log" 2>&1 || {
      tail -n 30 "/var/log/libvirt/qemu/$domain-serial.log" 2>/dev/null >&2
      return 1
    }
  wait_shutoff "$domain" 300 || return 1
  printf '%s\n' "$((SECONDS - started))" >"$gate_dir/$name-install-seconds"
  # libvirt may only write its logs where SELinux labels them (virt_log_t).
  cp "/var/log/libvirt/qemu/$domain-serial.log" "$gate_dir/$name-install-serial.log" 2>/dev/null || true
  virsh start "$domain" >/dev/null
  wait_ssh "$domain" 900 || return 1
  installed_domain=$domain
}

# A temporary wheel member with a NOPASSWD rule runs `sudo -n true`; both are
# removed again whatever the result.
sudo_probe='set -u; useradd -m -G wheel luma-gate-sudo && printf "luma-gate-sudo ALL=(ALL) NOPASSWD: ALL\n" >/etc/sudoers.d/90-luma-gate && chmod 0440 /etc/sudoers.d/90-luma-gate; ls -l /usr/bin/sudo; runuser -u luma-gate-sudo -- sudo -n true; rc=$?; rm -f /etc/sudoers.d/90-luma-gate; userdel -r luma-gate-sudo >/dev/null 2>&1; exit $rc'

# evaluate DOMAIN STAGE EXPECTED_COMMIT: the checks every evaluated boot gets.
evaluate() {
  local domain=$1 stage=$2 expected=$3 verdict failures=0
  verdict=$(wait_greenboot "$domain" 900)
  guest "$domain" 'journalctl -b -u greenboot-healthcheck.service --no-pager' >"$gate_dir/$stage-greenboot.log" 2>&1 || true
  guest "$domain" 'rpm-ostree status --json' >"$gate_dir/$stage-rpm-ostree-status.json" 2>/dev/null || true
  screenshot "$domain" "$stage"
  if [ "$verdict" != healthy ]; then
    record "$stage-greenboot" fail "greenboot verdict: $verdict"
    failures=$((failures + 1))
  else
    record "$stage-greenboot" pass 'greenboot judged the boot healthy'
  fi
  local actual
  actual=$(booted_commit "$domain" || true)
  if [ "$actual" != "$expected" ]; then
    record "$stage-booted-commit" fail "booted ${actual:-nothing}, expected $expected"
    failures=$((failures + 1))
  fi
  guest_copy "$domain" "$release_files/tests/os/image-contract.sh" /var/tmp/luma-image-contract.sh
  guest_copy "$domain" "$release_files/config/os/release.env" /var/tmp/luma-release.env
  local layout_args=()
  # The update stage's machine was installed from the previous release and
  # keeps that release's remote file (OSTree's /etc merge); the fresh and
  # no-account stages require the mirror-list layout.
  if [ "$stage" = update ] &&
     ! guest "$domain" "grep -Fxq 'url=mirrorlist=file:///etc/luma/update-mirrorlist' /etc/ostree/remotes.d/luma.conf"; then
    layout_args=(--updated-from-url-layout)
  fi
  # The update was staged by the previous release's agent; before
  # 1.0.0-1.luma.4 it did not apply kargs.d arguments.
  if [ "$stage" = update ] && [ "${previous_agent_applies_kargs:-yes}" = no ]; then
    layout_args+=(--updated-by-agent-without-kargs)
  fi
  if guest "$domain" bash /var/tmp/luma-image-contract.sh --release-env /var/tmp/luma-release.env \
      --expect-commit "$expected" --channel "$channel" "${layout_args[@]}" >"$gate_dir/$stage-image-contract.log" 2>&1; then
    record "$stage-image-contract" pass "$(grep -c '^PASS' "$gate_dir/$stage-image-contract.log") checks"
  else
    record "$stage-image-contract" fail "$(grep -c '^FAIL' "$gate_dir/$stage-image-contract.log") failing checks"
    failures=$((failures + 1))
  fi
  guest_copy "$domain" "$release_files/tests/smoke/desktop.sh" /var/tmp/luma-desktop-smoke.sh
  guest_copy "$domain" "$release_files/config/desktop/inputs.env" /var/tmp/luma-desktop-inputs.env
  if guest "$domain" bash /var/tmp/luma-desktop-smoke.sh >"$gate_dir/$stage-desktop-smoke.log" 2>&1; then
    record "$stage-desktop-smoke" pass "$(grep -c '^PASS' "$gate_dir/$stage-desktop-smoke.log") checks"
  else
    record "$stage-desktop-smoke" fail "$(grep -c '^FAIL' "$gate_dir/$stage-desktop-smoke.log") failing checks"
    failures=$((failures + 1))
  fi
  # Signed applications update independently of their protected native
  # engines. Exercise Depot's actual system Flatpak Remove and named-source
  # Restore, retaining all other heads, user data and the durable seed marker.
  if [ "$stage" = fresh ]; then
    guest_copy "$domain" "$release_files/tests/os/gate/app-remove-restore.py" /var/tmp/luma-app-remove-restore.py
    if guest "$domain" python3 -B /var/tmp/luma-app-remove-restore.py --pre-account >"$gate_dir/$stage-remove-restore.log" 2>&1; then
      record "$stage-remove-restore" pass "$(tail -n 1 "$gate_dir/$stage-remove-restore.log")"
    else
      record "$stage-remove-restore" fail 'signed app removal or authenticated named-source restoration failed (see remove-restore.log)'
      failures=$((failures + 1))
    fi
  fi
  # Out-of-box release checks on the fresh install: Viola is the default web
  # browser (default-browser.sh) and no configured repository can replace a
  # package Luma owns (fedora-cannot-replace-luma.sh).
  if [ "$stage" = fresh ]; then
    gate_release_check "$domain" "$stage" default-browser || failures=$((failures + 1))
    # Viewer opens PDFs and images; Document Viewer is not part of Luma.
    gate_release_check "$domain" "$stage" default-pdf-viewer || failures=$((failures + 1))
    gate_release_check "$domain" "$stage" luma-identity || failures=$((failures + 1))
    gate_release_check "$domain" "$stage" fedora-cannot-replace-luma || failures=$((failures + 1))
    # Depot opens, every time, the way a person opens it: a real Shell activates
    # its desktop entry over the first minutes of a session with fwupd running,
    # and a launch that puts no window on screen fails the gate (depot-opens.sh).
    gate_release_check "$domain" "$stage" depot-opens || failures=$((failures + 1))
  fi
  # Privilege escalation works for an administrator: sudo as a wheel member.
  if guest "$domain" "$sudo_probe" >"$gate_dir/$stage-sudo.log" 2>&1; then
    record "$stage-sudo" pass 'sudo -n true succeeds for a wheel member'
  else
    record "$stage-sudo" fail 'sudo does not work for a wheel member (see sudo.log)'
    failures=$((failures + 1))
  fi
  return "$failures"
}

# The installer-owned boot policy (GRUB theme and menu), applied the way the
# canonical composition applied it after the first boot, then a reboot so the
# evaluated boot runs with it.
apply_installer_boot_policy() {
  local domain=$1
  guest_copy "$domain" "$release_files/config/boot/apply-grub-policy.sh" /var/tmp/apply-grub-policy.sh
  guest "$domain" 'bash /var/tmp/apply-grub-policy.sh && rm -f /var/tmp/apply-grub-policy.sh'
}

# gate_hidden_menu DOMAIN STAGE: the updated machine's grub.cfg carries the
# hidden-menu piece before its BLS menu, the rest of the file is what it was
# before the update (the helper's backup), the helper is idempotent and its
# removal is exact, and Esc during the hidden countdown opens GRUB's menu.
gate_hidden_menu() {
  local domain=$1 stage=$2 ok=1 domain_xml
  if guest "$domain" 'set -e
      cfg=/boot/grub2/grub.cfg
      grep -q "^### BEGIN 09_luma_hidden_menu.cfg ###$" $cfg
      python3 -c "import re,sys; t=open(sys.argv[1]).read(); h=t.find(\"### BEGIN 09_luma_hidden_menu.cfg ###\"); b=re.search(r\"(?m)^(blscfg|source .prefix/luma.cfg)$\", t); sys.exit(not (h >= 0 and b and h < b.start()))" $cfg
      journalctl -b -o cat --no-pager -t luma-boot-hidden-menu -t luma-updated | grep -E "luma-boot-hidden-menu: " || true
      cp $cfg /run/luma-gate-grub.cfg
      /usr/libexec/luma-boot-hidden-menu --grub-cfg /run/luma-gate-grub.cfg | grep -q "is current"
      /usr/libexec/luma-boot-hidden-menu --grub-cfg /run/luma-gate-grub.cfg --remove >/dev/null
      if [ -e $cfg.pre-luma-hidden-menu ]; then cmp /run/luma-gate-grub.cfg $cfg.pre-luma-hidden-menu; fi
      rm -f /run/luma-gate-grub.cfg' >"$gate_dir/$stage-hidden-menu.log" 2>&1; then
    record "$stage-hidden-menu" pass "grub.cfg carries the hidden-menu piece before the BLS menu; idempotent; removal restores the pre-update file"
  else
    record "$stage-hidden-menu" fail "grub.cfg lacks the hidden-menu piece or the helper changed more than its piece (see $stage-hidden-menu.log)"
    ok=0
  fi
  # The hidden countdown can still be interrupted to reach GRUB's menu.
  # GRUB treats Esc, F4 and F8 alike during a hidden timeout (one key check in
  # grub-core/normal/menu.c), and the piece's promise is that they still work.
  # The gate presses F8, not Esc: these VMs boot OVMF, which takes Esc for its
  # own setup screen, so holding Esc from power-on can stop in the firmware and
  # pass on the wrong menu. F8 is pressed on its own fast loop, because GRUB's
  # countdown lasts one second and each screenshot takes about as long to read.
  # A menu is a lit screen that stays unchanged for twenty seconds while the
  # system does not come up, which no firmware logo, splash or login screen
  # does. (An upper bound on lit pixels wrongly rejected GRUB's themed menu,
  # whose grey panel fills most of the screen: 20260917.2's own screenshot
  # shows the menu open.) Screenshots are read with
  # scripts/os/lib/screen_image.py: libvirt picks their format (PNG here).
  local shot="$gate_dir/$stage-hidden-menu-interrupt" stop="$vm_dir/$stage-hidden-menu-keys.stop"
  local deadline=$((SECONDS + 150)) seen=0 fraction keys_pid
  rm -f "$stop"
  guest "$domain" 'systemctl reboot' >/dev/null 2>&1 || true
  (
    until [ -e "$stop" ] || [ "$SECONDS" -ge "$deadline" ]; do
      virsh send-key "$domain" KEY_F8 >/dev/null 2>&1 || true
      sleep 0.25
    done
  ) &
  keys_pid=$!
  sleep 5
  while [ "$SECONDS" -lt "$deadline" ]; do
    if python3 "$luma_os_repo_root/scripts/os/lib/screen_image.py" capture "$domain" "$shot.img" >/dev/null 2>&1 &&
       fraction=$(python3 "$luma_os_repo_root/scripts/os/lib/screen_image.py" lit-fraction "$shot.img" 2>/dev/null) &&
       python3 -c 'import sys; sys.exit(0 if float(sys.argv[1]) > 0.02 else 1)' "$fraction"; then
      cp "$shot.img" "$shot-first.img"
      sleep 20
      if python3 "$luma_os_repo_root/scripts/os/lib/screen_image.py" capture "$domain" "$shot.img" >/dev/null 2>&1 &&
         python3 "$luma_os_repo_root/scripts/os/lib/screen_image.py" same "$shot.img" "$shot-first.img" &&
         ! guest "$domain" true 2>/dev/null; then
        seen=1
        break
      fi
    fi
    sleep 0.5
  done
  touch "$stop"
  wait "$keys_pid" 2>/dev/null || true
  rm -f "$stop" "$shot-first.img"
  [ -f "$shot.img" ] &&
    python3 "$luma_os_repo_root/scripts/os/lib/screen_image.py" to-png "$shot.img" "$shot.png" 2>/dev/null &&
    rm -f "$shot.img" || true
  if [ "$seen" -eq 1 ]; then
    record "$stage-hidden-menu-interrupt" pass "F8 during the hidden countdown opened GRUB's menu, which then waited (see $stage-hidden-menu-interrupt.png)"
    virsh send-key "$domain" KEY_ENTER >/dev/null 2>&1 || true
  else
    record "$stage-hidden-menu-interrupt" fail "GRUB's menu did not open with F8 during the hidden countdown (last screen: $stage-hidden-menu-interrupt.png)"
    ok=0
    # A menu that opened but was not recognised waits forever (an interrupted
    # countdown has no timeout), and every later stage would fail waiting for
    # the machine. Enter boots its default entry; it does nothing harmful on
    # a machine already past GRUB.
    virsh send-key "$domain" KEY_ENTER >/dev/null 2>&1 || true
  fi
  wait_ssh "$domain" 900 || ok=0
  [ "$ok" -eq 1 ]
}

reboot_vm() {
  local domain=$1
  guest "$domain" 'systemctl reboot' >/dev/null 2>&1 || true
  sleep 15
  wait_ssh "$domain" 900
}

overall=pass

# ---------------------------------------------------------------------------
# Stage: fresh install of the candidate
# ---------------------------------------------------------------------------
if [ "$skip_fresh" -eq 1 ]; then
  record fresh skipped 'skipped by operator (--skip-fresh); publication refuses this gate'
elif install_vm fresh "$candidate"; then
  fresh=$installed_domain
  record fresh-install pass "installed in $(cat "$gate_dir/fresh-install-seconds")s over HTTP with GPG verification"
  first_boot=$(wait_greenboot "$fresh" 900)
  guest "$fresh" 'journalctl -b -u greenboot-healthcheck.service --no-pager' >"$gate_dir/fresh-first-boot-greenboot.log" 2>&1 || true
  [ "$first_boot" = healthy ] &&
    record fresh-first-boot pass 'greenboot judged the first boot healthy' ||
    { record fresh-first-boot fail "greenboot verdict: $first_boot"; overall=fail; }
  apply_installer_boot_policy "$fresh" && reboot_vm "$fresh" ||
    { record fresh-boot-policy fail 'installer boot policy or reboot failed'; overall=fail; }
  evaluate "$fresh" fresh "$candidate" || overall=fail
  # Flicker-free boot (luma-boot-theme 1.luma.14+): a fresh bootupd install
  # must carry the hidden-menu piece in grub.cfg, or GRUB's text menu wipes the
  # firmware logo for a second on every boot. Fresh installs only: updated
  # computers keep the grub.cfg they were installed with.
  if guest "$fresh" 'ls /usr/lib/bootupd/grub2-static/configs.d/*_luma_hidden_menu.cfg' >/dev/null 2>&1; then
    if guest "$fresh" 'grep -q "^### BEGIN [0-9]*_luma_hidden_menu.cfg ###$" /boot/grub2/grub.cfg' >/dev/null 2>&1; then
      record fresh-hidden-boot-menu pass 'grub.cfg on the fresh install carries the hidden-menu piece'
    else
      guest "$fresh" 'grep -n "timeout" /boot/grub2/grub.cfg' >"$gate_dir/fresh-hidden-boot-menu.log" 2>&1 || true
      record fresh-hidden-boot-menu fail 'grub.cfg on the fresh install lacks the hidden-menu piece (see fresh-hidden-boot-menu.log)'
      overall=fail
    fi
  fi
  virsh shutdown "$fresh" >/dev/null 2>&1 || true
  wait_shutoff "$fresh" 180 || virsh destroy "$fresh" >/dev/null 2>&1 || true
else
  record fresh-install fail 'Anaconda install of the candidate failed'
  overall=fail
fi

# ---------------------------------------------------------------------------
# Stage: an install that creates no account (first-boot setup)
# ---------------------------------------------------------------------------
no_account_result=pass
if [ "$skip_fresh" -eq 1 ]; then
  no_account_result=skipped
  record no-account skipped 'skipped with the fresh stage (--skip-fresh)'
elif install_vm no-account "$candidate" first-boot-setup; then
  na=$installed_domain
  record no-account-install pass "installed in $(cat "$gate_dir/no-account-install-seconds")s without an account"
  verdict=$(wait_greenboot "$na" 900)
  guest "$na" 'journalctl -b -u greenboot-healthcheck.service --no-pager' >"$gate_dir/no-account-greenboot.log" 2>&1 || true
  if [ "$verdict" = healthy ]; then
    record no-account-greenboot pass 'greenboot judged the first boot healthy'
  else
    record no-account-greenboot fail "greenboot verdict: $verdict"; no_account_result=fail
  fi
  # The image's own checks, enforced as on a trial boot.
  if guest "$na" 'LUMA_GREENBOOT_FORCE_TRIAL=1 /usr/lib/greenboot/check/required.d/10-luma-display-manager.sh && LUMA_GREENBOOT_FORCE_TRIAL=1 /usr/lib/greenboot/check/required.d/20-luma-shell-responsive.sh' \
      >"$gate_dir/no-account-display-checks.log" 2>&1; then
    record no-account-display pass "$(grep '^Luma:' "$gate_dir/no-account-display-checks.log" | tr '\n' ' ')"
  else
    record no-account-display fail 'the display manager or GNOME Shell checks failed with no account (see no-account-display-checks.log)'
    no_account_result=fail
  fi
  session=$(guest "$na" /usr/libexec/luma-os/graphical-session 2>/dev/null || true)
  if [ "$(awk '{ print $2 }' <<<"$session")" = greeter ] && [ "$(awk '{ print $4 }' <<<"$session")" = gnome-initial-setup ]; then
    record no-account-setup-session pass "first-boot setup session: $session"
  else
    record no-account-setup-session fail "expected the gnome-initial-setup greeter session, found: ${session:-none}"
    no_account_result=fail
  fi
  failed_units=$(guest "$na" 'systemctl list-units --state=failed --no-legend --plain' 2>/dev/null || echo 'unknown')
  if [ -z "$failed_units" ]; then
    record no-account-units pass 'no failed system units'
  else
    record no-account-units fail "failed units: $(tr '\n' ' ' <<<"$failed_units")"
    no_account_result=fail
  fi
  # Luma Cast (ADR-034), when the release carries it: not started by the
  # first-boot session, usable there on demand through D-Bus activation, with
  # no SELinux denials and no Shell JavaScript errors from Cast.
  if guest "$na" 'rpm -q luma-cast' >/dev/null 2>&1; then
    cast_user=$(awk '{ print $4 }' <<<"$session")
    if guest "$na" "u='$cast_user'; uid=\$(id -u \"\$u\") || exit 1
      before=\$(systemctl --user -M \"\$u@\" is-active luma-cast.service 2>/dev/null)
      echo \"before: \$before\"
      [ \"\$before\" != active ] || exit 1
      out=\$(runuser -u \"\$u\" -- env XDG_RUNTIME_DIR=/run/user/\$uid DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/\$uid/bus luma-cast list --wait 3 2>&1); rc=\$?
      printf 'luma-cast list (exit %s):\n%s\n' \"\$rc\" \"\$out\"
      [ \"\$rc\" -eq 0 ] || { [ \"\$rc\" -eq 1 ] && [ \"\$out\" = 'No screens found.' ]; } || exit 1
      after=\$(systemctl --user -M \"\$u@\" is-active luma-cast.service 2>/dev/null)
      echo \"after: \$after\"
      [ \"\$after\" = active ]" >"$gate_dir/no-account-cast.log" 2>&1; then
      record no-account-cast pass "luma-cast was not running in the first-boot session, started on demand for luma-cast list and answered"
    else
      record no-account-cast fail 'luma-cast did not stay on demand or luma-cast list failed with no account (see no-account-cast.log)'
      no_account_result=fail
    fi
    if guest "$na" "! journalctl -b -q --no-pager _TRANSPORT=audit | grep -F 'avc:  denied' | grep -Eq 'comm=\"luma-cast|exe=\"/usr/libexec/luma-cast'" >/dev/null 2>&1; then
      record no-account-cast-selinux pass 'no SELinux denials for luma-cast'
    else
      guest "$na" "journalctl -b -q --no-pager _TRANSPORT=audit | grep -F 'avc:  denied' | grep -E 'luma-cast'" >>"$gate_dir/no-account-cast.log" 2>&1 || true
      record no-account-cast-selinux fail 'SELinux denied luma-cast (see no-account-cast.log)'
      no_account_result=fail
    fi
    if guest "$na" "! journalctl -b -q --no-pager -o cat _COMM=gnome-shell | grep -E 'JS ERROR|TypeError|ReferenceError|SyntaxError|Gjs-CRITICAL' -A6 | grep -Eq 'lumaCast(Dialogs|Sheet)?\\.js|status/cast\\.js'" >/dev/null 2>&1; then
      record no-account-cast-shell pass 'GNOME Shell logged no JavaScript errors from Cast'
    else
      record no-account-cast-shell fail 'GNOME Shell logged JavaScript errors from Cast (see no-account-journal.log)'
      no_account_result=fail
    fi
  fi
  # Workspace overview keys (Shell 0128, ADR-037): Super+A, Super+Tab and
  # Shift+Super+Tab are real key presses into the VM (virsh send-key), then
  # Escape. GNOME Shell must log no JavaScript error from the moment the keys
  # are sent and must still answer on its bus. The first-boot setup session
  # has no overview (hasOverview=false), so this proves the new bindings are
  # handled safely in the session every new computer starts in; it does not
  # prove the overview opens, which needs a logged-in user session.
  if guest "$na" "grep -rqs 'toggle-workspace-overview' /usr/share/glib-2.0/schemas/" >/dev/null 2>&1; then
    keys_since=$(guest "$na" 'date +%s' 2>/dev/null | tr -d '\r')
    for combo in "KEY_LEFTMETA KEY_A" "KEY_ESC" "KEY_LEFTMETA KEY_TAB" "KEY_ESC" \
                 "KEY_LEFTSHIFT KEY_LEFTMETA KEY_TAB" "KEY_ESC"; do
      # shellcheck disable=SC2086
      virsh send-key "$na" $combo >/dev/null 2>&1 || true
      sleep 2
    done
    sleep 3
    guest "$na" "journalctl -b -q --no-pager -o cat _COMM=gnome-shell --since @$keys_since | grep -E 'JS ERROR|TypeError|ReferenceError|SyntaxError|Gjs-CRITICAL' -A6" \
      >"$gate_dir/no-account-overview-keys.log" 2>&1 || true
    if [ -n "$keys_since" ] && [ ! -s "$gate_dir/no-account-overview-keys.log" ] &&
       guest "$na" 'LUMA_GREENBOOT_FORCE_TRIAL=1 /usr/lib/greenboot/check/required.d/20-luma-shell-responsive.sh' >>"$gate_dir/no-account-overview-keys.log" 2>&1; then
      record no-account-overview-keys pass 'Super+A, Super+Tab and Shift+Super+Tab reached the first-boot session with no Shell JavaScript error and the Shell still answering'
    else
      record no-account-overview-keys fail 'Shell JavaScript errors or an unresponsive Shell after the workspace overview keys (see no-account-overview-keys.log)'
      no_account_result=fail
    fi
  fi
  # Viola and Charlie (owner decision 2026-09-16, shipped from outside this
  # repository): each must start as an unprivileged throwaway user without an
  # error, and neither may cause an SELinux denial. Viola's rendering, default
  # browser and launch checks are tests/os/gate/default-browser.sh (run here
  # too, in the first-boot session's machine); Charlie's launcher only
  # dispatches to charlie_luma.application or charlie_luma.mail_agent, so both
  # entry modules must import cleanly.
  if guest "$na" 'rpm -q viola-browser-stable || rpm -q luma-charlie' >/dev/null 2>&1; then
    apps_since=$(guest "$na" 'date +%s' 2>/dev/null | tr -d '\r')
    guest "$na" 'id luma-gate-apps >/dev/null 2>&1 || useradd -m luma-gate-apps' >/dev/null 2>&1 || true
    : >"$gate_dir/no-account-apps.log"
    if guest "$na" 'rpm -q viola-browser-stable' >/dev/null 2>&1; then
      gate_release_check "$na" no-account default-browser || no_account_result=fail
      gate_release_check "$na" no-account luma-identity || no_account_result=fail
    fi
    if guest "$na" 'rpm -q luma-charlie' >/dev/null 2>&1; then
      if guest "$na" "runuser -u luma-gate-apps -- env HOME=/home/luma-gate-apps timeout 60 python3 -c 'import charlie_luma.mail_agent, charlie_luma.application; print(\"luma-gate-charlie\")' 2>&1 | tee /dev/stderr | grep -q '^luma-gate-charlie$'" >>"$gate_dir/no-account-apps.log" 2>&1; then
        record no-account-charlie pass "Charlie's application and mail agent entry modules load as an unprivileged user"
      else
        record no-account-charlie fail "Charlie's entry modules did not load (see no-account-apps.log)"
        no_account_result=fail
      fi
    fi
    if guest "$na" "! journalctl -q --no-pager _TRANSPORT=audit --since @$apps_since | grep -F 'avc:  denied'" >/dev/null 2>&1; then
      record no-account-apps-selinux pass 'no SELinux denials while Viola and Charlie started'
    else
      guest "$na" "journalctl -q --no-pager _TRANSPORT=audit --since @$apps_since | grep -F 'avc:  denied'" >>"$gate_dir/no-account-apps.log" 2>&1 || true
      record no-account-apps-selinux fail 'SELinux denials while Viola and Charlie started (see no-account-apps.log)'
      no_account_result=fail
    fi
  fi
  # Memory guard (luma-vitals-memory-guard), when the release carries it: a
  # synthetic runaway in the session's app.slice must be ended by
  # systemd-oomd while GNOME Shell, the display manager and every system
  # service survive (scripts/os/lib/gate-memory-guard.sh).
  if guest "$na" 'rpm -q luma-vitals-memory-guard' >/dev/null 2>&1; then
    guard_user=$(awk '{ print $4 }' <<<"$session")
    guest_copy "$na" "$luma_os_repo_root/scripts/os/lib/gate-memory-guard.sh" /var/tmp/luma-gate-memory-guard.sh
    if guest "$na" "bash /var/tmp/luma-gate-memory-guard.sh '$guard_user'" >"$gate_dir/no-account-memory-guard.log" 2>&1; then
      record no-account-memory-guard pass "$(grep '^PASS' "$gate_dir/no-account-memory-guard.log" | head -n 1 | cut -c6-)"
    else
      record no-account-memory-guard fail "$(grep '^FAIL' "$gate_dir/no-account-memory-guard.log" | head -n 1 | cut -c6-) (see no-account-memory-guard.log)"
      no_account_result=fail
    fi
  fi
  screenshot "$na" no-account
  guest "$na" 'journalctl -b --no-pager' >"$gate_dir/no-account-journal.log" 2>&1 || true
  # Last in this VM: it logs a person in, which ends the first-boot session.
  gate_admin_prompt "$na" no-account || no_account_result=fail
  virsh destroy "$na" >/dev/null 2>&1 || true
  if [ "$keep_vms" -eq 0 ]; then
    virsh undefine "$na" --nvram >/dev/null 2>&1 || true
    rm -f "$vm_dir/$na.qcow2"
  fi
else
  record no-account-install fail 'Anaconda install without an account failed'
  no_account_result=fail
fi
[ "$no_account_result" != fail ] || overall=fail

# ---------------------------------------------------------------------------
# luma-update: when the release carries the update agent, updates and the
# automatic rollback go through it exactly as on a device: a signed graph
# names the release, the agent stages it, the restart finishes it, and the
# agent records what became of it. Gate-only rig settings on the VM: the
# graph and events URLs point at this gate's loopback server (plain HTTP is
# allowed only by allow_insecure_urls), a throwaway graph key is trusted in
# /etc/luma/update-graph-keys.d, and the check timer is stopped for this boot (it stays enabled) so the
# gate decides when checks happen.
# ---------------------------------------------------------------------------
graph_key_dir="$vm_dir/graph-key"
gate_graph() {
  # gate_graph COMMIT=VERSION... : sign and serve the channel graph.
  local generated entries="" item
  generated=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  for item in "$@"; do entries+="${item},"; done
  install -d -m 0755 "$gate_repo/luma-gate-graph"
  python3 - "$gate_repo/luma-gate-graph/$channel.json" "$channel" "$LUMA_OS_ARCH" "$generated" "${entries%,}" <<'PY'
import json, sys
path, channel, arch, generated, entries = sys.argv[1:6]
releases = []
for item in entries.split(","):
    commit, version = item.split("=", 1)
    releases.append({
        "version": version, "commit": commit, "released_at": "2026-01-01T00:00:00Z",
        "rollout": {"start_at": "2026-01-01T00:00:00Z", "start_percentage": 1.0, "duration_minutes": 0},
        "paused": False, "deadend": False, "deadend_reason": None, "barrier": False,
        "importance": "normal", "notes_url": None, "summary": "Luma gate release",
        "download_bytes_estimate": 0})
json.dump({"schema_version": 1, "channel": channel, "arch": arch, "generated_at": generated,
           "releases": releases}, open(path, "w"), indent=2)
PY
  LUMA_OS_TOOLS_MOUNTS="$graph_key_dir $gate_repo" luma_os_tools \
    minisign -S -s "$graph_key_dir/gate.key" -m "$gate_repo/luma-gate-graph/$channel.json" \
    -t "luma-gate-graph channel=$channel generated_at=$generated" >/dev/null
  sleep 1   # graphs must be strictly newer than the last one accepted
}

agent_setup() {
  local domain=$1
  guest "$domain" 'rpm -q luma-update' >/dev/null 2>&1 || return 1
  if [ ! -s "$graph_key_dir/gate.pub" ]; then
    install -d -m 0700 "$graph_key_dir"
    LUMA_OS_TOOLS_MOUNTS="$graph_key_dir" luma_os_tools \
      minisign -G -W -p "$graph_key_dir/gate.pub" -s "$graph_key_dir/gate.key" >/dev/null
  fi
  guest "$domain" 'install -d -m 0755 /etc/luma/update-graph-keys.d'
  guest_copy "$domain" "$graph_key_dir/gate.pub" /etc/luma/update-graph-keys.d/luma-gate.pub
  guest "$domain" "printf '%s\n' '[update]' 'graph_url = http://$guest_gateway:$http_port/luma-gate-graph/{channel}.json' 'events_url = http://$guest_gateway:$http_port/luma-gate-events' 'preview_repo_url = http://$guest_gateway:$http_port' 'stable_repo_url = http://$guest_gateway:$http_port' 'allow_insecure_urls = true' > /etc/luma/update.conf && systemctl stop luma-updated.timer >/dev/null 2>&1; true"
  if luma_os_channel_is_preview "$channel"; then
    # A preview channel needs an enrolled device. The gate stands in for Hub
    # and writes exactly what luma-update's EnrollPreview writes: a root-only
    # credential record and the preview URL in the root-only mirror list
    # (mirror-list layout), or a preview mirror list and the remote's url line
    # pointing at it (URL layout, earlier images) (docs/os/luma-update.md).
    guest "$domain" "umask 077; printf '{\"credential\": \"luma-gate-%s\", \"channel\": \"%s\", \"channels\": [\"%s\"], \"issued_at\": %s}\n' '$run_id' '$channel' '$channel' \$(date +%s) > /etc/luma/update-preview-credential && if grep -Fxq 'url=mirrorlist=file:///etc/luma/update-mirrorlist' /etc/ostree/remotes.d/luma.conf; then printf 'http://%s:%s\n' '$guest_gateway' '$http_port' > /etc/luma/update-mirrorlist; else printf 'http://%s:%s\n' '$guest_gateway' '$http_port' > /etc/luma/update-preview-mirrorlist && umask 022 && sed -i 's#^url=.*#url=mirrorlist=file:///etc/luma/update-preview-mirrorlist#' /etc/ostree/remotes.d/luma.conf; fi"
  else
    # A retained public-channel guest may still reference an earlier gate's
    # HTTP port. Update its payload endpoint along with the graph endpoint,
    # without enrollment credentials or changes to signature verification.
    guest "$domain" "umask 022; printf 'http://%s:%s\n' '$guest_gateway' '$http_port' > /etc/luma/update-mirrorlist && sed -i 's#^url=.*#url=mirrorlist=file:///etc/luma/update-mirrorlist#' /etc/ostree/remotes.d/luma.conf"
  fi
  guest "$domain" 'systemctl restart luma-updated.service >/dev/null 2>&1 || true; rpm-ostree status >/dev/null'

}

agent_field() {
  guest "$1" 'luma-update status --json' 2>/dev/null |
    python3 -c 'import json,sys; v=json.load(sys.stdin).get(sys.argv[1]); print(v if v is not None else "")' "$2"
}

# agent_stage DOMAIN COMMIT: check, then wait for the agent to stage COMMIT.
agent_stage() {
  local domain=$1 want=$2 state deadline
  guest "$domain" 'luma-update check' >>"$gate_dir/agent.log" 2>&1 || true
  deadline=$((SECONDS + 1800))
  while [ "$SECONDS" -lt "$deadline" ]; do
    state=$(agent_field "$domain" state || true)
    case "$state" in checking|downloading|'') sleep 10 ;; *) break ;; esac
  done
  guest "$domain" 'luma-update status --json' >>"$gate_dir/agent.log" 2>&1 || true
  [ "$(agent_field "$domain" state)" = staged ] && [ "$(agent_field "$domain" staged_commit)" = "$want" ]
}

stage_update() {
  # stage_update DOMAIN COMMIT VERSION MODE: MODE is agent or rpm-ostree.
  local domain=$1 commit=$2 mode=$4
  if [ "$mode" = agent ]; then
    agent_stage "$domain" "$commit"
  else
    guest "$domain" 'rpm-ostree upgrade' >>"$gate_dir/update-upgrade.log" 2>&1 &&
    guest "$domain" "rpm-ostree status --json" | python3 -c '
import json, sys
staged = [d for d in json.load(sys.stdin)["deployments"] if d.get("staged")]
sys.exit(0 if staged and staged[0]["checksum"] == sys.argv[1] else 1)' "$commit"
  fi
}

# ---------------------------------------------------------------------------
# Stage: update from the published head, then rollback
# ---------------------------------------------------------------------------
update_result=not-applicable
rollback_result=not-applicable
if [ -z "$previous" ]; then
  record update not-applicable "no published head on $ref: first release of the channel"
  record rollback not-applicable "no published head on $ref: first release of the channel"
elif [ "$overall" = pass ]; then
  update_result=fail
  previous_version=$(python3 "$luma_os_repo_root/scripts/os/lib/ostree_metadata.py" version "$gate_repo" "$previous")
  # The update machine is installed while the earlier ones still hold their
  # disks. Check again here so a volume that filled during the gate is
  # reported as what it is, instead of surfacing as a machine that never
  # finishes booting.
  volume_free_gib=$(df --output=avail -B1G "$LUMA_OS_ROOT" | tail -n 1 | tr -d ' ')
  if [ "${volume_free_gib:-0}" -lt 14 ]; then
    record update-install-previous fail "the release volume has ${volume_free_gib} GiB free; the update machine needs 14"
  elif install_vm update "$previous"; then
    upd=$installed_domain
    record update-install-previous pass "installed $previous ($previous_version)"
    wait_greenboot "$upd" 900 >/dev/null
    apply_installer_boot_policy "$upd" && reboot_vm "$upd"
    [ "$(booted_commit "$upd")" = "$previous" ] || record update-previous-booted fail 'previous release did not boot'
    guest "$upd" 'install -d -m 0700 /var/home/luma-gate && head -c 1048576 /dev/urandom > /var/home/luma-gate/marker && sha256sum /var/home/luma-gate/marker > /var/home/luma-gate/marker.sha256'
    mode=rpm-ostree
    if agent_setup "$upd"; then mode=agent; fi
    previous_agent_applies_kargs=no
    if [ "$mode" = agent ]; then
      agent_evr=$(guest "$upd" "rpm -q --qf '%{VERSION} %{RELEASE}' luma-update" 2>/dev/null || true)
      previous_agent_applies_kargs=$(python3 -c '
import sys
sys.path.insert(0, sys.argv[1])
import migration_plan as m
version, _, release = sys.argv[2].partition(" ")
newer = m.rpmvercmp(version, "1.0.0")
print("yes" if newer > 0 or (newer == 0 and m.rpmvercmp(release, "1.luma.4") >= 0) else "no")' \
        "$luma_os_repo_root/scripts/update" "${agent_evr:-0 0}")
      luma_os_log "previous release's luma-update $agent_evr applies kargs.d: $previous_agent_applies_kargs"
    fi
    point_gate_ref "$candidate"
    [ "$mode" = agent ] && gate_graph "$previous=$previous_version" "$candidate=$LUMA_OS_VERSION"
    # rpm-ostree's own check against the channel (what `rpm-ostree upgrade
    # --check` shows an administrator): it must answer without rpmostreed
    # crashing (a package-list diff crash was seen on 20260915.5).
    if guest "$upd" 'rpm-ostree upgrade --check; rc=$?; [ "$rc" -eq 0 ] || [ "$rc" -eq 77 ]' >"$gate_dir/update-rpm-ostree-check.log" 2>&1 &&
       ! guest "$upd" 'journalctl -b --no-pager -o cat | grep -Eq "rpm-ostreed.*(dumped core|Segmentation fault)|rpm-ostreed.service: Main process exited, code=(dumped|killed)"'; then
      record update-rpm-ostree-check pass 'rpm-ostree upgrade --check answers and rpmostreed stays up'
    else
      guest "$upd" 'journalctl -b -u rpm-ostreed --no-pager | tail -40' >>"$gate_dir/update-rpm-ostree-check.log" 2>&1 || true
      record update-rpm-ostree-check fail 'rpm-ostree upgrade --check failed or rpmostreed crashed (see update-rpm-ostree-check.log)'
      overall=fail
    fi
    luma_os_log "staging the candidate as an update through $mode"
    if stage_update "$upd" "$candidate" "$LUMA_OS_VERSION" "$mode"; then
      record update-staged pass "$mode staged $candidate"
      reboot_vm "$upd"
      booted_ok=1
      if [ "$mode" = agent ]; then
        [ "$(agent_field "$upd" booted_commit)" = "$candidate" ] &&
          [ "$(agent_field "$upd" booted_version)" = "$LUMA_OS_VERSION" ] || booted_ok=0
        guest "$upd" 'journalctl -b -u luma-update-boot.service --no-pager; cat /var/lib/luma-update/state.json' >"$gate_dir/update-agent-boot.log" 2>&1 || true
        [ "$booted_ok" -eq 1 ] && record update-agent-booted pass "luma-update reports $LUMA_OS_VERSION booted" ||
          record update-agent-booted fail 'luma-update does not report the candidate as booted'
      fi
      # Flicker-free boot on updated computers (luma-boot-theme 1.luma.15+):
      # bootupd never rewrites the grub.cfg this machine was installed with, so
      # the candidate's luma-boot-hidden-menu must have added the piece (from
      # luma-update right after staging, or its boot unit), placed before the
      # BLS menu, with every other line of the file unchanged, and GRUB's menu
      # must still open with Esc during the hidden countdown.
      if guest "$upd" 'test -x /usr/libexec/luma-boot-hidden-menu' >/dev/null 2>&1; then
        gate_hidden_menu "$upd" update || overall=fail
      fi
      if [ "$booted_ok" -eq 1 ] && evaluate "$upd" update "$candidate" &&
         guest "$upd" 'sha256sum --check --status /var/home/luma-gate/marker.sha256'; then
        update_result=pass
        record update pass "update through $mode booted healthy with /var data intact"
      else
        record update fail 'updated system failed its checks'
      fi
    else
      record update-staged fail "$mode did not stage the candidate"
    fi
  else
    record update-install-previous fail "install of $previous failed"
  fi
  [ "$update_result" = pass ] || overall=fail

  if [ "$update_result" = pass ] && [ "$skip_rollback" -eq 0 ]; then
    rollback_result=fail
    manual=fail
    rollback_command='rpm-ostree rollback'
    [ "$mode" = agent ] && rollback_command='luma-update rollback'
    # Manual rollback: the previous release is retained and boots, and the
    # candidate comes back the same way.
    if guest "$upd" "$rollback_command" >"$gate_dir/rollback-manual.log" 2>&1 &&
       reboot_vm "$upd" && [ "$(booted_commit "$upd")" = "$previous" ] &&
       [ "$(wait_greenboot "$upd" 900)" = healthy ] &&
       guest "$upd" 'sha256sum --check --status /var/home/luma-gate/marker.sha256' &&
       # grub.cfg is shared by every deployment: the previous release boots
       # through the one the candidate brought up to date.
       guest "$upd" 'grub2-script-check /boot/grub2/grub.cfg' >>"$gate_dir/rollback-manual.log" 2>&1 &&
       guest "$upd" "$rollback_command" >>"$gate_dir/rollback-manual.log" 2>&1 &&
       reboot_vm "$upd" && [ "$(booted_commit "$upd")" = "$candidate" ] &&
       [ "$(wait_greenboot "$upd" 900)" = healthy ]; then
      manual=pass
      record rollback-manual pass "$rollback_command reached the previous release healthy and returned to the candidate"
    else
      record rollback-manual fail 'manual rollback to the previous release and back did not complete'
    fi

    automatic=fail
    failure_version="$LUMA_OS_VERSION.1"
    if [ "$manual" = pass ]; then
      if [ "$mode" = agent ]; then
        built=0
        # The rollback round trip can restore a deployment's older /etc
        # endpoint. Reapply this gate's graph and payload URLs before staging.
        agent_setup "$upd" >>"$gate_dir/rollback-build.log" 2>&1
        luma_os_gpg_unlock
        "$luma_os_repo_root/scripts/os/lib/gate-failure-build.sh" \
          --source-repo "$gate_repo" --into-repo "$gate_repo" --commit "$candidate" \
          --work "$vm_dir/failure" --ref "$ref" --version "$failure_version" \
          >"$gate_dir/rollback-build.log" 2>&1 && built=1
        if [ "$built" -eq 1 ]; then
          failure_commit=$(cat "$vm_dir/failure/commit")
          point_gate_ref "$failure_commit"
          guest_copy "$upd" "$vm_dir/failure/gate-key.gpg" /etc/pki/ostree/luma-gate-forced-failure.gpg
          # Gate-only: this VM's luma remote also trusts the throwaway key.
          guest "$upd" "sed -i 's#^gpgkeypath=.*#gpgkeypath=/etc/pki/ostree/luma-release.gpg;/etc/pki/ostree/luma-gate-forced-failure.gpg#' /etc/ostree/remotes.d/luma.conf"
          gate_graph "$candidate=$LUMA_OS_VERSION" "$failure_commit=$failure_version"
          if agent_stage "$upd" "$failure_commit"; then
            record rollback-staged pass "luma-update staged the forced-failure build $failure_commit"
          else
            built=0
            record rollback-staged fail 'luma-update did not stage the forced-failure build'
          fi
        else
          record rollback-build fail 'could not create the forced-failure build'
        fi
      else
        built=0
        if "$luma_os_repo_root/scripts/os/lib/gate-failure-build.sh" \
            --source-repo "$gate_repo" --commit "$candidate" --work "$vm_dir/failure" \
            --ref "$LUMA_OS_REF_PREFIX/gate-forced-failure" >"$gate_dir/rollback-build.log" 2>&1; then
          failure_commit=$(cat "$vm_dir/failure/commit")
          failure_port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
          luma_os_podman run --detach --rm --name "$http_name-failure" --network host --security-opt label=disable \
            --volume "$vm_dir/failure/repo:/srv/repo:ro" "$(luma_os_tools_image)" \
            python3 -m http.server --bind 127.0.0.1 --directory /srv/repo "$failure_port" >/dev/null
          sleep 2
          guest_copy "$upd" "$vm_dir/failure/gate-key.gpg" /etc/pki/ostree/luma-gate-forced-failure.gpg
          guest "$upd" "printf '%s\n' '[remote \"luma-gate-forced-failure\"]' 'url=http://$guest_gateway:$failure_port' 'gpg-verify=true' 'gpg-verify-summary=true' 'gpgkeypath=/etc/pki/ostree/luma-gate-forced-failure.gpg' > /etc/ostree/remotes.d/luma-gate-forced-failure.conf"
          if guest "$upd" "rpm-ostree rebase luma-gate-forced-failure:$LUMA_OS_REF_PREFIX/gate-forced-failure" >"$gate_dir/rollback-rebase.log" 2>&1; then
            built=1
            record rollback-staged pass "staged the forced-failure build $failure_commit"
          else
            record rollback-staged fail 'could not stage the forced-failure build'
          fi
        else
          record rollback-build fail 'could not create the forced-failure build'
        fi
      fi

      if [ "$built" -eq 1 ]; then
        guest "$upd" 'grub2-editenv list' >"$gate_dir/rollback-grubenv-before.log" 2>&1 || true
        guest "$upd" 'systemctl reboot' >/dev/null 2>&1 || true
        # greenboot restarts the failing trial boots until its boot counter
        # runs out, then rolls back to the candidate and restarts.
        deadline=$((SECONDS + 2700))
        returned=
        sleep 60
        while [ "$SECONDS" -lt "$deadline" ]; do
          current=$(booted_commit "$upd" 2>/dev/null || true)
          if [ "$current" = "$candidate" ] && [ "$(wait_greenboot "$upd" 600)" = healthy ]; then
            returned=1
            break
          fi
          sleep 20
        done
        guest "$upd" 'journalctl --list-boots --no-pager; for b in -6 -5 -4 -3 -2 -1 0; do echo "== boot $b"; journalctl -b $b -u greenboot-healthcheck.service -u luma-update-boot.service --no-pager 2>/dev/null | tail -n 30; done; echo "== grubenv"; grub2-editenv list; rpm-ostree status' >"$gate_dir/rollback-automatic.log" 2>&1 || true
        screenshot "$upd" rollback
        recorded=1
        if [ "$mode" = agent ]; then
          guest "$upd" 'luma-update status --json' >"$gate_dir/rollback-agent-status.json" 2>&1 || true
          [ "$(agent_field "$upd" rolled_back_version)" = "$failure_version" ] || recorded=0
        fi
        if [ -n "$returned" ] && [ "$recorded" -eq 1 ] &&
           grep -q 'Luma gate: forced health-check failure' "$gate_dir/rollback-automatic.log" &&
           guest "$upd" 'sha256sum --check --status /var/home/luma-gate/marker.sha256'; then
          automatic=pass
          record rollback-automatic pass "greenboot failed the forced-failure trial boots, the machine returned to the candidate by itself${mode:+ and $mode recorded the rollback}"
        else
          record rollback-automatic fail "no automatic return to the candidate (returned=${returned:-no}, agent recorded=$recorded)"
        fi
      fi
      luma_os_podman stop --time 2 "$http_name-failure" >/dev/null 2>&1 || true
    fi
    [ "$manual" = pass ] && [ "$automatic" = pass ] && rollback_result=pass
    [ "$rollback_result" = pass ] || overall=fail
  elif [ "$skip_rollback" -eq 1 ]; then
    rollback_result=skipped
    record rollback skipped 'skipped by operator (--skip-rollback); publication refuses this gate'
  fi
fi

# ---------------------------------------------------------------------------
# Stage: choosing a channel (ADR-030 section 4: every channel is public), on the
# fresh install of the candidate, with the candidate's own agent. The computer
# follows Official, then Nightly is chosen exactly as Depot does (SetChannel,
# then a check): a next build (the candidate's tree recommitted as
# $LUMA_OS_VERSION.2 on the channel ref, signed with the release key) must be
# found and staged from the gate's repository with no credential, booted after a
# restart, rolled back, and the computer returned to Official. Candidates whose
# agent still requires enrollment for a preview channel (luma-update before
# 1.0.0-1.luma.10) record not-applicable.
# ---------------------------------------------------------------------------
if [ -n "${fresh:-}" ] && [ "$skip_fresh" -eq 0 ] && [ -n "$(virsh domstate "$fresh" 2>/dev/null)" ] &&
   [ "$channel" = nightly ]; then   # the next build is published on the channel being gated
  virsh start "$fresh" >/dev/null 2>&1 || true
  if ! wait_ssh "$fresh" 900; then
    record fresh-channel-choice fail 'the fresh install did not start again for the channel checks'
    overall=fail
  elif ! guest "$fresh" 'rpm -q luma-update >/dev/null && ! grep -q "enroll in early updates before choosing" /usr/lib/python3*/site-packages/luma_update/engine.py' 2>/dev/null; then
    record fresh-channel-choice not-applicable 'the candidate agent requires enrollment for preview channels (luma-update before 1.0.0-1.luma.10)'
  else
    c_field() { guest "$fresh" 'luma-update status --json' 2>/dev/null |
      python3 -c 'import json,sys; v=json.load(sys.stdin).get(sys.argv[1]); print(json.dumps(v) if isinstance(v,(bool,list)) else ("" if v is None else v))' "$1"; }
    c_record() { if [ "$2" = 0 ]; then record "fresh-channel-$1" pass "$3"; else record "fresh-channel-$1" fail "$3"; overall=fail; fi; }
    # c_idle [SECONDS]: wait until the agent is not checking or downloading.
    c_idle() {
      local until=$((SECONDS + ${1:-900})) st
      while [ "$SECONDS" -lt "$until" ]; do
        st=$(c_field state 2>/dev/null || true)
        case "$st" in checking|downloading|preparing|staging) sleep 10 ;; *) return 0 ;; esac
      done
      return 1
    }
    c_booted() { guest "$fresh" 'rpm-ostree status --json' | python3 -c 'import json,sys; print(next(d["checksum"] for d in json.load(sys.stdin)["deployments"] if d["booted"]))'; }
    next_version="$LUMA_OS_VERSION.2"
    # Prepare the entire tree before unlocking for its detached signature.
    # A preset obtained before tree traversal can disappear before signing.
    # Bound to the channel ref like every real build (and the forced-failure
    # build): rpm-ostree refuses a commit whose ref binding does not name the
    # ref it pulled, which failed 20260917.2's nightly-staged check with a
    # 'transaction' error.
    next_commit=$(luma_os_commit_and_sign "$gate_repo" --orphan --parent="$candidate" --tree="ref=$candidate" \
      --bind-ref="$ref" \
      --owner-uid=0 --owner-gid=0 --selinux-policy-from-base \
      --subject='Luma gate next build (never published)' --add-metadata-string=version="$next_version" \
      2>"$gate_dir/channel-next-build.log") || next_commit=""
    if [ -z "$next_commit" ]; then
      record fresh-channel-choice fail 'could not make the next build (see channel-next-build.log)'
      overall=fail
    else
      point_gate_ref "$next_commit"
      # The agent's usual gate settings (agent_setup: graph key, loopback graph); no credential at all.
      agent_setup "$fresh" >/dev/null 2>&1 || true
      guest "$fresh" "rm -f /etc/luma/update-preview-credential /etc/luma/update-preview-mirrorlist; umask 077; printf '%s\n' '$repo_url' >/etc/luma/update-mirrorlist; systemctl restart luma-updated.service >/dev/null 2>&1; true"
      gate_graph "$candidate=$LUMA_OS_VERSION" "$next_commit=$next_version"
      c_idle 300 || true
      guest "$fresh" 'luma-update channel stable' >"$gate_dir/channel.log" 2>&1 || true
      rc=1; [ "$(c_field channel)/$(c_field available_channels)" = 'stable/["stable", "beta", "nightly"]' ] && rc=0
      c_record official "$rc" "follows Official with every channel on offer: $(c_field channel) $(c_field available_channels)"
      # Every command here may fail without ending the gate (set -e): the
      # checks below record what the agent reached.
      c_idle 300 || true
      guest "$fresh" 'busctl --system --timeout=1500 call org.projectluma.Update1 /org/projectluma/Update1 org.projectluma.Update1 SetChannel s nightly' >>"$gate_dir/channel.log" 2>&1 || true
      c_idle 300 || true
      guest "$fresh" 'luma-update check' >>"$gate_dir/channel.log" 2>&1 || true
      c_idle 900 || true
      [ "$(c_field staged_commit 2>/dev/null || true)" = "$next_commit" ] ||
        { guest "$fresh" 'luma-update download' >>"$gate_dir/channel.log" 2>&1 || true; c_idle 1800 || true; }
      rc=1; [ "$(c_field channel)/$(c_field preview_enrolled)/$(c_field staged_commit)" = "nightly/false/$next_commit" ] &&
        guest "$fresh" '! test -e /etc/luma/update-preview-credential' && rc=0
      c_record nightly-staged "$rc" "chose Nightly with no credential; $next_version found and staged ($(c_field state), last error class '$(c_field last_error_class)')"
      if [ "$rc" = 0 ]; then
        reboot_vm "$fresh" || true
        rc=1; [ "$(c_booted)" = "$next_commit" ] && [ "$(c_field channel)" = nightly ] && rc=0
        c_record nightly-booted "$rc" "restarted into $next_version on Nightly"
        guest "$fresh" 'luma-update rollback' >>"$gate_dir/channel.log" 2>&1 || true
        reboot_vm "$fresh" || true
        rc=1; [ "$(c_booted)" = "$candidate" ] && rc=0
        c_record rollback "$rc" "rolled back to $LUMA_OS_VERSION"
      fi
      c_idle 300 || true
      guest "$fresh" 'busctl --system --timeout=1500 call org.projectluma.Update1 /org/projectluma/Update1 org.projectluma.Update1 SetChannel s stable' >>"$gate_dir/channel.log" 2>&1 || true
      rc=1; [ "$(c_field channel)" = stable ] && guest "$fresh" "grep -Fxq '$repo_url' /etc/luma/update-mirrorlist" && rc=0
      c_record back-to-official "$rc" "back to Official from the same public repository"
      point_gate_ref "$candidate"
    fi
  fi
  virsh destroy "$fresh" >/dev/null 2>&1 || true
fi

# A run that skipped a stage on request proves less than the gate requires.
if [ "$overall" = pass ] && { { [ "$skip_fresh" -eq 1 ] && [ -z "$update_from" ]; } || [ "$rollback_result" = skipped ]; }; then
  overall=incomplete
fi
completed=$(date -u +%Y-%m-%dT%H:%M:%SZ)
python3 - "$results" "$gate_dir/result.json" <<PY
import json, sys
stages = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
result = {
    "schema": "org.projectluma.os-gate/v1",
    "build_id": "$build_id",
    "version": "$LUMA_OS_VERSION",
    "channel": "$channel",
    "ref": "$ref",
    "commit": "$candidate",
    "previous_commit": "$previous" or None,
    "result": "$overall",
    "fresh": "skipped" if any(s["stage"] == "fresh" and s["result"] == "skipped" for s in stages)
             else ("pass" if not any(s["result"] == "fail" and s["stage"].startswith("fresh") for s in stages) else "fail"),
    "no_account": "$no_account_result",
    "update": "$update_result",
    "rollback": "$rollback_result",
    "stages": stages,
    "run_id": "$run_id",
    "completed_utc": "$completed",
}
with open(sys.argv[2], "w", encoding="utf-8") as stream:
    json.dump(result, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY
if [ -n "$update_from" ]; then
  # Additional evidence only: publication reads gate-result.json, which stays
  # the gate from the channel head.
  cp "$gate_dir/result.json" "$build_dir/gate-update-from-${update_from:0:12}.json"
else
  cp "$gate_dir/result.json" "$build_dir/gate-result.json"
fi
luma_os_log "gate result: $overall (update $update_result, rollback $rollback_result)"
[ "$overall" = pass ]
