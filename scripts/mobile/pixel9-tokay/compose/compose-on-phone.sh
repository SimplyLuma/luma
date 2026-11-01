# On the Pixel 9: layer RPMs in /var/tmp/rpms147 on the booted base commit and deploy it.
set -euo pipefail
BASE=aa3d451e69a5da684f008b58fc9f4f2dbdb4d5f8c423b6c67e7b193db689d8ca
TAG=tokay147c
REPO=/ostree/repo
W=/var/tmp/compose-$TAG; O=$W/overlay
rm -rf $W; mkdir -p $O/usr/share/rpm
ostree --repo=$REPO cat $BASE /usr/share/rpm/rpmdb.sqlite > $O/usr/share/rpm/rpmdb.sqlite
rpm --root=$O --dbpath=/usr/share/rpm --ignorearch --nodeps --noscripts --notriggers \
	--oldpackage --replacefiles --replacepkgs -Uh /var/tmp/rpms147/*.rpm 2>&1 | grep -v "remove failed" || true
rpm --root=$O --dbpath=/usr/share/rpm -q gnome-shell mutter prairie-core-apps luma-developer-platform luma-android-runtime waydroid gnome-control-center
# Scriptlets are skipped: enable the new user units the way their presets would.
systemctl --root=$O --global preset luma-app-host.service luma-native-app-prewarm.timer || true
# As main's image (android_apps): binderfs before the container, delegated cgroups.
mkdir -p $O/etc/systemd/system/waydroid-container.service.d
cat > $O/etc/systemd/system/waydroid-container.service.d/10-luma-ordering.conf <<'CONF'
[Unit]
After=dev-binderfs.mount

[Service]
Delegate=yes
CONF
# luma-android-images payload (Mesa 26.3 pair) where luma_android.initialize verifies it.
I=$O/usr/share/waydroid-extra/images; mkdir -p $I
cp /var/lib/waydroid/images-mesa263/system.img /var/lib/waydroid/images-mesa263/vendor.img $I/
chmod 0644 $I/*.img
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
rm -f $O/usr/share/rpm/.rpm.lock; : > $O/usr/share/rpm/.rpm.lock
KVER=$(ostree --repo=$REPO show --print-metadata-key=ostree.linux $BASE | tr -d "'")
pkglist=$(rpm --root=$O --dbpath=/usr/share/rpm -qa --qf "('%{NAME}', '%{EPOCHNUM}', '%{VERSION}', '%{RELEASE}', '%{ARCH}'), " | sed 's/, $//')
NEW=$(ostree --repo=$REPO commit --branch=luma/tokay/local --parent=$BASE \
	--tree=ref=$BASE --tree=dir=$O --selinux-policy=/ \
	--subject="Pixel 9 $TAG" \
	--add-metadata-string=version="1.0.0-nightly.20261015.25+fold.mobile2.$TAG" \
	--add-metadata-string=org.projectluma.channel=test \
	--add-metadata-string=org.projectluma.base-commit=$BASE \
	--add-metadata=ostree.bootable=true \
	--add-metadata-string=ostree.linux="$KVER" \
	--add-metadata="rpmostree.rpmdb.pkglist=[$pkglist]")
echo "commit $NEW"
ORIG=$(ls /ostree/deploy/luma/deploy/$BASE.0.origin)
cp $ORIG /var/tmp/origin-$TAG
ostree admin deploy --os=luma --retain --origin-file=/var/tmp/origin-$TAG $NEW
ostree admin status | head -8
ls -la /ostree/ | grep boot
ls /ostree/boot.0/luma/*/
