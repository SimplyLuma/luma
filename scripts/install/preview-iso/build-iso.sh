#!/bin/bash
set -euo pipefail
cd /w
V=LUMA-0_5-PREVIEW-X86_64
mkdir -p /w/live/iso/luma-live
mv -f /w/live/iso/images/luma/vmlinuz /w/live/iso/images/luma/initrd.img /w/live/iso/luma-live/ 2>/dev/null || true
chmod 0644 /w/live/iso/luma-live/initrd.img
TRY=$'menuentry \'Try Luma 0.5 Preview without installing\' --class fedora --class gnu-linux --class gnu --class os {\n\tlinux /luma-live/vmlinuz root=live:CDLABEL='"$V"$' rd.live.image rd.live.overlay.overlayfs=1 quiet rhgb\n\tinitrd /luma-live/initrd.img\n}\nsubmenu \'Troubleshooting -->\''
rm -f luma-0.5-preview-x86_64.iso.partial
mkksiso --ks /w/add/ks/luma.ks -a /w/add/ks/luma-auto-test.ks -a /w/luma-repo \
  -a /w/live/iso/LiveOS -a /w/live/iso/luma-live \
  -V $V \
  -R "submenu 'Troubleshooting -->'" "$TRY" \
  -R "Install Fedora 44" "Install Luma 0.5 Preview" \
  -R "install Fedora 44" "install Luma 0.5 Preview" \
  -R "Rescue a Fedora system" "Rescue a Luma system" \
  -R 'set default="1"' 'set default="0"' \
  --tmp /w/tmp \
  Fedora-Silverblue-ostree-x86_64-44-1.7.iso luma-0.5-preview-x86_64.iso.partial
mv luma-0.5-preview-x86_64.iso.partial luma-0.5-preview-x86_64.iso
xorriso -osirrox on -indev luma-0.5-preview-x86_64.iso -extract /EFI/BOOT/grub.cfg /w/out-efi.cfg -extract /boot/grub2/grub.cfg /w/out-bios.cfg >/dev/null 2>&1
ls -la luma-0.5-preview-x86_64.iso
sha256sum luma-0.5-preview-x86_64.iso > luma-0.5-preview-x86_64.iso.sha256
echo DONE
