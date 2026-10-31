#!/usr/bin/env bash
# Compose a Fold test commit on the phone itself, in its own bare repo.
# Same layering as compose-test-commit.sh (base tree by reference, the
# replacement RPMs' files and updated rpmdb on top), for when the build
# server's disk is too busy: the phone only pulls the base objects it lacks
# and commits the small overlay on its own flash.
# Usage (root on the phone): compose-on-phone.sh <tag> <base> <extra-from> <rpm>...
#   <base>        commit already pulled into /ostree/repo (e.g. an ARM nightly)
#   <extra-from>  commit whose display geometry and initramfs are carried, so
#                 the boot images' OSTREE_PATH (bootcsum) stays valid
# EXTRA_TREE=<dir> layers a file tree too (above the carried files, below the RPMs).
# KVER: the base's kernel release (default: the one in the base's /usr/lib/modules).
# Prints the new commit; deploy it with `ostree admin deploy <commit>`.
set -euo pipefail
TAG=$1 BASE=$2 FROM=$3; shift 3
REPO=/ostree/repo
KVER=${KVER:-$(ostree --repo="$REPO" ls "$BASE" /usr/lib/modules | awk '{print $NF}' | sed -n 's|^/usr/lib/modules/||p' | head -1)}
BASEVER=$(ostree --repo="$REPO" show --print-metadata-key=version "$BASE" | tr -d "'")
W=/var/tmp/compose-$TAG
O=$W/overlay
X=$W/extra
log() { echo "$(date +%T) $*"; }

rm -rf "$W"; mkdir -p "$O/usr/share/rpm" "$X/usr/share/luma" "$X/usr/lib/modules/$KVER"
ostree --repo="$REPO" cat "$BASE" /usr/share/rpm/rpmdb.sqlite > "$O/usr/share/rpm/rpmdb.sqlite"
if [ $# -gt 0 ]; then
log "install $#"
/usr/bin/rpm --root="$O" --dbpath=/usr/share/rpm --ignorearch --nodeps --noscripts --notriggers \
	--oldpackage --replacefiles --replacepkgs -Uvh "$@" 2>&1 | grep -v -e "^warning: file .*: remove failed" -e "^#" || true
/usr/bin/rpm --root="$O" --dbpath=/usr/share/rpm -q --qf '%{NAME}-%{VERSION}-%{RELEASE}\n' \
	$(for r in "$@"; do /usr/bin/rpm -qp --qf '%{NAME} ' "$r"; done)
fi
# OSTree keeps the default /etc under /usr/etc
if [ -d "$O/etc" ]; then
	mkdir -p "$O/usr/etc"
	cp -a "$O/etc/." "$O/usr/etc/"
	rm -rf "$O/etc"
fi
# rpm writes account files into the empty overlay with only the groups of
# the new packages; layered on top they would replace the base image ones.
# Accounts come from the packages sysusers.d files.
rm -f "$O"/usr/etc/{passwd,shadow,group,gshadow,.pwd.lock}{,-}
rm -rf "$O/var"
if [ -d "$O/usr/share/glib-2.0/schemas" ]; then
	ostree --repo="$REPO" checkout -U --subpath=/usr/share/glib-2.0/schemas "$BASE" "$W/schemas"
	cp -a "$O/usr/share/glib-2.0/schemas/." "$W/schemas/"
	glib-compile-schemas "$W/schemas"
	cp "$W/schemas/gschemas.compiled" "$O/usr/share/glib-2.0/schemas/"
fi
rm -f "$O/usr/share/rpm/.rpm.lock"; : > "$O/usr/share/rpm/.rpm.lock"

ostree --repo="$REPO" cat "$FROM" /usr/share/luma/display-geometry.json > "$X/usr/share/luma/display-geometry.json"
ostree --repo="$REPO" cat "$FROM" "/usr/lib/modules/$KVER/initramfs.img" > "$X/usr/lib/modules/$KVER/initramfs.img"
chmod 0644 "$X/usr/share/luma/display-geometry.json" "$X/usr/lib/modules/$KVER/initramfs.img"

# Label only the layered files, with the policy the phone runs (the base's
# family): --selinux-policy-from-base would relabel the whole base tree, which
# re-reads every object (10 GB) and took the phone down under that I/O. The
# base tree then stays by reference. /usr/etc is labelled as /etc, as ostree
# does.
python3 - "$X" ${EXTRA_TREE:+"$EXTRA_TREE"} "$O" <<'EOF'
import os, selinux, sys
handle = selinux.selabel_open(selinux.SELABEL_CTX_FILE, None, 0)
for top in sys.argv[1:]:
    for root, dirs, files in os.walk(top):
        for name in [None] + dirs + files:
            path = root if name is None else os.path.join(root, name)
            rel = "/" + os.path.relpath(path, top).lstrip(".")
            if rel.startswith("/usr/etc"):
                rel = rel[len("/usr"):]
            mode = os.lstat(path).st_mode
            label = selinux.selabel_lookup(handle, rel.rstrip("/") or "/", mode)[1]
            os.setxattr(path, "security.selinux", label.encode() + b"\0", follow_symlinks=False)
EOF

pkglist=$(/usr/bin/rpm --root="$O" --dbpath=/usr/share/rpm -qa --qf "('%{NAME}', '%{EPOCHNUM}', '%{VERSION}', '%{RELEASE}', '%{ARCH}'), " | sed 's/, $//')
log "commit"
NEW=$(ostree --repo="$REPO" commit --branch="fold/$TAG" --parent="$BASE" \
	--tree=ref="$BASE" --tree=dir="$X" ${EXTRA_TREE:+--tree=dir="$EXTRA_TREE"} --tree=dir="$O" \
	--subject="Fold test $TAG" \
	--add-metadata-string=version="$BASEVER+fold.$TAG" \
	--add-metadata-string=org.projectluma.channel=test \
	--add-metadata-string=org.projectluma.architecture=aarch64 \
	--add-metadata-string=org.projectluma.base-commit="$BASE" \
	--add-metadata=ostree.bootable=true \
	--add-metadata-string=ostree.linux="$KVER" \
	--add-metadata="rpmostree.rpmdb.pkglist=[$pkglist]")
rm -rf "$W"
log "commit $NEW"
