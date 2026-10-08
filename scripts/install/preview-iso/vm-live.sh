#!/bin/bash
set -euo pipefail
cd /w/vm
rm -f live-*.ppm
cp /usr/share/edk2/ovmf/OVMF_VARS.fd live-vars.fd
qemu-system-x86_64 -machine q35,accel=kvm -cpu host -smp 4 -m 6144 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/edk2/ovmf/OVMF_CODE.fd \
  -drive if=pflash,format=raw,file=live-vars.fd \
  -drive if=none,id=usbstick,file=/w/luma-0.5-preview-x86_64.iso,format=raw,readonly=on -device qemu-xhci -device usb-storage,drive=usbstick,bootindex=1 \
  -vga std -display none -monitor unix:/w/vm/live.sock,server,nowait -serial file:/w/vm/live-serial.log &
QPID=$!
python3 <<'PY'
import socket,time
def hmp(cmd):
    s=socket.socket(socket.AF_UNIX); s.connect("/w/vm/live.sock"); time.sleep(0.2); s.recv(4096)
    s.sendall((cmd+"\n").encode()); time.sleep(0.4); s.close()
time.sleep(12); hmp("screendump /w/vm/live-menu.ppm")
hmp("sendkey down"); time.sleep(0.5); hmp("sendkey down"); time.sleep(0.5); hmp("screendump /w/vm/live-menu2.ppm"); hmp("sendkey ret")
for t in (40,60,60,60):
    time.sleep(t); hmp(f"screendump /w/vm/live-{int(time.time())%100000}.ppm")
PY
kill $QPID || true; wait || true
ls /w/vm/live-*.ppm
