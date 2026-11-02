#!/bin/bash
# On the Pixel 9 (root): compose a release commit without deploying it.
# Layers the RPMs in $RPMS (luma-android-images among them, or an image pair
# in $IMAGES) and the device files from ../system onto $BASE, labelled with
# the base policy plus any packaged SELinux modules it lacks.
# Usage: BASE=<commit> TAG=<name> RPMS=<dir> [IMAGES=<dir>] compose-release.sh
set -euo pipefail
: "${BASE:?}" "${TAG:?}" "${RPMS:?}"
SYSTEM=$(cd "$(dirname "$0")/../system" && pwd)
REPO=/ostree/repo
W=/var/tmp/compose-$TAG; O=$W/overlay
rm -rf $W; mkdir -p $O/usr/share/rpm
ostree --repo=$REPO cat $BASE /usr/share/rpm/rpmdb.sqlite > $O/usr/share/rpm/rpmdb.sqlite
rpm --root=$O --dbpath=/usr/share/rpm --ignorearch --nodeps --noscripts --notriggers \
	--oldpackage --replacefiles --replacepkgs -Uh $RPMS/*.rpm 2>&1 | grep -v "remove failed" || true
rpm --root=$O --dbpath=/usr/share/rpm -q gnome-shell prairie-core-apps prairie-icon-theme luma-android-runtime
# /opt is a link to /var/opt: packaged /opt trees live in /usr/lib/opt, as
# rpm-ostree places them, with the base's tmpfiles links.
if [ -d $O/opt ]; then
	mkdir -p $O/usr/lib/opt; cp -a $O/opt/. $O/usr/lib/opt/; rm -rf $O/opt
fi
# Scriptlets are skipped: enable user units the way their presets would.
systemctl --root=$O --global --preset-mode=enable-only preset-all || true
# luma-keyring's %post: its PAM line in the passwd stack.
if [ -x $O/usr/libexec/luma-keyring-pam-setup ]; then
	mkdir -p $O/etc/pam.d
	[ -f $O/etc/pam.d/passwd ] || ostree --repo=/ostree/repo cat $BASE /usr/etc/pam.d/passwd > $O/etc/pam.d/passwd
	sh $O/usr/libexec/luma-keyring-pam-setup add $O/etc/pam.d/passwd
fi

# Android: binderfs before the container, delegated cgroups; the image pair
# where luma_android.initialize verifies it.
mkdir -p $O/etc/systemd/system/waydroid-container.service.d
printf '[Unit]\nAfter=dev-binderfs.mount\n\n[Service]\nDelegate=yes\n' \
	> $O/etc/systemd/system/waydroid-container.service.d/10-luma-ordering.conf
I=$O/usr/share/waydroid-extra/images
if [ -n "${IMAGES:-}" ]; then
mkdir -p $I
cp $IMAGES/system.img $IMAGES/vendor.img $I/; chmod 0644 $I/*.img
python3 - $I <<'PY'
import hashlib, json, os, sys
root = sys.argv[1]
images = {}
for name in ("system.img", "vendor.img"):
    path = os.path.join(root, name)
    with open(path, "rb") as f:
        images[name] = {"size": os.path.getsize(path), "sha256": hashlib.file_digest(f, "sha256").hexdigest()}
with open(os.path.join(root, "luma-images.json"), "w") as f:
    json.dump({"architecture": "aarch64", "images": images}, f, indent=2, sort_keys=True)
PY
chmod 0644 $I/luma-images.json
fi
test -s $I/luma-images.json ||
	ostree --repo=/ostree/repo cat $BASE /usr/share/waydroid-extra/images/luma-images.json >/dev/null

# Device files (see ../README.md)
install -Dm0644 $SYSTEM/50-luma-tokay-wifi-powersave.conf $O/usr/lib/NetworkManager/conf.d/50-luma-tokay-wifi-powersave.conf
install -Dm0644 $SYSTEM/60-luma-tokay-aoc-quantum.conf $O/usr/share/pipewire/pipewire.conf.d/60-luma-tokay-aoc-quantum.conf
install -Dm0644 $SYSTEM/60-luma-tokay-thermal.rules $O/usr/lib/udev/rules.d/60-luma-tokay-thermal.rules
install -Dm0644 $SYSTEM/60-luma-tokay-x4-cap.conf $O/usr/lib/tmpfiles.d/60-luma-tokay-x4-cap.conf
for w in ims aoc-audio; do
	install -Dm0755 $SYSTEM/luma-tokay-$w-watchdog.sh $O/usr/libexec/luma-tokay/$w-watchdog.sh
	sed "s|/var/lib/luma-tokay/$w-watchdog.sh|/usr/libexec/luma-tokay/$w-watchdog.sh|" \
		$SYSTEM/luma-tokay-$w-watchdog.service > $W/unit
	install -Dm0644 $W/unit $O/usr/lib/systemd/system/luma-tokay-$w-watchdog.service
	mkdir -p $O/etc/systemd/system/multi-user.target.wants
	ln -sf /usr/lib/systemd/system/luma-tokay-$w-watchdog.service \
		$O/etc/systemd/system/multi-user.target.wants/luma-tokay-$w-watchdog.service
done
# The device updater: its own ref and graph; no automatic download until a
# Pixel 9 channel is published.
mkdir -p $O/etc/luma
cat > $O/etc/luma/update.conf <<'CONF'
[update]
hardware_target = google,tokay
ref_template = luma/1/{arch}/google-tokay/{channel}
graph_url = https://dl.simplyluma.com/os/graph/aarch64/google-tokay/{channel}.json
stable_repo_url = https://dl.simplyluma.com/os/repo
automatic_download = false
CONF
# Mutter's frame-thread utilization floor (main's 00-luma-desktop; the base
# predates it), compiled into the luma dconf database.
ostree --repo=$REPO checkout -U --subpath=/usr/etc/dconf/db/luma.d $BASE $W/luma.d
printf '[org/gnome/mutter]\nframe-utilization-min=uint32 512\n' > $W/luma.d/20-luma-tokay-frames
mkdir -p $O/etc/dconf/db/luma.d
cp $W/luma.d/20-luma-tokay-frames $O/etc/dconf/db/luma.d/
dconf compile $O/etc/dconf/db/luma $W/luma.d

if [ -d $O/etc ]; then
	mkdir -p $O/usr/etc; cp -a $O/etc/. $O/usr/etc/; rm -rf $O/etc
	rm -f $O/usr/etc/{passwd,shadow,group,gshadow,.pwd.lock}{,-}
fi
rm -rf $O/var
if [ -d $O/usr/share/glib-2.0/schemas ]; then
	ostree --repo=$REPO checkout -U --subpath=/usr/share/glib-2.0/schemas $BASE $W/schemas
	cp -a $O/usr/share/glib-2.0/schemas/. $W/schemas/
	glib-compile-schemas $W/schemas
	cp $W/schemas/gschemas.compiled $O/usr/share/glib-2.0/schemas/
fi
# Scriptlets are skipped, so packaged SELinux modules the base policy lacks
# were never loaded (waydroid, luma_android_runtime, luma_esim). Load them
# into the policy this commit ships and labels with; files whose label
# changes are copied into the overlay so the commit relabels them.
SR=$W/sepolicy; mkdir -p $SR/etc
ostree --repo=$REPO checkout -U --subpath=/usr/etc/selinux $BASE $SR/etc/selinux
ostree --repo=$REPO checkout -U --subpath=/usr/share/selinux $BASE $W/selinux
[ ! -d $O/usr/share/selinux ] || cp -a $O/usr/share/selinux/. $W/selinux/
loaded=$(semodule -p $SR -l); mods=
for f in $W/selinux/packages/*.cil $W/selinux/packages/*.pp.bz2 $W/selinux/targeted/*.pp; do
	[ -e "$f" ] || continue; m=$(basename $f); m=${m%%.*}
	grep -qx "$m" <<<"$loaded" || mods="$mods $f"
done
if [ -n "$mods" ]; then
	semodule -p $SR -s targeted -X 200 -i $mods
	mkdir -p $O/usr/etc/selinux; cp -a $SR/etc/selinux/targeted $O/usr/etc/selinux/
	mkdir -p $W/root; ostree --repo=$REPO checkout --subpath=/usr $BASE $W/root/usr
	{ setfiles -n -v -r $W/root $SR/etc/selinux/targeted/contexts/files/file_contexts $W/root/usr 2>&1 || :; } |
		sed -n "s|^Would relabel $W/root\(/[^ ]*\) from .*|\1|p" | while read -r p; do
			[ -e "$O$p" ] || { mkdir -p "$O$(dirname "$p")"; cp -a "$W/root$p" "$O$p"; echo "relabel $p"; }
		done
	rm -rf $W/root
fi
rm -f $O/usr/share/rpm/.rpm.lock; : > $O/usr/share/rpm/.rpm.lock
KVER=$(ostree --repo=$REPO show --print-metadata-key=ostree.linux $BASE | tr -d "'")
pkglist=$(rpm --root=$O --dbpath=/usr/share/rpm -qa --qf "('%{NAME}', '%{EPOCHNUM}', '%{VERSION}', '%{RELEASE}', '%{ARCH}'), " | sed 's/, $//')
NEW=$(ostree --repo=$REPO commit --branch=luma/1/aarch64/google-tokay/$TAG --parent=$BASE \
	--tree=ref=$BASE --tree=dir=$O --selinux-policy=$SR \
	--subject="Pixel 9 $TAG" \
	--add-metadata-string=version="$TAG" \
	--add-metadata-string=org.projectluma.channel=$TAG \
	--add-metadata-string=org.projectluma.base-commit=$BASE \
	--add-metadata=ostree.bootable=true \
	--add-metadata-string=ostree.linux="$KVER" \
	--add-metadata="rpmostree.rpmdb.pkglist=[$pkglist]")
echo "commit $NEW"
