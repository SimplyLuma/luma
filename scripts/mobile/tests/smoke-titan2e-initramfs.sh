#!/bin/bash
# Smoke test of the Titan release initramfs against a composed image, on the
# arm64 build VM: the real init runs as PID 1 of a private PID/mount namespace,
# chrooted into a BusyBox root, with the loop partition in place of mmcblk0p1
# and a check in place of starting systemd.
#   smoke-titan2e-initramfs.sh IMAGE INIT BUSYBOX_RPM   (as root; INIT is
#   config/mobile/titan2e-initramfs/init)
#   smoke-titan2e-initramfs.sh IMAGE INIT BUSYBOX_RPM --update REPO REF
#     IMAGE is the channel head's image; the candidate commit REF in REPO is
#     staged over it as an update would be: boot the head, boot the staged
#     candidate, bless it, then force a fallback to the head.
set -euo pipefail
img=$1 init=$2 bbrpm=$3
mode=fresh
if [ "${4:-}" = --update ]; then
	mode=update candidate_repo=$5 candidate_ref=$6
fi
W=/var/tmp/titan-smoke
rm -rf "$W"; mkdir -p "$W/mini/bin"
cp --sparse=always "$img" "$W/disk.img"
(cd "$W" && rpm2cpio "$bbrpm" | cpio -id --quiet ./usr/bin/busybox) && mv "$W/usr/bin/busybox" "$W/mini/bin/"
L=$(losetup -P -f --show "$W/disk.img")
trap 'losetup -d "$L" 2>/dev/null || :' EXIT
for d in proc sys dev run tmp sysroot; do mkdir -p "$W/mini/$d"; done

# The check that replaces systemd: what the booted system would see.
cat >"$W/mini/check" <<'EOF'
#!/bin/busybox sh
/bin/busybox --install -s /bin 2>/dev/null
echo "== in new root"
[ -e /run/ostree-booted ] && echo "ok /run/ostree-booted" || echo "FAIL /run/ostree-booted missing"
grep -o 'ostree=[^ ]*' /proc/cmdline | sed 's/^/ok cmdline /'
[ -d /sysroot/ostree ] && echo "ok /sysroot is the physical root" || echo "FAIL /sysroot"
grep -q '^NAME=' /usr/lib/os-release && echo "ok os-release: $(sed -n 's/^PRETTY_NAME=//p' /usr/lib/os-release)"
awk '$2=="/" || $2=="/sysroot" || $2=="/var" || $2=="/usr" {print "mount", $2, $1, $3, $4}' /proc/mounts
cat /sysroot/boot/luma-boot-attempt 2>/dev/null | sed 's/^/attempt /'
t=$(sed -n 's/.*ostree=\([^ ]*\).*/\1/p' /proc/cmdline)
d=$(cd "/sysroot$t" 2>/dev/null && pwd -P) && echo "ok deployment ${d##*/}"
# The boot contract the booted system relies on (the gate requires each for
# this build's boots): greetd as the display manager, accounts through
# altfiles, the root grow, /boot for rpm-ostree, the FP6 Alpha 1 account and
# phone session (no first-user wizard), and the LK slot marked only after
# greenboot's health check.
c() { if eval "$2"; then echo "contract ok $1"; else echo "contract missing $1"; fi; }
w=/etc/systemd/system
c display-manager '[ "$(readlink $w/display-manager.service)" = /usr/lib/systemd/system/greetd.service ]'
c account-luma '/usr/bin/getent passwd luma >/dev/null'
c account-greetd '/usr/bin/getent group greetd >/dev/null'
c resize2fs '[ -x /usr/sbin/resize2fs ] || [ -x /usr/bin/resize2fs ]'
c boot-mount 'grep -q "^/sysroot/boot /boot none bind" /etc/fstab'
c phone-session 'grep -q "^command = \"/usr/libexec/luma-phosh-session\"" /etc/greetd/config.toml'
c initial-credential 'awk -F: "\$1==\"luma\"{print \$2}" /etc/shadow | grep -q "^\\\$y\\\$"'
c no-first-user '[ ! -e $w/sockets.target.wants/luma-first-user.socket ]'
c health-check '[ -L $w/multi-user.target.wants/greenboot-healthcheck.service ]'
c update-channel 'grep -q "^hardware_target = unihertz,titan2e" /etc/luma/update.conf'
c slot-mark-after-health '[ -L $w/greenboot-success.target.wants/luma-titan2e-boot-ok.service ] && [ ! -e $w/multi-user.target.wants/luma-titan2e-boot-ok.service ]'
EOF
chmod 0755 "$W/mini/check"
# The check runs from the new root; give it the same BusyBox.
run() {
	sed -e "s#/dev/mmcblk0p1#${L}p1#g" \
	    -e 's#exec switch_root /sysroot /usr/lib/systemd/systemd#cp /check /bin/busybox /sysroot/run/ \&\& exec chroot /sysroot /run/busybox sh /run/check#' \
	    -e 's#sleep 30; reboot -f#exit 1#' "$init" >"$W/mini/init"
	chmod 0755 "$W/mini/init"
	unshare --fork --pid --mount --propagation private sh -c 'mount --bind "$1" "$1" && exec chroot "$1" /init' sh "$W/mini" 2>&1 | sed 's/^/  /' || :
	{ dmesg | grep 'luma-initramfs' || :; } | tail -n 2 | sed 's/^/  kmsg: /'
}
mnt() { mkdir -p "$W/m"; mount "${L}p1" "$W/m"; }
umnt() { umount "$W/m"; }

entry_target() { # entry_target newest|previous
	ls "$W"/m/boot/loader/entries/*.conf | while read -r f; do echo "$(sed -n 's/^version //p' "$f") $f"; done |
		sort -rn | sed -n "$([ "$1" = newest ] && echo 1 || echo 2)p" | cut -d' ' -f2 |
		xargs sed -n 's/^options .*ostree=\([^ ]*\).*/\1/p'
}

if [ "$mode" = update ]; then
	echo "## update 1: the head, blessed"
	mnt; entry_target newest >"$W/m/boot/luma-boot-ok"; rm -f "$W/m/boot/luma-boot-attempt"; umnt
	run
	commit=$(ostree rev-parse --repo="$candidate_repo" "$candidate_ref")
	echo "## update 2: candidate $commit staged"
	mnt
	ostree pull-local --repo="$W/m/ostree/repo" --remote=luma "$candidate_repo" "$candidate_ref" >/dev/null
	ostree admin deploy --sysroot="$W/m" --os=luma --karg-none "luma:$candidate_ref" >/dev/null
	umnt
	run
	echo "## update 3: candidate blessed"
	mnt; entry_target newest >"$W/m/boot/luma-boot-ok"; rm -f "$W/m/boot/luma-boot-attempt"; umnt
	run
	echo "## update 4: forced fallback, the candidate unblessed after two tries"
	mnt; entry_target previous >"$W/m/boot/luma-boot-ok"
	echo "$(entry_target newest) 2" >"$W/m/boot/luma-boot-attempt"; umnt
	run
	exit 0
fi

echo "## boot 1: one deployment, nothing blessed"
run
echo "## boot 2: still unblessed"
run
echo "## second deployment (an update), then boots 3-5 without blessing"
mnt
ostree admin deploy --sysroot="$W/m" --os=luma --karg-none "$(ostree rev-parse --repo="$W/m/ostree/repo" luma:luma/1/aarch64/unihertz-titan2e/nightly)" >/dev/null
ls "$W/m/boot/loader/entries"; umnt
run; run; run
echo "## bless the newest entry (as luma-titan2e-boot-bless would after a good boot), then boot 6"
mnt
newest=$(for f in "$W"/m/boot/loader/entries/*.conf; do echo "$(sed -n 's/^version //p' "$f") $f"; done | sort -rn | head -1 | cut -d' ' -f2)
t=$(sed -n 's/^options .*ostree=\([^ ]*\).*/\1/p' "$newest")
echo "$t" >"$W/m/boot/luma-boot-ok"; rm -f "$W/m/boot/luma-boot-attempt"; umnt
run
