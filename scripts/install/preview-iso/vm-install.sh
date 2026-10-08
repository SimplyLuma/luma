#!/bin/bash
set -euo pipefail
cd /w/vm
ISO=/w/luma-0.5-preview-x86_64.iso
rm -f target.qcow2 install-serial.log
qemu-img create -f qcow2 target.qcow2 60G >/dev/null
xorriso -osirrox on -indev $ISO -extract /images/pxeboot/vmlinuz vmlinuz -extract /images/pxeboot/initrd.img initrd.img >/dev/null 2>&1
cp /usr/share/edk2/ovmf/OVMF_VARS.fd vars.fd
timeout 5400 qemu-system-x86_64 -machine q35,accel=kvm -cpu host -smp 4 -m 6144 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/edk2/ovmf/OVMF_CODE.fd \
  -drive if=pflash,format=raw,file=vars.fd \
  -drive if=none,id=usbstick,file=$ISO,format=raw,readonly=on -device qemu-xhci -device usb-storage,drive=usbstick \
  -drive file=target.qcow2,if=virtio,format=qcow2 \
  -kernel vmlinuz -initrd initrd.img \
  -append "inst.stage2=hd:LABEL=LUMA-0_5-PREVIEW-X86_64 inst.ks=hd:LABEL=LUMA-0_5-PREVIEW-X86_64:/luma-auto-test.ks inst.text console=ttyS0" \
  -display none -serial file:install-serial.log -monitor unix:/w/vm/monitor.sock,server,nowait -no-reboot
echo "QEMU EXIT $?"
