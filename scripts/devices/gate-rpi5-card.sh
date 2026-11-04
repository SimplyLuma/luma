#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Gate a Raspberry Pi 5 hardware-target candidate: boot its preinstalled card
# image in an aarch64 VM (HVF on Apple silicon, KVM on an aarch64 Linux host)
# and write the gate-result.json that publish-channel.sh checks
# (org.projectluma.os-gate/v1).
#
#   gate-rpi5-card.sh --image CARD.img.xz --key SSH_PRIVATE_KEY --build-env build.env
#                     --export-env export.env --output gate-result.json [--work DIR]
#
# CARD is the card build-rpi5-disk-image.sh made from the candidate with
# --test-payload <candidate repo> <candidate> --test-ssh-key <KEY.pub>: the
# same commit, plus root SSH for the checks. The card is the Pi's fresh install
# (there is no installer), so the stages are:
#   fresh-install       the card image expands to a 32 GiB disk and boots
#   fresh-first-boot    SSH answers; the deployment is the candidate on the Pi ref
#   fresh-greenboot     greenboot's required checks pass, boot_success=1
#   fresh-image-contract  Luma Pi kernel, firmware-sync and no-sleep policy,
#                       update stream, offline apps imported, no failed units
#   no-account          no user account; Luma's first-boot setup is on screen
#                       and GNOME Initial Setup is not installed
# update and rollback are not-applicable only while the Pi ref has no release:
# publish-channel.sh refuses once it does, until those stages are added here.
set -euo pipefail

image="" key="" build_env="" export_env="" output="" work=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --image) image=$(realpath "${2:?}"); shift 2 ;;
    --key) key=$(realpath "${2:?}"); shift 2 ;;
    --build-env) build_env=$(realpath "${2:?}"); shift 2 ;;
    --export-env) export_env=$(realpath "${2:?}"); shift 2 ;;
    --output) output=${2:?}; shift 2 ;;
    --work) work=${2:?}; shift 2 ;;
    *) sed -n 's/^#   gate-rpi5-card.sh /usage: gate-rpi5-card.sh /p' "$0" >&2; exit 2 ;;
  esac
done
[ -n "$image" ] && [ -n "$key" ] && [ -n "$build_env" ] && [ -n "$export_env" ] && [ -n "$output" ] || {
  sed -n 's/^#   gate-rpi5-card.sh /usage: gate-rpi5-card.sh /p' "$0" >&2; exit 2; }
value() { sed -n "s/^$2=//p" "$1" | head -n 1; }
candidate=$(value "$export_env" LUMA_EXPORT_CANDIDATE)
ref=$(value "$export_env" LUMA_EXPORT_REF)
kernel=$(value "$export_env" LUMA_EXPORT_KERNEL)
previous=$(value "$export_env" LUMA_EXPORT_PARENT)
build_id=$(value "$build_env" LUMA_BUILD_ID)
channel=$(value "$build_env" LUMA_OS_CHANNEL)
source_revision=$(value "$build_env" LUMA_SOURCE_REVISION)
[[ "$candidate" =~ ^[0-9a-f]{64}$ ]] || { echo "no candidate in $export_env" >&2; exit 1; }
[[ "$ref" == */raspberrypi-rpi5/* ]] || { echo "$ref is not a Raspberry Pi 5 ref" >&2; exit 1; }
[ -z "$previous" ] || { echo "the Pi ref has a release ($previous): update and rollback stages are required and not implemented" >&2; exit 1; }

case "$(uname -s)" in
  Darwin) accel=hvf; firmware=$(brew --prefix qemu 2>/dev/null || echo /opt/homebrew)/share/qemu/edk2-aarch64-code.fd ;;
  Linux) accel=kvm; firmware=/usr/share/edk2/aarch64/QEMU_EFI-silent-pflash.raw ;;
esac
[ "$(uname -m)" = arm64 ] || [ "$(uname -m)" = aarch64 ] || { echo "run on an aarch64 host" >&2; exit 1; }
work=${work:-$(mktemp -d "${TMPDIR:-/tmp}/luma-rpi5-gate.XXXXXX")}
mkdir -p "$work"
port=$((22000 + RANDOM % 2000))
qemu_pid=""
cleanup() { [ -z "$qemu_pid" ] || kill "$qemu_pid" 2>/dev/null || true; rm -f "$work/disk.img" "$work/vars.fd"; }
trap cleanup EXIT INT TERM

stages="$work/stages.jsonl"; : >"$stages"
stage() {  # NAME RESULT DETAIL
  python3 -c 'import json,sys,datetime; print(json.dumps({"at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "stage": sys.argv[1], "result": sys.argv[2], "detail": sys.argv[3]}))' "$@" >>"$stages"
  printf '%-22s %s  %s\n' "$1" "$2" "$3"
}
vm() { ssh -i "$key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  -o LogLevel=ERROR -o ConnectTimeout=10 -p "$port" root@127.0.0.1 "$@"; }
finish() {
  python3 - "$stages" "$output" "$build_id" "$channel" "$source_revision" "$candidate" "$ref" "$previous" "$port" <<'PY'
import json, sys, datetime
stages_file, out, build_id, channel, revision, commit, ref, previous, _ = sys.argv[1:]
stages = [json.loads(line) for line in open(stages_file)]
ok = lambda *names: "pass" if names and all(any(s["stage"] == n and s["result"] == "pass" for s in stages) for n in names) else "fail"
fresh = ok("fresh-install", "fresh-first-boot", "fresh-greenboot", "fresh-image-contract")
no_account = ok("no-account")
now = datetime.datetime.now(datetime.timezone.utc)
gate = {"schema": "org.projectluma.os-gate/v1", "build_id": build_id, "channel": channel,
        "checks_revision": revision, "source_revision": revision, "commit": commit, "ref": ref,
        "previous_commit": previous, "hardware_target": "raspberrypi,rpi5",
        "run_id": now.strftime("%Y%m%dT%H%M%SZ"), "completed_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fresh": fresh, "no_account": no_account, "update": "not-applicable", "rollback": "not-applicable",
        "result": "pass" if fresh == no_account == "pass" else "fail", "stages": stages}
json.dump(gate, open(out, "w"), indent=2, sort_keys=True)
print(f"gate {gate['result']}: {out}")
sys.exit(0 if gate["result"] == "pass" else 1)
PY
}
fail_now() { stage "$1" fail "$2"; finish || true; exit 1; }

# fresh-install: the card image on a 32 GiB sparse disk (the first boot grows
# the root partition and imports the offline apps).
python3 - "$image" "$work/disk.img" <<'PY'
import lzma, sys
src, dst = sys.argv[1:]
block = 1 << 20
with lzma.open(src) as i, open(dst, "wb") as o:
    while chunk := i.read(block):
        if chunk.count(0) == len(chunk):
            o.seek(len(chunk), 1)
        else:
            o.write(chunk)
    o.truncate(32 << 30)
PY
cp "$(dirname "$firmware")/edk2-arm-vars.fd" "$work/vars.fd" 2>/dev/null || dd if=/dev/zero of="$work/vars.fd" bs=1048576 count=64 2>/dev/null
qemu-system-aarch64 -M virt -accel "$accel" -cpu host -m 6G -smp 4 \
  -drive if=pflash,format=raw,readonly=on,file="$firmware" -drive if=pflash,format=raw,file="$work/vars.fd" \
  -drive if=virtio,format=raw,file="$work/disk.img" -device virtio-gpu-pci -device qemu-xhci -device usb-kbd \
  -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$port-:22 \
  -display none -serial file:"$work/serial.log" -qmp unix:"$work/qmp.sock",server,nowait &
qemu_pid=$!
stage fresh-install pass "card image $(basename "$image") on a 32 GiB disk, UEFI boot"

for _ in $(seq 1 90); do vm true 2>/dev/null && break; sleep 10; done
vm true 2>/dev/null || fail_now fresh-first-boot "no SSH within 15 minutes (serial: $work/serial.log)"
booted=$(vm "rpm-ostree status --booted --json" | python3 -c 'import json,sys; d=[x for x in json.load(sys.stdin)["deployments"] if x.get("booted")][0]; print(d["checksum"], d["origin"])')
[ "$booted" = "$candidate luma:$ref" ] || fail_now fresh-first-boot "booted $booted, expected $candidate luma:$ref"
stage fresh-first-boot pass "booted $candidate from luma:$ref"

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
contract=$(vm "kernel='$kernel'" 'bash -s' <<'EOF'
problems=()
[ "$(uname -r)" = "$kernel" ] || problems+=("kernel $(uname -r), expected $kernel")
rpm -q kernel-luma-rpi luma-rpi5-platform luma-rpi-bluetooth-firmware >/dev/null || problems+=("Pi packages missing")
! rpm -q kernel-core >/dev/null 2>&1 || problems+=("Fedora kernel-core is installed")
[ "$(busctl call org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager CanSuspend)" = 's "no"' ] ||
  problems+=("logind still offers suspend")
grep -qx 'hardware_target = raspberrypi,rpi5' /etc/luma/update.conf || problems+=("no Pi update stream")
[ -f /usr/lib/systemd/system/luma-rpi5-firmware-sync.service ] || problems+=("no firmware-sync unit")
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
[[ "$account" == PASS* ]] || fail_now no-account "${account#FAIL }"
stage no-account pass "${account#PASS } (screen: $work/first-boot.png)"
finish
