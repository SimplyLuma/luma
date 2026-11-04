#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Gate a Raspberry Pi 5 card: boot the preinstalled card image in an aarch64
# VM (HVF on Apple silicon, KVM on an aarch64 Linux host) and write
# gate-result.json (org.projectluma.device-card-gate/v1).
#
#   gate-rpi5-card.sh (--image CARD.img[.xz] | --disk-url nbd://HOST:PORT) --key SSH_PRIVATE_KEY
#                     --manifest CARD.manifest.txt --device-repo DIR --output gate-result.json
#                     [--expect-commit COMMIT] [--work DIR]
#
# --disk-url boots a disk served elsewhere (the expanded card, raw or a qcow2
# overlay, 32 GiB, e.g. qemu-nbd on the build host behind an SSH tunnel) so
# the gate host needs no local disk space.
#
# CARD is a card build-rpi5-disk-image.sh made with --device-repo and
# --test-payload <repo> <commit> --test-ssh-key <KEY.pub>: the commit and
# override the release card carries, plus root SSH for the checks. --manifest
# is that card's manifest; --expect-commit is the signed generic commit the
# release card is built from. --device-repo is the signed Raspberry Pi 5
# device repository the card was built against; the gate serves it to the VM
# for the update stage. The stages:
#   fresh-install       the card image expands to a 32 GiB disk and boots
#   fresh-first-boot    SSH answers; the deployment is the generic commit on
#                       the generic ARM ref with the kernel override, and
#                       names no Raspberry Pi hardware ref
#   fresh-greenboot     greenboot's required checks pass, boot_success=1
#   fresh-image-contract  Luma Pi kernel, no Fedora kernel, the card's Pi
#                       packages, no hardware-targeted update.conf, the device
#                       repository and key, offline apps, no failed units
#   no-account          no user account; Luma's first-boot setup is on screen
#                       and GNOME Initial Setup is not installed
#   override-update     an rpm-ostree upgrade to a newer commit (a local child
#                       of the generic commit with the same tree) reapplies the
#                       override from the device repository; the VM reboots
#                       into it on the Luma Pi kernel and greenboot passes
set -euo pipefail

image="" disk_url="" key="" manifest="" device_repo="" expect_commit="" output="" work=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --image) image=$(realpath "${2:?}"); shift 2 ;;
    --disk-url) disk_url=${2:?}; shift 2 ;;
    --key) key=$(realpath "${2:?}"); shift 2 ;;
    --manifest) manifest=$(realpath "${2:?}"); shift 2 ;;
    --device-repo) device_repo=$(realpath "${2:?}"); shift 2 ;;
    --expect-commit) expect_commit=${2:?}; shift 2 ;;
    --output) output=${2:?}; shift 2 ;;
    --work) work=${2:?}; shift 2 ;;
    *) sed -n 's/^#   gate-rpi5-card.sh /usage: gate-rpi5-card.sh /p' "$0" >&2; exit 2 ;;
  esac
done
{ [ -n "$image" ] || [ -n "$disk_url" ]; } && [ -n "$key" ] && [ -n "$manifest" ] && [ -n "$device_repo" ] && [ -n "$output" ] || {
  sed -n 's/^#   gate-rpi5-card.sh /usage: gate-rpi5-card.sh /p' "$0" >&2; exit 2; }
field() { awk -v k="$1" '$1 == k { sub(/^[^ ]+ /, ""); print; exit }' "$manifest"; }
candidate=$(field commit)
origin=$(field origin)
kernel=$(field kernel)
channel=$(field channel)
version=$(field version)
card=$(field image)
override=$(field override)
locals=$(awk '$1 == "local_package" {print $2}' "$manifest" | sed -E 's/-[^-]+-[^-]+\.(noarch|aarch64)\.rpm$//' | tr '\n' ' ')
locals="$locals $(printf '%s\n' "$override" | sed -n 's/.*requested=\([^ ]*\).*/\1/p' | tr ';' ' ')"
ref=${origin#*:}
[[ "$candidate" =~ ^[0-9a-f]{64}$ ]] || { echo "no commit in $manifest" >&2; exit 1; }
[ -z "$expect_commit" ] || [ "$candidate" = "$expect_commit" ] || { echo "the card carries $candidate, not $expect_commit" >&2; exit 1; }
[[ "$ref" =~ ^luma/[0-9]+/aarch64/$channel$ ]] || { echo "$origin is not the generic ARM $channel ref" >&2; exit 1; }
[ -n "$override" ] || { echo "the card has no kernel override" >&2; exit 1; }
[ -f "$device_repo/repodata/repomd.xml" ] || { echo "$device_repo is not an rpm-md repository" >&2; exit 1; }

case "$(uname -s)" in
  Darwin) accel=hvf; firmware=$(brew --prefix qemu 2>/dev/null || echo /opt/homebrew)/share/qemu/edk2-aarch64-code.fd ;;
  Linux) accel=kvm; firmware=/usr/share/edk2/aarch64/QEMU_EFI-silent-pflash.raw ;;
esac
[ "$(uname -m)" = arm64 ] || [ "$(uname -m)" = aarch64 ] || { echo "run on an aarch64 host" >&2; exit 1; }
work=${work:-$(mktemp -d "${TMPDIR:-/tmp}/luma-rpi5-gate.XXXXXX")}
mkdir -p "$work"
port=$((22000 + RANDOM % 2000))
repo_port=$((24000 + RANDOM % 2000))
qemu_pid="" http_pid=""
cleanup() {
  [ -z "$qemu_pid" ] || kill "$qemu_pid" 2>/dev/null || true
  [ -z "$http_pid" ] || kill "$http_pid" 2>/dev/null || true
  rm -f "$work/disk.img" "$work/vars.fd"
}
trap cleanup EXIT INT TERM

stages="$work/stages.jsonl"; : >"$stages"
stage() {  # NAME RESULT DETAIL
  python3 -c 'import json,sys,datetime; print(json.dumps({"at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "stage": sys.argv[1], "result": sys.argv[2], "detail": sys.argv[3]}))' "$@" >>"$stages"
  printf '%-22s %s  %s\n' "$1" "$2" "$3"
}
vm() { ssh -i "$key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  -o LogLevel=ERROR -o ConnectTimeout=10 -p "$port" root@127.0.0.1 "$@"; }
finish() {
  python3 - "$stages" "$output" "$card" "$channel" "$candidate" "$origin" "$kernel" "$version" <<'PY'
import json, sys, datetime
stages_file, out, card, channel, commit, origin, kernel, version = sys.argv[1:]
stages = [json.loads(line) for line in open(stages_file)]
ok = lambda *names: "pass" if names and all(any(s["stage"] == n and s["result"] == "pass" for s in stages) for n in names) else "fail"
fresh = ok("fresh-install", "fresh-first-boot", "fresh-greenboot", "fresh-image-contract")
no_account = ok("no-account")
update = ok("override-update")
now = datetime.datetime.now(datetime.timezone.utc)
gate = {"schema": "org.projectluma.device-card-gate/v1", "device": "raspberrypi,rpi5", "card": card,
        "channel": channel, "commit": commit, "version": version, "origin": origin, "kernel": kernel,
        "run_id": now.strftime("%Y%m%dT%H%M%SZ"), "completed_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fresh": fresh, "no_account": no_account, "override_update": update,
        "result": "pass" if fresh == no_account == update == "pass" else "fail", "stages": stages}
json.dump(gate, open(out, "w"), indent=2, sort_keys=True)
print(f"gate {gate['result']}: {out}")
sys.exit(0 if gate["result"] == "pass" else 1)
PY
}
fail_now() { stage "$1" fail "$2"; finish || true; exit 1; }
# One deployment's base commit, origin, requested packages, removed base
# packages and checksum (WHICH: booted, or 0 for the default deployment).
deployment() {
  vm "rpm-ostree status --json" | python3 -c '
import json, sys
rows = json.load(sys.stdin)["deployments"]
d = rows[0] if sys.argv[1] == "0" else [x for x in rows if x.get(sys.argv[1])][0]
print(d.get("base-checksum") or d["checksum"], d["origin"], ",".join(d.get("requested-packages") or []) or "-",
      ",".join(sorted(d.get("base-removals") or [])) or "-", d["checksum"])' "$1"
}
override_ok() {  # ORIGIN REQUESTED REMOVALS
  [ "$1" = "$origin" ] && [[ "$1" != *raspberrypi-rpi5* ]] && [[ ",$2," == *,kernel-luma-rpi,* ]] && [[ "$3" == *kernel-core* ]]
}

# fresh-install: the card image on a 32 GiB sparse disk (the first boot grows
# the root partition and imports the offline apps).
if [ -n "$disk_url" ]; then
  drive="file=$disk_url,format=raw,if=virtio,cache=writeback"
  disk_detail="served disk $disk_url"
else
python3 - "$image" "$work/disk.img" <<'PY'
import lzma, sys
src, dst = sys.argv[1:]
block = 1 << 20
with (lzma.open(src) if src.endswith(".xz") else open(src, "rb")) as i, open(dst, "wb") as o:
    while chunk := i.read(block):
        if chunk.count(0) == len(chunk):
            o.seek(len(chunk), 1)
        else:
            o.write(chunk)
    o.truncate(32 << 30)
PY
  drive="if=virtio,format=raw,file=$work/disk.img"
  disk_detail="card image $(basename "$image") on a 32 GiB disk"
fi
cp "$(dirname "$firmware")/edk2-arm-vars.fd" "$work/vars.fd" 2>/dev/null || dd if=/dev/zero of="$work/vars.fd" bs=1048576 count=64 2>/dev/null
qemu-system-aarch64 -M virt -accel "$accel" -cpu host -m 6G -smp 4 \
  -drive if=pflash,format=raw,readonly=on,file="$firmware" -drive if=pflash,format=raw,file="$work/vars.fd" \
  -drive "$drive" -device virtio-gpu-pci -device qemu-xhci -device usb-kbd \
  -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$port-:22 \
  -display none -serial file:"$work/serial.log" -qmp unix:"$work/qmp.sock",server,nowait &
qemu_pid=$!
stage fresh-install pass "$disk_detail, UEFI boot"

for _ in $(seq 1 90); do vm true 2>/dev/null && break; sleep 10; done
vm true 2>/dev/null || fail_now fresh-first-boot "no SSH within 15 minutes (serial: $work/serial.log)"
read -r base got_origin requested removals _ <<<"$(deployment booted)"
[ "$base" = "$candidate" ] && override_ok "$got_origin" "$requested" "$removals" ||
  fail_now fresh-first-boot "booted base $base origin $got_origin requested $requested removed $removals; expected $candidate on $origin with kernel-luma-rpi replacing Fedora's kernel"
stage fresh-first-boot pass "booted $candidate from $origin; override: kernel-luma-rpi, removed ${removals//,/ }"

for _ in $(seq 1 60); do
  [ "$(vm systemctl is-active greenboot-healthcheck 2>/dev/null)" = activating ] || break; sleep 10
done
state=$(vm systemctl is-active greenboot-healthcheck boot-complete.target | tr '\n' ' ')
env=$(vm grub2-editenv list 2>/dev/null | tr '\n' ' ')
[ "$state" = "active active " ] && [[ "$env" == *boot_success=1* ]] ||
  fail_now fresh-greenboot "greenboot-healthcheck/boot-complete: $state; grubenv: $env"
stage fresh-greenboot pass "greenboot judged the first boot healthy (boot_success=1)"

for _ in $(seq 1 60); do
  [ "$(vm systemctl is-active luma-app-baseline 2>/dev/null)" = activating ] || break; sleep 10
done
contract=$(vm "kernel='$kernel' locals='$locals'" 'bash -s' <<'EOF'
problems=()
[ "$(uname -r)" = "$kernel" ] || problems+=("kernel $(uname -r), expected $kernel")
for name in kernel-luma-rpi $locals; do rpm -q "$name" >/dev/null || problems+=("$name is not installed"); done
! rpm -q kernel-core >/dev/null 2>&1 || problems+=("Fedora kernel-core is installed")
if [ -e /etc/luma/update.conf ] && grep -Eq '^\s*(hardware_target\s*=\s*\S|ref_template\s*=.*raspberrypi)' /etc/luma/update.conf; then
  problems+=("update.conf names a hardware target")
fi
grep -q '^baseurl=https://' /etc/yum.repos.d/luma-raspberrypi-rpi5.repo 2>/dev/null || problems+=("no Raspberry Pi 5 device repository")
[ -s /etc/pki/rpm-gpg/RPM-GPG-KEY-luma-os-release ] || problems+=("no release key for the device repository")
grep -qx repo_gpgcheck=1 /etc/yum.repos.d/luma-raspberrypi-rpi5.repo || problems+=("device repository metadata is not signature-checked")
if rpm -q luma-rpi5-platform >/dev/null 2>&1; then
  [ "$(busctl call org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager CanSuspend)" = 's "no"' ] ||
    problems+=("logind still offers suspend")
  [ -f /usr/lib/systemd/system/luma-rpi5-firmware-sync.service ] || problems+=("no firmware-sync unit")
fi
for unit in luma-app-baseline gdm; do
  [ "$(systemctl is-active $unit)" = active ] || problems+=("$unit is $(systemctl is-active $unit)")
done
failed=$(systemctl --failed --no-legend --plain | awk '{print $1}' | tr '\n' ' ')
[ -z "$failed" ] || problems+=("failed units: $failed")
apps=$(flatpak list --app --columns=application 2>/dev/null | wc -l)
[ "$apps" -gt 0 ] || problems+=("no offline apps imported")
if [ "${#problems[@]}" -eq 0 ]; then echo "PASS $apps apps, kernel $(uname -r)"; else printf 'FAIL %s; ' "${problems[@]}"; echo; fi
EOF
)
[[ "$contract" == PASS* ]] || fail_now fresh-image-contract "${contract#FAIL }"
stage fresh-image-contract pass "${contract#PASS }"

account=$(vm 'bash -s' <<'EOF'
problems=()
users=$(awk -F: '$3 >= 1000 && $3 < 60000 {print $1}' /etc/passwd | tr '\n' ' ')
[ -z "$users" ] || problems+=("accounts exist: $users")
rpm -q gnome-initial-setup >/dev/null 2>&1 && problems+=("gnome-initial-setup is installed")
rpm -q luma-setup >/dev/null || problems+=("luma-setup is not installed")
for _ in $(seq 1 30); do pgrep -f 'libexec/luma-setup( |$)' >/dev/null && break; sleep 5; done
pgrep -f 'libexec/luma-setup( |$)' >/dev/null || problems+=("luma-setup is not running")
if [ "${#problems[@]}" -eq 0 ]; then echo "PASS no account; luma-setup on screen"; else printf 'FAIL %s; ' "${problems[@]}"; echo; fi
EOF
)
python3 - "$work/qmp.sock" "$work/first-boot.png" <<'PY' || true
import json, socket, sys
s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); f = s.makefile("rw"); f.readline()
for cmd in ({"execute": "qmp_capabilities"}, {"execute": "screendump", "arguments": {"filename": sys.argv[2], "format": "png"}}):
    f.write(json.dumps(cmd) + "\n"); f.flush(); f.readline()
PY
# A failed no-account stage fails the gate but does not stop the update stage.
if [[ "$account" == PASS* ]]; then stage no-account pass "${account#PASS } (screen: $work/first-boot.png)"
else stage no-account fail "${account#FAIL } (screen: $work/first-boot.png)"; fi

# override-update: a newer commit (a local child of the generic commit with
# the same tree; the remote ref moves to it), the device repository served by
# this host, then the rpm-ostree upgrade a Pi runs. The override must be
# reapplied and the Pi kernel must boot.
python3 -m http.server --bind 127.0.0.1 --directory "$device_repo" "$repo_port" >"$work/http.log" 2>&1 &
http_pid=$!
staged=$(vm "candidate='$candidate' ref='$ref' repo_port='$repo_port'" 'bash -s' <<'EOF' 2>&1
set -e
sed -i "s|^baseurl=.*|baseurl=http://10.0.2.2:$repo_port/|" /etc/yum.repos.d/luma-raspberrypi-rpi5.repo
new=$(unshare -m sh -c 'mount -o remount,rw /sysroot && ostree commit --repo=/ostree/repo --orphan \
  --tree=ref="$1" --parent="$1" -s "card gate update" --add-metadata-string=version=gate-update' _ "$candidate")
unshare -m sh -c 'mount -o remount,rw /sysroot && ostree refs --repo=/ostree/repo --delete "luma:$1" &&
  ostree refs --repo=/ostree/repo --create="luma:$1" "$2"' _ "$ref" "$new"
rpm-ostree refresh-md >/dev/null
rpm-ostree upgrade --cache-only >/tmp/upgrade.log 2>&1 || { tail -n 5 /tmp/upgrade.log; exit 1; }
echo "$new"
EOF
) || true
new_commit=$(printf '%s\n' "$staged" | tail -n 1)
[[ "$new_commit" =~ ^[0-9a-f]{64}$ ]] || fail_now override-update "upgrade failed: $(printf '%s' "$staged" | tr '\n' ' ')"
read -r base got_origin requested removals staged_checksum <<<"$(deployment 0)"
[ "$base" = "$new_commit" ] && override_ok "$got_origin" "$requested" "$removals" ||
  fail_now override-update "staged base $base origin $got_origin requested $requested removed $removals"
vm 'systemctl reboot' >/dev/null 2>&1 || true
sleep 30
for _ in $(seq 1 60); do vm true 2>/dev/null && break; sleep 10; done
read -r base _ _ _ booted_checksum <<<"$(deployment booted)"
[ "$booted_checksum" = "$staged_checksum" ] && [ "$base" = "$new_commit" ] ||
  fail_now override-update "after the reboot the booted deployment is $booted_checksum (base $base), not the update"
for _ in $(seq 1 60); do
  [ "$(vm systemctl is-active greenboot-healthcheck 2>/dev/null)" = activating ] || break; sleep 10
done
after=$(vm 'uname -r; systemctl is-active greenboot-healthcheck; rpm -q kernel-core >/dev/null 2>&1 && echo fedora-kernel || echo no-fedora-kernel' | tr '\n' ' ')
[ "$after" = "$kernel active no-fedora-kernel " ] ||
  fail_now override-update "after the update: $after (expected $kernel, greenboot active, no Fedora kernel)"
stage override-update pass "rpm-ostree upgrade to $new_commit reapplied the override; rebooted on $kernel, greenboot passed"
finish
