#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Exercise scripts/update/migrate-to-channel.sh on a disposable VM that runs a
# private (non-Luma) ref with local changes, never on a real machine.
#
#   tests/os/migration-vm-test.sh [--channel nightly] [--keep-vm]
#
# 1. Installs the channel head's parent release, recommitted unsigned on a
#    private remote and ref (luma-private-test:luma/private/migration-test),
#    with Fedora's installer: a stand-in for a machine on a private parity ref.
# 2. Adds local changes like the ones owner machines carry: a layered
#    repository package the release also contains
#    (gnome-shell-extension-appindicator), one it does not (htop), a base
#    removal of a package the release no longer ships (gnome-characters, when
#    the parent release still has it), and a grub.cfg without greenboot's
#    counter.
# 3. Runs the migration dry run (must change nothing), then --apply against the
#    local loopback content server through a relay inside the VM (so the
#    script's local-test HTTP allowance applies), restarts, and checks: booted
#    commit is the channel head with origin luma:<ref>; htop kept and the
#    appindicator layer dropped; greenboot's boot counter in grub.cfg; the
#    remote world-readable with the credential only in root-only files; /var
#    data intact; greenboot healthy.
#
# Evidence: $LUMA_OS_ROOT/migration-tests/<UTC time>/.

set -euo pipefail
. "$(dirname -- "$0")/../../scripts/os/lib/common.sh"

channel=nightly
keep=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --channel) channel=${2:?}; shift 2 ;;
    --keep-vm) keep=1; shift ;;
    *) printf 'usage: %s [--channel C] [--keep-vm]\n' "$0" >&2; exit 2 ;;
  esac
done
luma_os_require_root
luma_os_check_host
luma_os_vm_lock
luma_os_check_space 25
iso="$LUMA_OS_ROOT/vm/media/Fedora-Silverblue-ostree-x86_64-44-1.7.iso"
ref=$(luma_os_channel_ref "$channel")
repo=$(luma_os_channel_repo "$channel")
head=$(ostree rev-parse --repo="$repo" "$ref")
credential=$(cat "$LUMA_OS_SECRETS/local-preview-test-credential")
curl --fail --silent "http://127.0.0.1:8871/os/preview/$credential/repo/config" >/dev/null ||
  luma_os_die 'the loopback content server does not serve the preview repository'

run=$(date -u +%Y%m%dT%H%M%SZ)
out="$LUMA_OS_ROOT/migration-tests/$run"
vm="$LUMA_OS_ROOT/vm/migration-$run"
install -d -m 0755 "$out" "$vm"
exec > >(tee -a "$out/test.log") 2>&1
key_dir="/root/.ssh/luma-os-migration-$run"
install -d -m 0700 "$key_dir"
ssh-keygen -q -t ed25519 -N '' -f "$key_dir/ssh-key"
domain="luma-os-migration-$run"
port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
ssh_opts=(-q -p "$port" -i "$key_dir/ssh-key" -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)
guest() { ssh "${ssh_opts[@]}" root@127.0.0.1 "$@"; }
wait_ssh() { local d=$((SECONDS + ${1:-900})); while [ "$SECONDS" -lt "$d" ]; do guest true 2>/dev/null && return 0; sleep 5; done; return 1; }
failures=0
check() { local d=$1; shift; if "$@" >>"$out/check-output.log" 2>&1; then echo "PASS  $d"; else echo "FAIL  $d"; failures=$((failures + 1)); fi; }
cleanup() {
  local status=$?
  set +e
  virsh destroy "$domain" >/dev/null 2>&1
  luma_os_podman stop --time 2 "luma-os-migration-http-$run" >/dev/null 2>&1
  if [ "$keep" -eq 0 ]; then
    virsh undefine "$domain" --nvram >/dev/null 2>&1
    rm -rf "$vm" "$key_dir"
  fi
  exit "$status"
}
trap cleanup EXIT

# The private stand-in: the channel head's parent release, recommitted
# unsigned on a private ref in a private repository, like a locally composed
# parity deployment.
base=$(ostree rev-parse --repo="$repo" "$head^")
private="$vm/private-repo"
ostree init --repo="$private" --mode=archive >/dev/null
ostree pull-local --repo="$private" "$repo" "$base" >/dev/null
ostree commit --repo="$private" --branch=luma/private/migration-test --tree="ref=$base" \
  --add-metadata-string=version=private-migration-test --subject='private stand-in' >/dev/null
ostree summary --repo="$private" --update
http_port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
http_name="luma-os-migration-http-$run"
luma_os_podman run --detach --rm --name "$http_name" --network host --security-opt label=disable \
  --volume "$private:/srv/repo:ro" "$(luma_os_tools_image)" \
  python3 -m http.server --bind 127.0.0.1 --directory /srv/repo "$http_port" >/dev/null
# passt maps the guest's default gateway to this host's loopback.
gateway=$(ip -4 route show default | awk '{ print $3; exit }')

cat >"$vm/private.ks" <<EOF
text
lang en_US.UTF-8
keyboard us
timezone UTC --utc
network --bootproto=dhcp --device=link --activate --hostname=luma-migration-test
zerombr
clearpart --all --initlabel --disklabel=gpt
autopart --type=btrfs --noswap
bootloader --timeout=3
rootpw --lock
# A machine in use has a person's account, so first-boot setup does not run.
user --name=migration-test --lock
firstboot --disable
sshkey --username=root "$(cat "$key_dir/ssh-key.pub")"
services --enabled=sshd
xconfig --startxonboot
poweroff
ostreesetup --osname=luma --remote=luma-private-test --url=http://$gateway:$http_port --ref=luma/private/migration-test --nogpg
%post --nochroot
# Test machine only. The installer's fstab entry for / stays: machines being
# migrated have it, and the migration retires it.
chroot "\${ANA_INSTALL_PATH:-/mnt/sysroot}" systemctl set-default graphical.target
%end
EOF
qemu-img create -q -f qcow2 "$vm/disk.qcow2" 48G
luma_os_log "installing $base on a private ref as the pre-migration machine"
virt-install --connect qemu:///system --name "$domain" \
  --memory 6144 --vcpus 4 --cpu host-passthrough --machine q35 --boot uefi \
  --disk "path=$vm/disk.qcow2,format=qcow2,bus=virtio,cache=unsafe,discard=unmap" \
  --location "$iso,kernel=images/pxeboot/vmlinuz,initrd=images/pxeboot/initrd.img" \
  --initrd-inject "$vm/private.ks" \
  --extra-args "inst.ks=file:/private.ks inst.stage2=hd:LABEL=Fedora-SB-ostree-x86_64-44 inst.text console=ttyS0" \
  --network "passt,portForward0.proto=tcp,portForward0.range0.start=$port,portForward0.range0.to=22" \
  --graphics vnc,listen=127.0.0.1 --video virtio --rng /dev/urandom \
  --serial "file,path=/var/log/libvirt/qemu/$domain-serial.log" \
  --os-variant fedora-unknown --noreboot --noautoconsole --wait 60 >"$out/virt-install.log" 2>&1 ||
  { cp "/var/log/libvirt/qemu/$domain-serial.log" "$out/" 2>/dev/null; luma_os_die 'private stand-in install failed'; }
luma_os_podman stop --time 2 "$http_name" >/dev/null 2>&1 || true
virsh start "$domain" >/dev/null
wait_ssh 900 || luma_os_die 'private stand-in VM did not come up'

luma_os_log 'adding local changes'
# A layer the release already contains (recorded inactive, as on machines
# whose layer later entered the base), a layer it does not, a base removal,
# and a bootloader configuration from before greenboot.
# Releases before the image pinned $releasever cannot layer from Fedora's
# repositories (a private parity machine reports Fedora's own VERSION_ID).
guest 'test -s /etc/dnf/vars/releasever || { . /usr/lib/os-release; echo "${PLATFORM_ID#platform:f}" > /etc/dnf/vars/releasever; }'
guest 'rpm-ostree install --idempotent --allow-inactive gnome-shell-extension-appindicator htop' >"$out/local-changes.log" 2>&1 ||
  luma_os_die 'could not add local changes (network to Fedora mirrors?)'
# Characters left every image on 2026-09-22. A parent release from before then
# still has it: remove it there, as owner machines did. From a parent without
# it, the removal would be a no-op that rpm-ostree refuses, so skip it.
removed_characters=0
if guest 'rpm -q gnome-characters' >/dev/null 2>&1; then
  guest 'rpm-ostree override remove gnome-characters' >>"$out/local-changes.log" 2>&1 ||
    luma_os_die 'could not remove gnome-characters from the stand-in base'
  removed_characters=1
fi
# bootupd writes the counter as its own section of grub.cfg; a machine whose
# bootloader predates greenboot has no such section.
guest 'python3 - && ! grep -q boot_counter /boot/grub2/grub.cfg' >>"$out/local-changes.log" 2>&1 <<'PY' ||
import re
path = "/boot/grub2/grub.cfg"
text = open(path, encoding="utf-8").read()
new, count = re.subn(r"(?s)### BEGIN 08_greenboot\.cfg ###.*?### END 08_greenboot\.cfg ###\n?", "", text)
if count != 1:
    raise SystemExit(f"expected one greenboot section in {path}, found {count}")
open(path, "w", encoding="utf-8").write(new)
PY
  luma_os_die 'could not remove the boot counter from the stand-in grub.cfg'
guest 'systemctl reboot' >/dev/null 2>&1 || true
sleep 20
wait_ssh 900
guest 'install -d /var/home/migration-test && head -c 1048576 /dev/urandom > /var/home/migration-test/marker && cd /var/home/migration-test && sha256sum marker > marker.sha256; rpm-ostree status' >"$out/before.txt"

scp -q -P "$port" -i "$key_dir/ssh-key" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  "$luma_os_repo_root/scripts/update/migrate-to-channel.sh" "$luma_os_repo_root/scripts/update/migration_plan.py" \
  "$LUMA_OS_KEYS/luma-os-release.asc" root@127.0.0.1:/var/tmp/
# The channel through a relay inside the VM: its 127.0.0.1:8871 forwards to
# this host's loopback content server, which passt exposes at the VM's default
# gateway. The URL stays a loopback one, so the script's local-test HTTP
# allowance applies.
tunnel_url="http://127.0.0.1:8871/os/preview/$credential/repo"
cat >"$vm/luma-relay.py" <<'PY'
import asyncio, sys
upstream = sys.argv[1]
async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except OSError:
        pass
    finally:
        writer.close()
async def handle(reader, writer):
    up_reader, up_writer = await asyncio.open_connection(upstream, 8871)
    await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))
async def main():
    server = await asyncio.start_server(handle, "127.0.0.1", 8871)
    async with server:
        await server.serve_forever()
asyncio.run(main())
PY
scp -q -P "$port" -i "$key_dir/ssh-key" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  "$vm/luma-relay.py" root@127.0.0.1:/var/tmp/
# Started from the SSH session: socket units and sshd forwards could not
# listen on that port in the guest.
guest "setsid -f python3 /var/tmp/luma-relay.py $gateway >/var/tmp/luma-relay.log 2>&1 </dev/null"
for _ in $(seq 1 20); do
  guest "curl --fail --silent --output /dev/null '$tunnel_url/config'" && break
  sleep 1
done
guest "curl --fail --silent --output /dev/null '$tunnel_url/summary.sig'" ||
  luma_os_die 'the VM cannot reach the loopback content server through its relay'
before_status=$(guest 'rpm-ostree status --json | sha256sum')
guest "bash /var/tmp/migrate-to-channel.sh --channel $channel --url '$tunnel_url' --release-key /var/tmp/luma-os-release.asc" >"$out/dry-run.log" 2>&1 || true
sed "s/$credential/<credential>/g" -i "$out/dry-run.log"
check 'the dry run prints a plan' grep -q 'Luma channel migration plan' "$out/dry-run.log"
check 'the dry run changes nothing' test "$(guest 'rpm-ostree status --json | sha256sum')" = "$before_status"
check 'the plan drops the appindicator layer the release contains' grep -q 'drop layer: gnome-shell-extension-appindicator' "$out/dry-run.log"
check 'the plan keeps htop' grep -q 'keep layer: htop' "$out/dry-run.log"
check 'the plan retires the fstab root entry' grep -q 'comment out the / entry in /etc/fstab' "$out/dry-run.log"
# The release no longer ships Characters: there is nothing to re-remove.
check 'the plan does not re-remove gnome-characters' bash -c "! grep -q 're-remove: gnome-characters' '$out/dry-run.log'"

guest "umask 077; printf '%s\n' '$credential' > /root/preview-credential"
# --url wins over the credential template for this loopback test; the
# credential file path is exercised by the mirror list check below.
guest "bash /var/tmp/migrate-to-channel.sh --channel $channel --url '$tunnel_url' --release-key /var/tmp/luma-os-release.asc --apply" >"$out/apply.log" 2>&1 ||
  { sed "s/$credential/<credential>/g" -i "$out/apply.log"; luma_os_die 'migration --apply failed'; }
sed "s/$credential/<credential>/g" -i "$out/apply.log"
check 'grub.cfg carries the greenboot boot counter' guest 'grep -q boot_counter /boot/grub2/grub.cfg'
check 'grub.cfg carries the hidden menu before the BLS entries' guest "python3 -c 'import re,sys; t=open(\"/boot/grub2/grub.cfg\").read(); h=t.find(\"### BEGIN 09_luma_hidden_menu.cfg ###\"); b=re.search(r\"(?m)^(blscfg|source .prefix/luma.cfg)\$\", t); sys.exit(not (h >= 0 and b and h < b.start()))'"
check 'the luma remote file is world-readable' guest 'test "$(stat -c %a /etc/ostree/remotes.d/luma.conf)" = 644'
check 'the remote reads the root-only mirror list, which holds the channel URL' guest "grep -Fxq 'url=mirrorlist=file:///etc/luma/update-mirrorlist' /etc/ostree/remotes.d/luma.conf && test \"\$(stat -c '%a %U' /etc/luma/update-mirrorlist)\" = '600 root' && grep -Fxq '$tunnel_url' /etc/luma/update-mirrorlist && ! grep -Fq '$credential' /etc/ostree/remotes.d/luma.conf"
guest 'systemctl reboot' >/dev/null 2>&1 || true
sleep 20
wait_ssh 900 || luma_os_die 'migrated VM did not come up'
guest 'rpm-ostree status --json' >"$out/after.json"
booted() { python3 -c 'import json,sys; d=[x for x in json.load(open(sys.argv[1]))["deployments"] if x.get("booted")][0]; v=d.get(sys.argv[2]); print(" ".join(v) if isinstance(v, list) else (v or ""))' "$out/after.json" "$1"; }
check "booted the channel head $head" test "$(booted base-checksum || true)" = "$head" -o "$(booted checksum)" = "$head"
check "origin is luma:$ref" test "$(booted origin)" = "luma:$ref"
check 'htop stays layered' bash -c "grep -qw htop <<<'$(booted requested-packages)'"
check 'the appindicator layer was dropped' bash -c "! grep -qw gnome-shell-extension-appindicator <<<'$(booted requested-packages)'"
check 'gnome-characters stays absent' guest '! rpm -q gnome-characters'
if [ "$removed_characters" = 1 ]; then
  check 'the no-op gnome-characters removal was dropped' bash -c "! grep -qw gnome-characters <<<'$(booted requested-base-removals)'"
fi
check 'the fstab root entry is retired' guest "! grep -Eq '^[^#[:space:]]+[[:space:]]+/[[:space:]]' /etc/fstab"
check 'no system unit failed after migration' guest 'sleep 60; test -z "$(systemctl --failed --no-legend --plain)"'
check '/var data survived' guest 'cd /var/home/migration-test && sha256sum --check --status marker.sha256'
check 'greenboot judged the migrated boot healthy' guest 'for i in $(seq 1 120); do s=$(systemctl show -p ActiveState --value greenboot-healthcheck.service); r=$(systemctl show -p Result --value greenboot-healthcheck.service); [ "$s" = active ] && [ "$r" = success ] && exit 0; [ "$s" = failed ] && exit 1; sleep 5; done; exit 1'
check 'the channel file names the channel' guest "grep -Fxq 'channel=$channel' /etc/luma/update-channel.conf"
guest 'rpm-ostree status' >"$out/after.txt" 2>&1 || true
printf '\nmigration test: %s failing checks; evidence %s\n' "$failures" "$out"
[ "$failures" -eq 0 ]
