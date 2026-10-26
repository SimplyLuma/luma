#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Configure only a disposable image containing the actual native Luma phone shell.
set -euo pipefail
[ "$(id -u)" = 0 ]
[ "$(systemd-detect-virt --vm)" = kvm ]
[ "$#" = 1 ]
build_id=$1
root=/var/tmp/luma-tester-phone-image
[ -x /opt/luma/phosh/libexec/phosh ]
[ -x /opt/luma/phosh/bin/phosh-session ]
[ -x /usr/bin/phoc ]
install -d /etc/ld.so.conf.d /etc/systemd/user /etc/phosh
install -d /etc/cloud/cloud.cfg.d
# Export removes all host keys. Keep the first boot-generated identity stable
# when cloud-init rechecks its NoCloud seed; runtime host-key pinning stays strict.
printf '%s\n' 'ssh_deletekeys: false' >/etc/cloud/cloud.cfg.d/99-luma-tester-ssh.cfg
printf '%s\n' /opt/luma/phosh/lib64 >/etc/ld.so.conf.d/luma-tester-phosh.conf
ldconfig
glib-compile-schemas --strict /opt/luma/phosh/share/glib-2.0/schemas
cp -a /opt/luma/phosh/lib/systemd/user/. /etc/systemd/user/
cat >/etc/phosh/phoc.ini <<'PHOC'
[output:Virtual-1]
mode=432x960
scale=1
PHOC
# Remove the desktop virtual EDID; the phone uses QEMU's actual portrait output.
grubby --update-kernel=ALL --remove-args='drm.edid_firmware=Virtual-1:luma/tester-desktop.bin'
rm -f /etc/dracut.conf.d/90-luma-tester-display.conf /usr/lib/firmware/luma/tester-desktop.bin
dracut --force --regenerate-all
rm -rf /usr/share/gnome-shell/extensions/handheld@project-luma.local
rm -f /etc/dconf/db/luma.d/95-luma-tester-extensions
install -Dm755 "$root/scripts/tester/mobile-session" /usr/libexec/luma-tester-mobile-session
install -Dm755 "$root/scripts/tester/mobile-ready.py" /usr/libexec/luma-tester-mobile-ready
install -Dm755 "$root/scripts/tester/apply-profile" /usr/libexec/luma-tester-apply-profile
install -Dm644 "$root/config/tester/mobile.dconf" /usr/share/luma-tester/profiles/mobile.dconf
install -Dm644 "$root/profiles/desktop.dconf" /usr/share/luma-tester/profiles/mobile-base.dconf
install -Dm644 "$root/profiles/handheld.dconf" /usr/share/luma-tester/profiles/mobile-handheld.dconf
install -Dm644 "$root/config/tester/mobile-ready.service" /etc/systemd/user/luma-tester-mobile-ready.service
install -Dm755 "$root/control/libluma-tester-control.so" /usr/lib64/gtk-3.0/modules/libluma-tester-control.so
install -d /etc/systemd/user/graphical-session.target.wants /etc/systemd/user/mobi.phosh.Shell.service.d /etc/systemd/user/mobi.phosh.OSK.service.d
ln -sf ../luma-tester-mobile-ready.service /etc/systemd/user/graphical-session.target.wants/luma-tester-mobile-ready.service
# The physical phone keeps its normal lock policy. This disposable developer VM
# intentionally starts unlocked and authorizes SSH only with its per-session key.
cat >/etc/systemd/user/mobi.phosh.Shell.service.d/99-luma-tester.conf <<'UNIT'
[Service]
Environment=LUMA_TESTER_CONTROL=1
Environment=GTK_MODULES=/usr/lib64/gtk-3.0/modules/libluma-tester-control.so
ExecStart=
ExecStart=/opt/luma/phosh/libexec/phosh --unlocked
UNIT
install -Dm755 "$root/keyboard/luma-stevia-x86_64/luma-phosh-osk-stevia" /usr/libexec/luma-phosh-osk-stevia
install -Dm755 "$root/keyboard/luma-keyboard-x86_64/luma-latinime-decoder" /usr/libexec/luma-latinime-decoder
install -Dm644 "$root/keyboard/luma-keyboard-x86_64/main_en.dict" /usr/share/luma-keyboard/main_en.dict
cat >/etc/systemd/user/mobi.phosh.OSK.service.d/99-luma-tester.conf <<'UNIT'
[Service]
Environment=POS_DEBUG=force-completion
Environment=LUMA_LATINIME_DECODER=/usr/libexec/luma-latinime-decoder
Environment=LUMA_LATINIME_DICTIONARY=/usr/share/luma-keyboard/main_en.dict
ExecStart=
ExecStart=/usr/libexec/luma-phosh-osk-stevia --allow-replacement
UNIT
printf '%s\n' 'd /run/luma-display 0755 luma luma -' >/etc/tmpfiles.d/luma-tester-phone.conf
systemd-tmpfiles --create /etc/tmpfiles.d/luma-tester-phone.conf
cat >/etc/greetd/config.toml <<'GREETD'
[terminal]
vt = 1
[initial_session]
command = "/usr/libexec/luma-tester-mobile-session"
user = "luma"
[default_session]
command = "agreety --cmd /usr/libexec/luma-tester-mobile-session"
user = "greetd"
GREETD
cat >/etc/luma/tester-image <<IDENTITY
LUMA_TESTER_IMAGE_VERSION=1
LUMA_TESTER_TARGET=mobile
LUMA_TESTER_MOBILE_RENDERER=luma-phosh
LUMA_TESTER_BUILD=$build_id
LUMA_HARDWARE_BACKEND=virtual
LUMA_PHYSICAL_DEVICE=false
IDENTITY
printf '%s\n' mobile >/etc/luma-tester-target
install -m644 "$root/inputs.json" /etc/luma/tester-source.json
/usr/libexec/luma-tester-apply-profile mobile
rpm -qa --qf '%{NEVRA}\n' | sort >/etc/luma/tester-packages.txt
systemctl enable sshd.service greetd.service luma-tester-profile.service
systemctl set-default graphical.target
