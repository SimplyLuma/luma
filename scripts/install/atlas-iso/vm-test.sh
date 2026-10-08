#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Boot the Atlas installer ISO in a throwaway VM, never on real hardware.
#
#   vm-test.sh prepare WORK            create the VM's drives:
#                                        target.raw  40 GB NVMe, GPT + ext4 with files on it
#                                        other.raw    8 GB virtio, GPT + ext4 that must not change
#   vm-test.sh checksum WORK NAME      sha256 of both drives into WORK/NAME.sha256
#   vm-test.sh remote WORK ISO         boot with Atlas served to the host (webui.remote=1) on
#                                        127.0.0.1:18080 and the installer's sshd on 127.0.0.1:12222
#                                        (ATLAS_REMOTE= keeps Atlas in the VM's own viewer instead;
#                                        reach it through the sshd, e.g. ssh -L PORT:127.0.0.1:80;
#                                        ATLAS_CONSOLE= drops the serial console, so Anaconda plans
#                                        the graphical-boot "rhgb quiet" arguments as on a real
#                                        machine; ATLAS_APPEND adds boot options, e.g. inst.updates=)
#   ATLAS_DISPLAY_ARGS                 the QEMU display adapter and backend for remote, screen and
#                                        installed (default "-vga std -display none"), e.g.
#                                        "-vga none -device virtio-vga -display none" or
#                                        "-vga none -device virtio-vga-gl -display egl-headless,rendernode=/dev/dri/renderD128"
#   vm-test.sh screen WORK ISO         boot through UEFI firmware and GRUB exactly as a person
#                                        would, and take screenshots of Atlas running inside the VM
#   vm-test.sh installed WORK          boot the installed target drive alone (after ejecting the ISO)
#   vm-test.sh shot WORK NAME          save the VM's screen as WORK/vm/NAME.ppm
#   vm-test.sh keys WORK KEY...        type QEMU key names (a b spc ret tab ...)
#   vm-test.sh serial WORK "CMD"       send one line to the serial console
#   vm-test.sh stop WORK               power the VM off through its monitor
#
# The ISO is attached as removable USB storage, so ejecting it from inside the
# guest is the real code path. Runs inside the atlas-iso tools image with
# /dev/kvm and host networking (see docs/build/atlas-installer-iso.md).

set -euo pipefail

command=${1:?usage: vm-test.sh prepare|checksum|remote|screen|installed|stop WORK [ISO|NAME]}
work=${2:?work directory required}
mkdir -p "$work/vm"
cd "$work/vm"

ovmf_code=/usr/share/edk2/ovmf/OVMF_CODE.fd
ovmf_vars=/usr/share/edk2/ovmf/OVMF_VARS.fd

monitor() {
    python3 - "$work/vm/monitor.sock" "$@" <<'PY'
import socket, sys, time
path, *commands = sys.argv[1:]
s = socket.socket(socket.AF_UNIX)
s.connect(path)
time.sleep(0.2)
s.recv(65536)
for command in commands:
    s.sendall((command + "\n").encode())
    time.sleep(0.6)
    print(s.recv(65536).decode(errors="replace"))
s.close()
PY
}

partitioned_disk() {
    # $1 image, $2 size, $3 label, $4 file content
    local image=$1 size=$2 label=$3 content=$4
    rm -f "$image" "$image.part"
    truncate -s "$size" "$image"
    mkdir -p "populate-$label"
    printf '%s\n' "$content" > "populate-$label/README.txt"
    head -c 4194304 /dev/urandom > "populate-$label/data.bin"
    local sectors part_sectors
    sectors=$(( $(stat -c %s "$image") / 512 ))
    part_sectors=$(( sectors - 2048 - 34 ))
    printf 'label: gpt\nstart=2048, size=%s, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name="%s"\n' "$part_sectors" "$label" | sfdisk --quiet "$image"
    truncate -s $(( part_sectors * 512 )) "$image.part"
    mkfs.ext4 -q -L "$label" -d "populate-$label" "$image.part"
    dd if="$image.part" of="$image" bs=1M seek=1 conv=notrunc,sparse status=none
    rm -rf "$image.part" "populate-$label"
}

common_args() {
    local iso=$1
    printf '%s\n' \
        -machine q35,accel=kvm -cpu host -smp 4 -m 6144 \
        -drive "if=pflash,format=raw,readonly=on,file=$ovmf_code" \
        -drive "if=pflash,format=raw,file=$work/vm/vars.fd" \
        -device qemu-xhci,id=xhci \
        -drive "if=none,id=stick,file=$iso,format=raw,readonly=on" \
        -device usb-storage,bus=xhci.0,drive=stick,removable=on,bootindex=1 \
        -drive "if=none,id=target,file=$work/vm/target.raw,format=raw,cache=unsafe" \
        -device nvme,drive=target,serial=ATLASTARGET01,bootindex=2 \
        -drive "if=none,id=other,file=$work/vm/other.raw,format=raw,cache=unsafe" \
        -device virtio-blk-pci,drive=other \
        -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:18080-:80,hostfwd=tcp:127.0.0.1:12222-:22" \
        -device virtio-net-pci,netdev=net0 \
        ${ATLAS_DISPLAY_ARGS:--vga std -display none} \
        -monitor "unix:$work/vm/monitor.sock,server,nowait" \
        -chardev "socket,id=ser0,path=$work/vm/serial.sock,server=on,wait=off,logfile=$work/vm/serial.log" \
        -serial chardev:ser0
}

case "$command" in
prepare)
    partitioned_disk target.raw 40G OLD-DATA "This drive held files before Luma was installed."
    partitioned_disk other.raw 8G KEEP-ME "Atlas must never touch this drive."
    cp "$ovmf_vars" vars.fd
    ls -ls target.raw other.raw
    ;;
checksum)
    name=${3:?checksum name required}
    sha256sum target.raw other.raw | tee "$work/vm/$name.sha256"
    ;;
remote)
    iso=${3:?ISO required}
    volid=$(blkid -o value -s LABEL "$iso")
    xorriso -osirrox on -indev "$iso" -extract /images/pxeboot/vmlinuz vmlinuz -extract /images/pxeboot/initrd.img initrd.img >/dev/null 2>&1
    mapfile -t args < <(common_args "$iso")
    qemu-system-x86_64 "${args[@]}" -kernel vmlinuz -initrd initrd.img \
        -append "inst.stage2=hd:LABEL=$volid ${ATLAS_RAM-rd.live.ram=1} inst.graphical ${ATLAS_REMOTE-webui.remote=1} inst.sshd ${ATLAS_CONSOLE-console=tty0 console=ttyS0,115200} ${ATLAS_APPEND:-}" \
        -daemonize -pidfile "$work/vm/qemu.pid"
    echo "VM started (pid $(cat "$work/vm/qemu.pid")); Atlas on 127.0.0.1:18080 once Anaconda is ready"
    ;;
screen)
    iso=${3:?ISO required}
    mapfile -t args < <(common_args "$iso")
    qemu-system-x86_64 "${args[@]}" -daemonize -pidfile "$work/vm/qemu.pid"
    echo "VM started through firmware (pid $(cat "$work/vm/qemu.pid"))"
    ;;
installed)
    qemu-system-x86_64 -machine q35,accel=kvm -cpu host -smp 4 -m 4096 \
        -drive "if=pflash,format=raw,readonly=on,file=$ovmf_code" \
        -drive "if=pflash,format=raw,file=$work/vm/vars.fd" \
        -drive "if=none,id=target,file=$work/vm/target.raw,format=raw,cache=unsafe" \
        -device nvme,drive=target,serial=ATLASTARGET01,bootindex=1 \
        -drive "if=none,id=other,file=$work/vm/other.raw,format=raw,cache=unsafe" \
        -device virtio-blk-pci,drive=other \
        -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:12223-:22" -device virtio-net-pci,netdev=net0 \
        ${ATLAS_DISPLAY_ARGS:--vga std -display none} \
        -monitor "unix:$work/vm/monitor.sock,server,nowait" \
        -serial "file:$work/vm/serial-installed.log" \
        -daemonize -pidfile "$work/vm/qemu.pid"
    echo "installed system booting (pid $(cat "$work/vm/qemu.pid"))"
    ;;
shot)
    name=${3:?screenshot name required}
    monitor "screendump $work/vm/$name.ppm" >/dev/null
    ;;
serial)
    # send one line to the serial console and print what came back
    python3 - "$work/vm/serial.sock" "${3:?command}" <<'PY'
import socket, sys, time
s = socket.socket(socket.AF_UNIX)
s.connect(sys.argv[1])
s.settimeout(1.5)
s.sendall((sys.argv[2] + "\n").encode())
out = b""
end = time.time() + 6
while time.time() < end:
    try:
        chunk = s.recv(65536)
        if not chunk:
            break
        out += chunk
    except socket.timeout:
        pass
print(out.decode(errors="replace"))
PY
    ;;
keys)
    shift 2
    for key in "$@"; do monitor "sendkey $key" >/dev/null; done
    ;;
stop)
    monitor "quit" >/dev/null 2>&1 || true
    ;;
*)
    echo "unknown command $command" >&2
    exit 2
    ;;
esac
