#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Verify Atlas installer media in a disposable VM (never on hardware).
#
#   verify-media.sh --iso ISO [--account person|none] [--update-channel-from-local]
#                   [--preview-credential-file FILE] [--keep-vm]
#
# --preview-credential-file names the credential the medium carries: the
# installed mirror list must hold the credential-checked URL, the enrollment
# record must be root-only, and no other file in /etc, /var, /root or /home
# may contain the credential (it is compared inside the VM, never printed).
#
# --account person (the default) creates a locked account, as a person does in
# Atlas; --account none creates none and leaves first-boot setup on, and then
# also requires GDM's first-boot setup session to stay up with GNOME Shell
# answering (the image's display and Shell health checks run enforced).
#
# 1. Installs from the ISO unattended with tests/os/gate/atlas-media.ks.in,
#    which only answers disk and account questions and includes the media's
#    own installer kickstart (payload choice, GPG-verified pull, remote, key,
#    origin, channel).
# 2. Boots the installed system and checks what ADR-030 section 9 requires of
#    an Atlas install: the booted commit is the medium's release, the origin is
#    luma:<channel ref>, the luma remote is the HTTPS repository with signed
#    summaries and commits and the release key, the key is the pinned one,
#    /etc/luma/update-channel.conf names the channel, the physical repository
#    does not define the remote a second time, and greenboot judged the boot
#    healthy (then tests/os/image-contract.sh).
# 3. With --update-channel-from-local, enrolls the VM in the preview channel
#    the way luma-update does, pointed at this host's loopback content server
#    (luma-os-http.service, credential from the root-only local test
#    credential), serves the channel graph signed with the production graph key
#    from publish/graph, and requires luma-update to stage and boot the
#    channel's current head.
#
# 4. --require-check NAME[:ARGS] (repeatable) runs tests/os/gate/NAME.sh from
#    the release source as root in the installed system (one JSON line per
#    check); a missing script or any failing check fails the medium. Without
#    it, no-install-hurdles.sh runs when the image carries the install commands.
# 5. --update-from-origin-to VERSION: the out-of-box update path against the
#    real origin, driven by tests/os/gate/update-out-of-box.sh in phases with a
#    restart between each: pick Nightly and stage VERSION (a delta, not a new
#    medium), boot it, roll back to the medium's release, return to Official.
#
# Evidence: $LUMA_OS_ROOT/media/verify-<UTC time>/.

set -euo pipefail
. "$(dirname -- "$0")/lib/common.sh"

iso=
update=0
keep=0
account=person
credential_file=
forbid_credential_file=
expect_build_id=
expect_enrollment=
expect_packages=()
require_checks=()
checks_revision=
update_to=
skip_release_checks=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --iso) iso=$(realpath "${2:?}"); shift 2 ;;
    --update-channel-from-local) update=1; shift ;;
    --keep-vm) keep=1; shift ;;
    --account) account=${2:?}; shift 2 ;;
    --preview-credential-file) credential_file=$(realpath "${2:?}"); shift 2 ;;
    --forbid-credential-file) forbid_credential_file=$(realpath "${2:?}"); shift 2 ;;
    --expect-build-id) expect_build_id=${2:?}; shift 2 ;;
    --expect-package) expect_packages+=("${2:?}"); shift 2 ;;
    --expect-enrollment) expect_enrollment=${2:?}; shift 2 ;;
    --require-check) require_checks+=("${2:?}"); shift 2 ;;
    --checks-revision) checks_revision=${2:?}; shift 2 ;;
    --update-from-origin-to) update_to=${2:?}; shift 2 ;;
    --skip-release-checks) skip_release_checks=1; shift ;;
    *) printf 'usage: %s --iso ISO [--account person|none] [--update-channel-from-local] [--keep-vm] [--preview-credential-file FILE] [--forbid-credential-file FILE] [--expect-build-id ID] [--expect-package NAME]... [--expect-enrollment enrolled|none] [--require-check NAME[:ARGS]]... [--checks-revision REV] [--update-from-origin-to VERSION] [--skip-release-checks]\n' "$0" >&2; exit 2 ;;
  esac
done
case "$account" in person|none) ;; *) printf 'unknown --account: %s\n' "$account" >&2; exit 2 ;; esac
case "$expect_enrollment" in ''|enrolled|none) ;; *) printf 'unknown --expect-enrollment: %s\n' "$expect_enrollment" >&2; exit 2 ;; esac
[ -z "$credential_file" ] || [ -z "$forbid_credential_file" ] ||
  { printf -- '--preview-credential-file and --forbid-credential-file exclude each other\n' >&2; exit 2; }
luma_os_require_root
luma_os_check_host
luma_os_vm_lock
luma_os_check_space 25
[ -f "$iso" ] && [ -f "$iso.release" ] || luma_os_die "ISO or its .release record is missing: $iso"
luma_os_load_env /dev/stdin <<<"$(sed 's/^\([a-z_]*\)=/MEDIA_\U\1=/' "$iso.release")"
volid=$(isoinfo -d -i "$iso" | sed -n 's/^Volume id: //p')
# The medium's release is judged by the checks committed with its own source.
if [ "${MEDIA_TEST:-}" = true ]; then
  # Test media carry an exported candidate, not a published release.
  media_source=$(sed -n 's/^LUMA_SOURCE_REVISION=//p' "$LUMA_OS_ROOT/builds/$MEDIA_TEST_BUILD/build.env")
  [ "$update" -eq 0 ] || luma_os_die 'test media carry no published release to update from'
elif [ -n "${MEDIA_CANDIDATE_BUILD:-}" ]; then
  # A gated candidate's release medium, verified before publication.
  media_source=$(sed -n 's/^LUMA_SOURCE_REVISION=//p' "$LUMA_OS_ROOT/builds/$MEDIA_CANDIDATE_BUILD/build.env")
  [ "$update" -eq 0 ] || luma_os_die 'an unpublished candidate has no published release to update from'
else
  media_manifest="$(luma_os_channel_repo "$MEDIA_CHANNEL")/luma/releases/$MEDIA_VERSION/manifest.json"
  [ -s "$media_manifest" ] || luma_os_die "no published release manifest for $MEDIA_VERSION"
  media_source=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_revision"])' "$media_manifest")
fi
# Guest-side release checks (tests/os/gate/*.sh) come from the medium's own
# source unless --checks-revision names the release they judge (an older
# medium updating to tonight's build is judged by tonight's checks).
checks_source=${checks_revision:-$media_source}
media_build_dir=
if [ "${MEDIA_TEST:-}" = true ]; then
  media_build_dir="$LUMA_OS_ROOT/builds/$MEDIA_TEST_BUILD"
elif [ -n "${MEDIA_CANDIDATE_BUILD:-}" ]; then
  media_build_dir="$LUMA_OS_ROOT/builds/$MEDIA_CANDIDATE_BUILD"
fi
if [ -n "$media_build_dir" ]; then
  luma_os_load_env "$media_build_dir/build.env"
  if [ "$LUMA_SOURCE_DIRTY" = true ] && [ -n "$checks_revision" ] && [ "$checks_revision" != "$media_source" ]; then
    luma_os_die 'private media cannot replace its bound checks with a Git revision'
  fi
fi

run=$(date -u +%Y%m%dT%H%M%SZ)
out="$LUMA_OS_ROOT/media/verify-$run"
vm="$LUMA_OS_ROOT/vm/media-$run"
install -d -m 0755 "$out" "$vm"
exec > >(tee -a "$out/verify.log") 2>&1
key_dir="/root/.ssh/luma-os-media-$run"
install -d -m 0700 "$key_dir"
ssh-keygen -q -t ed25519 -N '' -f "$key_dir/ssh-key"
domain="luma-os-media-$run"
port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
gateway=$(ip -4 route show default | awk '{ print $3; exit }')
release_files="$vm/release-checks"
luma_os_release_files "$media_source" "$release_files" "$media_build_dir"
results="$out/checks.txt"
: >"$results"
failures=0
check() {
  local description=$1
  shift
  if "$@" >>"$out/check-output.log" 2>&1; then
    printf 'PASS  %s\n' "$description" | tee -a "$results"
  else
    printf 'FAIL  %s\n' "$description" | tee -a "$results"
    failures=$((failures + 1))
  fi
}
guest() {
  ssh -q -p "$port" -i "$key_dir/ssh-key" -o BatchMode=yes -o ConnectTimeout=10 \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR root@127.0.0.1 "$@"
}
wait_ssh() {
  local deadline=$((SECONDS + ${1:-900}))
  while [ "$SECONDS" -lt "$deadline" ]; do guest true 2>/dev/null && return 0; sleep 5; done
  return 1
}
greenboot_state() {
  local deadline=$((SECONDS + 900)) state
  while [ "$SECONDS" -lt "$deadline" ]; do
    state=$(guest 'systemctl show -p ActiveState --value greenboot-healthcheck.service; systemctl show -p Result --value greenboot-healthcheck.service' 2>/dev/null | tr '\n' ' ' || true)
    case "$state" in 'active success '*) echo healthy; return ;; 'failed '*) echo unhealthy; return ;; esac
    sleep 5
  done
  echo unknown
}
cleanup() {
  local status=$?
  set +e
  # LUMA_MEDIA_HOLD_FILE: while that file exists, the VM keeps running in the
  # state the checks left it (diagnosis on the booted system).
  while [ -n "${LUMA_MEDIA_HOLD_FILE:-}" ] && [ -e "$LUMA_MEDIA_HOLD_FILE" ]; do sleep 10; done
  virsh destroy "$domain" >/dev/null 2>&1
  # LUMA_MEDIA_KEEP_FAILED_VM=1 keeps the installed disk of a failed
  # verification for diagnosis (its domain is shut off; remove it after).
  if [ "$status" -ne 0 ] && [ "${LUMA_MEDIA_KEEP_FAILED_VM:-0}" = 1 ]; then
    keep=1
    luma_os_log "verification failed: VM $domain kept for diagnosis (disk under $vm, key $key_dir)"
  fi
  if [ "$keep" -eq 0 ]; then
    virsh undefine "$domain" --nvram >/dev/null 2>&1
    rm -rf "$vm" "$key_dir"
  fi
  exit "$status"
}
trap cleanup EXIT

if [ "$account" = person ]; then
  account_lines='firstboot --disable\nuser --name=luma-media-test --gecos="Media Test" --lock'
else
  account_lines='# no account: first-boot setup stays on'
fi
sed -e "s#@SSH_PUBLIC_KEY@#$(cat "$key_dir/ssh-key.pub")#" \
  -e "s|@ACCOUNT@|$account_lines|" \
  "$luma_os_repo_root/tests/os/gate/atlas-media.ks.in" >"$vm/atlas-media.ks"
qemu-img create -q -f qcow2 "$vm/disk.qcow2" 48G
luma_os_log "installing $MEDIA_VERSION from $(basename "$iso") ($volid), account: $account"
started=$SECONDS
virt-install --connect qemu:///system --name "$domain" \
  --memory 6144 --vcpus 4 --cpu host-passthrough --machine q35 --boot uefi \
  --disk "path=$vm/disk.qcow2,format=qcow2,bus=virtio,cache=unsafe,discard=unmap" \
  --location "$iso,kernel=images/pxeboot/vmlinuz,initrd=images/pxeboot/initrd.img" \
  --initrd-inject "$vm/atlas-media.ks" \
  --extra-args "inst.ks=file:/atlas-media.ks inst.stage2=hd:LABEL=$volid inst.text console=ttyS0 inst.notmux" \
  --network "passt,portForward0.proto=tcp,portForward0.range0.start=$port,portForward0.range0.to=22" \
  --graphics vnc,listen=127.0.0.1 --video virtio --rng /dev/urandom \
  --serial "file,path=/var/log/libvirt/qemu/$domain-serial.log" \
  --os-variant fedora-unknown --noreboot --noautoconsole --wait 60 >"$out/virt-install.log" 2>&1 || {
    cp "/var/log/libvirt/qemu/$domain-serial.log" "$out/install-serial.log" 2>/dev/null
    luma_os_die 'installation from the media failed'
  }
cp "/var/log/libvirt/qemu/$domain-serial.log" "$out/install-serial.log" 2>/dev/null || true
luma_os_log "installed in $((SECONDS - started))s"
virsh start "$domain" >/dev/null
wait_ssh 900 || luma_os_die 'installed system did not come up'

verdict=$(greenboot_state)
guest 'rpm-ostree status --json' >"$out/rpm-ostree-status.json"
booted() { python3 -c 'import json,sys; d=[x for x in json.load(open(sys.argv[1]))["deployments"] if x.get("booted")][0]; print(d.get(sys.argv[2]) or "")' "$out/rpm-ostree-status.json" "$1"; }
check "greenboot judged the first boot healthy ($verdict)" test "$verdict" = healthy
check "booted commit is the medium's release $MEDIA_COMMIT" test "$(booted checksum)" = "$MEDIA_COMMIT"
check "origin is luma:$MEDIA_REF" test "$(booted origin)" = "luma:$MEDIA_REF"
guest 'cat /etc/ostree/remotes.d/luma.conf; echo ---; cat /etc/luma/update-mirrorlist; echo ---; ostree remote list --repo=/ostree/repo; echo ---; grep -c "remote \"luma\"" /ostree/repo/config || true; echo ---; cat /etc/luma/update-channel.conf' >"$out/remote-and-channel.txt"
check 'the luma remote reads the root-only mirror list' guest "grep -Fxq 'url=mirrorlist=file:///etc/luma/update-mirrorlist' /etc/ostree/remotes.d/luma.conf && test \"\$(stat -c '%a %U' /etc/luma/update-mirrorlist)\" = '600 root' && test \"\$(stat -c %a /etc/ostree/remotes.d/luma.conf)\" = 644"
if [ -z "$credential_file" ]; then
  check 'the mirror list is the HTTPS repository' guest "test \"\$(cat /etc/luma/update-mirrorlist)\" = '$LUMA_OS_STABLE_URL'"
else
  scp -q -P "$port" -i "$key_dir/ssh-key" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    "$credential_file" root@127.0.0.1:/root/.luma-media-credential-check
  guest 'chmod 0600 /root/.luma-media-credential-check'
  check 'the mirror list holds the credential-checked preview URL' guest 'test "$(cat /etc/luma/update-mirrorlist)" = "https://dl.simplyluma.com/os/preview/$(tr -d "\n" </root/.luma-media-credential-check)/repo"'
  check 'the preview enrollment record is root-only and names the channel' guest "test \"\$(stat -c '%U:%G %a' /etc/luma/update-preview-credential)\" = 'root:root 600' && python3 -c 'import json,sys; d=json.load(open(\"/etc/luma/update-preview-credential\")); sys.exit(0 if d[\"channel\"] == \"$MEDIA_CHANNEL\" and d[\"credential\"] == open(\"/root/.luma-media-credential-check\").read().strip() else 1)'"
  check 'the credential is in no other file on the installed system' guest 'hits=$(grep -rlFs -f /root/.luma-media-credential-check /etc /var/log /var/lib /var/home /root 2>/dev/null | grep -vxF -e /etc/luma/update-mirrorlist -e /etc/luma/update-preview-credential -e /root/.luma-media-credential-check); [ -z "$hits" ] || { echo "$hits"; exit 1; }'
  guest 'rm -f /root/.luma-media-credential-check'
fi
check 'the luma remote requires signed commits and summaries' guest "grep -Fxq gpg-verify=true /etc/ostree/remotes.d/luma.conf && grep -Fxq gpg-verify-summary=true /etc/ostree/remotes.d/luma.conf"
check 'the luma remote uses the release key file and the Luma collection' guest "grep -Fxq 'gpgkeypath=$LUMA_OS_RELEASE_KEY_PATH' /etc/ostree/remotes.d/luma.conf && grep -Fxq 'collection-id=$LUMA_OS_COLLECTION_ID' /etc/ostree/remotes.d/luma.conf"
check 'the physical repository does not define the remote again' guest "! grep -q 'remote \"luma\"' /ostree/repo/config"
check 'the release key on the system is the pinned key' guest "gpg --batch --no-options --homedir \$(mktemp -d) --with-colons --show-keys $LUMA_OS_RELEASE_KEY_PATH | grep -q '^fpr:::::::::$LUMA_OS_RELEASE_KEY_FINGERPRINT:'"
check 'the installed luma.conf is byte-identical to the image copy' guest 'cmp /usr/etc/ostree/remotes.d/luma.conf /etc/ostree/remotes.d/luma.conf'
check 'the mirror list holds exactly one URL line' guest '[ "$(wc -l < /etc/luma/update-mirrorlist)" = 1 ] && [ "$(stat -c "%U:%G %a" /etc/luma/update-mirrorlist)" = "root:root 600" ]'
check 'the installer left the image remote as it is (kickstart log)' guest 'grep -rqs "the image.s luma.conf already reads /etc/luma/update-mirrorlist" /var/log/anaconda/ /root/anaconda-ks.cfg /tmp/ks-script-*.log'
check 'the installer retired the fstab entry for /' guest '! grep -Eq "^[^#[:space:]]+[[:space:]]+/[[:space:]]" /etc/fstab'
check 'no failed system units' guest '[ -z "$(systemctl list-units --state=failed --no-legend --plain)" ]'
# A failed unit is evidence to keep, not only a line: its status and this
# boot's journal for it, so a failure seen once can still be explained.
failed_units=$(guest 'systemctl list-units --state=failed --no-legend --plain | awk "{ print \$1 }"' 2>/dev/null || true)
if [ -n "$failed_units" ]; then
  for unit in $failed_units; do
    guest "systemctl status --no-pager --full $unit; echo; journalctl -b --no-pager -o short-monotonic -u $unit" \
      >"$out/failed-unit-$unit.log" 2>&1 || true
  done
  guest 'journalctl -b --no-pager -o short-monotonic' >"$out/journal.log" 2>&1 || true
fi
check "the channel file names $MEDIA_CHANNEL" guest "grep -Fxq 'channel=$MEDIA_CHANNEL' /etc/luma/update-channel.conf"
check 'the machine boots to the graphical login' guest 'test "$(systemctl get-default)" = graphical.target'
guest 'test -d /var/tmp' && scp -q -P "$port" -i "$key_dir/ssh-key" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  "$release_files/tests/os/image-contract.sh" "$release_files/config/os/release.env" root@127.0.0.1:/var/tmp/
guest "bash /var/tmp/image-contract.sh --release-env /var/tmp/release.env --expect-commit $MEDIA_COMMIT --channel $MEDIA_CHANNEL" >"$out/image-contract.log" 2>&1 &&
  printf 'PASS  image contract\n' | tee -a "$results" || { printf 'FAIL  image contract (see image-contract.log)\n' | tee -a "$results"; failures=$((failures + 1)); }

check 'sudo -n true succeeds for a wheel member' guest 'set -u; useradd -m -G wheel luma-media-sudo && printf "luma-media-sudo ALL=(ALL) NOPASSWD: ALL\n" >/etc/sudoers.d/90-luma-media && chmod 0440 /etc/sudoers.d/90-luma-media; runuser -u luma-media-sudo -- sudo -n true; rc=$?; rm -f /etc/sudoers.d/90-luma-media; userdel -r luma-media-sudo >/dev/null 2>&1; exit $rc'

if [ "$account" = none ]; then
  guest 'LUMA_GREENBOOT_FORCE_TRIAL=1 /usr/lib/greenboot/check/required.d/10-luma-display-manager.sh && LUMA_GREENBOOT_FORCE_TRIAL=1 /usr/lib/greenboot/check/required.d/20-luma-shell-responsive.sh' >"$out/display-checks.log" 2>&1 &&
    printf 'PASS  display manager and GNOME Shell checks (enforced) with no account\n' | tee -a "$results" ||
    { printf 'FAIL  display manager and GNOME Shell checks (enforced) with no account (see display-checks.log)\n' | tee -a "$results"; failures=$((failures + 1)); }
  session=$(guest /usr/libexec/luma-os/graphical-session 2>/dev/null || true)
  check "first-boot setup is the greeter session (${session:-none})" \
    test "$(awk '{ print $2 " " $4 }' <<<"$session")" = 'greeter gnome-initial-setup'
  guest 'journalctl -b --no-pager' >"$out/journal.log" 2>&1 || true
fi

# What the nightly media stage (scripts/os/nightly-media.sh) requires of every
# medium it publishes, on the system installed from it.
if [ -n "$expect_build_id" ]; then
  check "the installed system is build $expect_build_id" guest ". /usr/lib/os-release; test \"\${IMAGE_VERSION:-}\" = '$expect_build_id' || test \"\${BUILD_ID:-}\" = '$expect_build_id'"
fi
for package in "${expect_packages[@]}"; do
  check "$package is installed" guest "rpm -q '$package'"
done
if [ -n "$forbid_credential_file" ]; then
  # A medium without a credential: none may appear anywhere on the system.
  scp -q -P "$port" -i "$key_dir/ssh-key" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    "$forbid_credential_file" root@127.0.0.1:/root/.luma-media-credential-check
  guest 'chmod 0600 /root/.luma-media-credential-check'
  check 'no preview credential anywhere on the installed system' guest 'test ! -e /etc/luma/update-preview-credential && hits=$(grep -rlFs -f /root/.luma-media-credential-check /etc /usr/local /var/log /var/lib /var/home /root 2>/dev/null | grep -vxF /root/.luma-media-credential-check); [ -z "$hits" ] || { echo "$hits"; exit 1; }'
  guest 'rm -f /root/.luma-media-credential-check'
fi
if [ -n "$expect_enrollment" ]; then
  guest 'luma-update status --json' >"$out/update-status-installed.json" 2>&1 || true
  if [ "$expect_enrollment" = enrolled ]; then
    check "Updates: enrolled in $MEDIA_CHANNEL and managed" python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get("preview_enrolled") and d.get("channel") == sys.argv[2] and d.get("managed") else 1)' "$out/update-status-installed.json" "$MEDIA_CHANNEL"
    # The next update is fetchable: the signed summary comes through the
    # credential-checked mirror from dl.simplyluma.com and verifies.
    check "Updates: the $MEDIA_CHANNEL repository answers through the preview credential" guest "ostree remote summary --repo=/ostree/repo luma 2>&1 | grep -Fq '$MEDIA_REF'"
  else
    check 'Updates: not enrolled, and a channel can be chosen' python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if not d.get("preview_enrolled") and (d.get("managed") or d.get("adoptable")) and d.get("available_channels") else 1)' "$out/update-status-installed.json"
  fi
fi
# The out-of-box checks act as a person would after first-boot setup: they
# start once the first boot has finished starting (systemctl
# is-system-running leaves "starting").
settle_deadline=$((SECONDS + 1200))
while [ "$SECONDS" -lt "$settle_deadline" ]; do
  state=$(guest 'systemctl is-system-running' 2>/dev/null | tr -d '\r')
  case "$state" in starting|initializing|'') sleep 10 ;; *) break ;; esac
done
guest 'systemctl list-jobs --no-pager' >"$out/first-boot-jobs.txt" 2>&1 || true
printf 'info  first boot settled as "%s" before the out-of-box checks\n' "${state:-unknown}" | tee -a "$results"
# release_check NAME LABEL [ARGS...]: tests/os/gate/NAME.sh from the release
# source, run as root in the installed system, one JSON line per check.
release_check() {
  local name=$1 label=$2 jsonl
  shift 2
  jsonl="$out/$label.jsonl"
  local check_ready=0
  if [[ "$name" =~ ^[A-Za-z0-9_-]+$ ]]; then
    if [ -n "$media_build_dir" ] && [ "${LUMA_SOURCE_DIRTY:-false}" = true ]; then
      cp -- "$release_files/tests/os/gate/$name.sh" "$vm/$name.sh" && check_ready=1
    elif git -c "safe.directory=$luma_os_repo_root" -C "$luma_os_repo_root" show "$checks_source:tests/os/gate/$name.sh" >"$vm/$name.sh" 2>/dev/null; then
      check_ready=1
    fi
  fi
  if [ "$check_ready" -ne 1 ]; then
    printf 'FAIL  %s: tests/os/gate/%s.sh is not in the release source %s\n' "$label" "$name" "${checks_source:0:12}" | tee -a "$results"
    failures=$((failures + 1))
    return
  fi
  # Keep the signed-browser script's source-owned sibling closure intact.
  if [ "$name" = default-browser ]; then
    local helper helper_ready
    for helper in browser-opens.sh browser-opens.js browser-process-proof.py; do
      helper_ready=0
      if [ -n "$media_build_dir" ] && [ "${LUMA_SOURCE_DIRTY:-false}" = true ]; then
        cp -- "$release_files/tests/os/gate/$helper" "$vm/$helper" && helper_ready=1
      elif git -c "safe.directory=$luma_os_repo_root" -C "$luma_os_repo_root" show "$checks_source:tests/os/gate/$helper" >"$vm/$helper" 2>/dev/null; then
        helper_ready=1
      fi
      if [ "$helper_ready" -ne 1 ]; then
        printf 'FAIL  %s: tests/os/gate/%s is not in the release source %s\n' "$label" "$helper" "${checks_source:0:12}" | tee -a "$results"
        failures=$((failures + 1))
        return 1
      fi
      scp -q -P "$port" -i "$key_dir/ssh-key" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        "$vm/$helper" "root@127.0.0.1:/var/tmp/$helper" || {
          printf 'FAIL  %s: could not deliver release helper %s\n' "$label" "$helper" | tee -a "$results"
          failures=$((failures + 1))
          return 1
        }
    done
  fi
  scp -q -P "$port" -i "$key_dir/ssh-key" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    "$vm/$name.sh" "root@127.0.0.1:/var/tmp/luma-check-$name.sh"
  if guest "bash /var/tmp/luma-check-$name.sh $*" >"$jsonl" 2>&1 && ! grep -q '"result": "fail"' "$jsonl"; then
    printf 'PASS  %s: %s checks (%s skipped)\n' "$label" "$(grep -c '"result": "pass"' "$jsonl")" "$(grep -c '"result": "skip"' "$jsonl")" | tee -a "$results"
  else
    printf 'FAIL  %s: %s failing: %s (see %s.jsonl)\n' "$label" "$(grep -c '"result": "fail"' "$jsonl")" \
      "$(python3 -c 'import json,sys
names=[]
for line in open(sys.argv[1]):
    try: d=json.loads(line)
    except ValueError: continue
    if d.get("result") == "fail": names.append(d.get("check", "?"))
print(", ".join(names) or "script error")' "$jsonl")" "$label" | tee -a "$results"
    failures=$((failures + 1))
  fi
}
if [ "$skip_release_checks" -eq 1 ]; then
  printf 'n/a   release checks: skipped (--skip-release-checks, interim medium)\n' | tee -a "$results"
elif [ "${#require_checks[@]}" -gt 0 ]; then
  for spec in "${require_checks[@]}"; do
    name=${spec%%:*} args=
    [ "$name" = "$spec" ] || args=${spec#*:}
    # A medium follows the channel it was built from (owner decision 2026-09-16).
    args=${args//@CHANNEL@/$MEDIA_CHANNEL}
    # shellcheck disable=SC2086
    release_check "$name" "$name" $args
  done
elif guest 'rpm -q luma-install-commands' >/dev/null 2>&1; then
  # No install hurdles (ADR-038), when the image carries luma-install-commands;
  # --quick skips the large Flatpak and Snap downloads on credential media.
  hurdles_args=--quick
  [ -n "$forbid_credential_file" ] && hurdles_args=
  # shellcheck disable=SC2086
  release_check no-install-hurdles no-install-hurdles $hurdles_args
else
  printf 'n/a   no-install-hurdles checks: this release does not carry luma-install-commands\n' | tee -a "$results"
fi
# SELinux: no denial since boot except the recorded Fedora base-policy ones
# (config/os/selinux-known-denials.txt, each with a reason and an expiry).
guest 'journalctl -b -q --no-pager _TRANSPORT=audit | grep -F "avc:  denied"' >"$out/selinux-denials.log" 2>/dev/null || true
# /var/tmp must still be the sticky world-writable directory the image ships.
# (verify-media once ran `install -d /var/tmp` in the guest, which reset it to
# 0755 and made every unprivileged Flatpak install fail until the next boot.)
check '/var/tmp is still mode 1777 owned by root' guest 'test "$(stat -c "%a %U" /var/tmp)" = "1777 root"'
check 'no SELinux denials since boot beyond the recorded Fedora base-policy ones' python3 - "$out/selinux-denials.log" "$luma_os_repo_root/config/os/selinux-known-denials.txt" <<'SELINUX'
import datetime, re, sys
log, known_file = sys.argv[1], sys.argv[2]
today = datetime.date.today().isoformat()
known = {}
for line in open(known_file):
    if not line.strip() or line.startswith("#"):
        continue
    source, comm, perm, tclass, permissive, expires = line.split()[:6]
    if today < expires:
        known[(source, comm, perm, tclass, permissive)] = expires
unexpected = []
pattern = re.compile(r'denied\s+\{ ([^}]*)\} for (?:.*?comm="([^"]*)")?.*?scontext=[^:]*:[^:]*:([^:]*):.*?tclass=(\S+) permissive=([01])')
for line in open(log):
    match = pattern.search(line)
    if not match:
        unexpected.append(line.strip())
        continue
    perms, comm, source, tclass, permissive = match.groups()
    comm = comm or "*"
    for perm in perms.split():
        if (source, comm, perm, tclass, permissive) not in known and (source, "*", perm, tclass, permissive) not in known:
            unexpected.append(line.strip())
            break
for line in unexpected:
    print(line)
sys.exit(1 if unexpected else 0)
SELINUX

if [ "$update" -eq 1 ]; then
  ref=$MEDIA_REF
  head=$(ostree rev-parse --repo="$(luma_os_channel_repo "$MEDIA_CHANNEL")" "$ref")
  head_version=$(python3 "$luma_os_repo_root/scripts/os/lib/ostree_metadata.py" version "$(luma_os_channel_repo "$MEDIA_CHANNEL")" "$head")
  credential=$(cat "$LUMA_OS_SECRETS/local-preview-test-credential")
  [ -s "$LUMA_OS_ROOT/webroot/current/os/graph/$MEDIA_CHANNEL.json" ] || luma_os_die "no signed $MEDIA_CHANNEL graph is served"
  if [ "$head" = "$MEDIA_COMMIT" ]; then
    luma_os_die "the channel head is still the medium's release; publish the next release first"
  fi
  # Rig: loopback HTTP instead of dl.simplyluma.com; everything signed as in production.
  guest "printf '%s\n' '[update]' 'graph_url = http://$gateway:8871/os/graph/{channel}.json' 'preview_repo_url = http://$gateway:8871/os/preview/{credential}/repo' 'events_url = http://$gateway:8871/os/luma-verify-events' 'allow_insecure_urls = true' > /etc/luma/update.conf && systemctl stop luma-updated.timer"
  guest "umask 077; printf '{\"credential\": \"%s\", \"channel\": \"%s\", \"channels\": [\"%s\"], \"issued_at\": %s}\n' '$credential' '$MEDIA_CHANNEL' '$MEDIA_CHANNEL' \$(date +%s) > /etc/luma/update-preview-credential && printf 'http://%s:8871/os/preview/%s/repo\n' '$gateway' '$credential' > /etc/luma/update-mirrorlist && systemctl restart luma-updated.service; true"
  guest 'luma-update check' >"$out/update-check.log" 2>&1 || true
  deadline=$((SECONDS + 1800))
  state=
  while [ "$SECONDS" -lt "$deadline" ]; do
    state=$(guest 'luma-update status --json' 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])' || true)
    case "$state" in checking|downloading|'') sleep 10 ;; *) break ;; esac
  done
  guest 'luma-update status --json' >"$out/update-status-staged.json" 2>&1 || true
  check "luma-update staged the channel head $head_version" \
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d["state"] == "staged" and d["staged_commit"] == sys.argv[2] else 1)' "$out/update-status-staged.json" "$head"
  guest 'systemctl reboot' >/dev/null 2>&1 || true
  sleep 20
  wait_ssh 900 || luma_os_die 'the updated system did not come up'
  verdict=$(greenboot_state)
  guest 'luma-update status --json' >"$out/update-status-booted.json" 2>&1 || true
  check "greenboot judged the updated boot healthy ($verdict)" test "$verdict" = healthy
  check "luma-update reports $head_version booted" \
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d["booted_commit"] == sys.argv[2] else 1)' "$out/update-status-booted.json" "$head"
fi

if [ -n "$update_to" ]; then
  # The out-of-box update path against the real origin (no rig, no credential).
  reboot_guest() {
    guest 'systemctl reboot' >/dev/null 2>&1 || true
    sleep 20
    wait_ssh 900 || { printf 'FAIL  %s: the system did not come back after restarting\n' "$1" | tee -a "$results"; failures=$((failures + 1)); return 1; }
    verdict=$(greenboot_state)
    check "greenboot judged the boot after $1 healthy ($verdict)" test "$verdict" = healthy
  }
  release_check update-out-of-box update-pick-and-stage --medium public --phase pick-and-stage --channel "$MEDIA_CHANNEL" --expect-version "$update_to"
  if ! grep -q '^FAIL  update-pick-and-stage' "$results" && reboot_guest 'staging the update'; then
    release_check update-out-of-box update-booted --medium public --phase booted --expect-version "$update_to"
    if ! grep -q '^FAIL  update-booted' "$results" && reboot_guest 'rolling back'; then
      release_check update-out-of-box update-rolled-back --medium public --phase rolled-back --expect-version "$MEDIA_VERSION"
    fi
  fi
fi

python3 "$luma_os_repo_root/scripts/os/lib/screen_image.py" capture "$domain" "$out/final.img" >/dev/null 2>&1 &&
  python3 "$luma_os_repo_root/scripts/os/lib/screen_image.py" to-png "$out/final.img" "$out/final.png" && rm -f "$out/final.img" || true
printf '\nmedia verification: %s failing checks; evidence %s\n' "$failures" "$out"
[ "$failures" -eq 0 ]
