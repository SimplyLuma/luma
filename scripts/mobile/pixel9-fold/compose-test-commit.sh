#!/usr/bin/env bash
# Compose a Pixel 9 Pro Fold test commit and its fresh-install userdata image.
# The base mobile commit stays as is; only the replacement RPMs' files and the
# updated rpmdb are layered on top (--tree=ref + --tree=dir), labelled with
# the base's own SELinux policy. No full checkout: the build host's policy
# cannot carry every label of the phone's tree. Files the new packages no
# longer ship stay from the base, which is fine for a test image. Then the
# FP6 fresh-image builder with the published root/boot UUIDs, so the
# RAM-boot kernel command line keeps working. Not a release: channel "test",
# and the base build's composition/provenance keys are not carried over.
# The builder only accepts signed commits; test commits are signed with a
# local test-only key (never the release key) and the image trusts only that
# key, through a copy of the pinned inputs that names it.
# Usage (root): compose-test-commit.sh <tag> [<rpm>...]
# EXTRA_TREE=<dir> layers files too; NO_IMAGE=1 stops after the signed commit.
# Inputs (all required):
#   WORKDIR     root-only work directory (repo, images, test signing key); the
#               image builder refuses group-writable ancestors
#   BASE_REPO   OSTree repo holding BASE
#   BASE        the base mobile commit
#   KVER        its kernel release
#   SRC         Luma source tree with scripts/mobile/build-fp6-alpha1-fresh-image.py
#   INPUTS      the fresh-image builder's portable inputs
#   FIRMWARE    the builder's normalised firmware archive
#   BOOT        an existing boot image for the builder (it is not flashed)
#   ROOT_UUID, BOOT_UUID  the published image UUIDs the boot image expects
set -euo pipefail
TAG=$1; shift
: "${WORKDIR:?}" "${BASE_REPO:?}" "${BASE:?}" "${KVER:?}" "${SRC:?}" "${INPUTS:?}" "${FIRMWARE:?}" "${BOOT:?}" "${ROOT_UUID:?}" "${BOOT_UUID:?}"
REF=luma/1/aarch64/fairphone-fp6/nightly
D=$WORKDIR
mkdir -p "$D"; chmod 0700 "$D"
REPO=$D/repo
W=$D/work-$TAG
O=$W/overlay
G=$D/test-signing
log() { echo "$(date +%T) $*"; }

[ -f "$REPO/config" ] || ostree init --repo="$REPO" --mode=archive --collection-id=org.projectluma.OS
ostree --repo="$REPO" show "$BASE" >/dev/null 2>&1 || ostree --repo="$REPO" pull-local --untrusted "$BASE_REPO" "$BASE"

# Packages built in a setgid or default-ACL tree record those directory modes
# (e.g. 2750) and the session can then not read them; refuse such RPMs.
bad=$(for r in "$@"; do rpm -qp --dump "$r" 2>/dev/null |
	awk -v r="$(basename "$r")" '{ m = strtonum("0" $5); if (and(m, 0170000) == 0040000 && (and(m, 06000) || and(m, 0005) != 5)) printf "%s %s %o\n", r, $1, and(m, 07777) }'; done)
[ -z "$bad" ] || { echo "refusing RPMs with unreadable or setgid directories:"; echo "$bad"; exit 1; }
rm -rf "$W"; mkdir -p "$O/usr/share/rpm"
ostree --repo="$REPO" cat "$BASE" /usr/share/rpm/rpmdb.sqlite > "$O/usr/share/rpm/rpmdb.sqlite"
if [ $# -gt 0 ]; then
	log "install $#: $*"
	rpm --root="$O" --dbpath=/usr/share/rpm --ignorearch --nodeps --noscripts --notriggers \
		--oldpackage --replacefiles --replacepkgs -Uvh "$@" 2>&1 | grep -v "^warning: file .*: remove failed" || true
	rpm --root="$O" --dbpath=/usr/share/rpm -q --qf '%{NAME}-%{VERSION}-%{RELEASE}\n' \
		$(for r in "$@"; do rpm -qp --qf '%{NAME} ' "$r"; done)
	# OSTree keeps the default /etc under /usr/etc
	if [ -d "$O/etc" ]; then
		mkdir -p "$O/usr/etc"
		cp -a "$O/etc/." "$O/usr/etc/"
		rm -rf "$O/etc"
		# rpm writes account files into the empty overlay with only the groups
		# of the new packages; layered on top they would replace the base
		# image ones. Accounts come from the packages sysusers.d files.
		rm -f "$O"/usr/etc/{passwd,shadow,group,gshadow,.pwd.lock}{,-}
	fi
	rm -rf "$O/var"
	if [ -d "$O/usr/share/glib-2.0/schemas" ]; then
		ostree --repo="$REPO" checkout -U --subpath=/usr/share/glib-2.0/schemas "$BASE" "$W/schemas"
		cp -a "$O/usr/share/glib-2.0/schemas/." "$W/schemas/"
		glib-compile-schemas "$W/schemas"
		cp "$W/schemas/gschemas.compiled" "$O/usr/share/glib-2.0/schemas/"
	fi
fi
rm -f "$O/usr/share/rpm/.rpm.lock"; : > "$O/usr/share/rpm/.rpm.lock"

# test-only signing key and the matching portable inputs and pins
if [ ! -f "$G/key-id" ]; then
	install -d -m 0700 "$G/gnupg"
	gpg --homedir "$G/gnupg" --batch --passphrase '' --quick-generate-key \
		"Luma Fold test image signing (not for release) <test@luma.invalid>" ed25519 sign never
	gpg --homedir "$G/gnupg" --batch --list-keys --with-colons | awk -F: '/^fpr/{print $10; exit}' > "$G/key-id"
	install -d "$G/portable-inputs"
	cp "$INPUTS/origin" "$INPUTS/anchor.img" "$INPUTS/kernel-args.json" "$G/portable-inputs/"
	gpg --homedir "$G/gnupg" --batch --export "$(cat "$G/key-id")" > "$G/portable-inputs/luma-release.gpg"
	sed "s/^SHA256_LUMA_RELEASE_GPG=.*/SHA256_LUMA_RELEASE_GPG=$(sha256sum "$G/portable-inputs/luma-release.gpg" | cut -d' ' -f1)/" \
		"$SRC/config/mobile/fp6-physical/alpha1-fresh-image-inputs.env" > "$G/inputs.env"
fi
# The packaged remote trusts /etc/pki/ostree/luma-release.gpg: keep the
# release key and add the test key, so this image verifies its own commit
# and can still take signed release updates later.
mkdir -p "$O/usr/etc/pki/ostree"
ostree --repo="$REPO" cat "$BASE" /usr/etc/pki/ostree/luma-release.gpg > "$O/usr/etc/pki/ostree/luma-release.gpg" 2>/dev/null || : > "$O/usr/etc/pki/ostree/luma-release.gpg"
cat "$G/portable-inputs/luma-release.gpg" >> "$O/usr/etc/pki/ostree/luma-release.gpg"

pkglist=$(rpm --root="$O" --dbpath=/usr/share/rpm -qa --qf "('%{NAME}', '%{EPOCHNUM}', '%{VERSION}', '%{RELEASE}', '%{ARCH}'), " | sed 's/, $//')
log "commit"
NEW=$(ostree --repo="$REPO" commit --branch="$REF" --parent="$BASE" \
	--tree=ref="$BASE" ${EXTRA_TREE:+--tree=dir="$EXTRA_TREE"} --tree=dir="$O" --selinux-policy-from-base \
	--subject="Fold test $TAG" \
	--add-metadata-string=version="$(ostree --repo="$REPO" show --print-metadata-key=version "$BASE" | tr -d "'")+fold.$TAG" \
	--add-metadata-string=org.projectluma.channel=test \
	--add-metadata-string=org.projectluma.architecture=aarch64 \
	--add-metadata-string=org.projectluma.hardware-target=fairphone,fp6 \
	--add-metadata-string=org.projectluma.base-commit="$BASE" \
	--add-metadata=ostree.bootable=true \
	--add-metadata-string=ostree.linux="$KVER" \
	--add-metadata="rpmostree.rpmdb.pkglist=[$pkglist]")
log "commit $NEW"
echo "$NEW" > "$D/commit-$TAG"

ostree --repo="$REPO" gpg-sign --gpg-homedir="$G/gnupg" "$NEW" "$(cat "$G/key-id")"

[ -z "${NO_IMAGE:-}" ] || { rm -rf "$W"; exit 0; }
log "image"
python3 -B "$SRC/scripts/mobile/build-fp6-alpha1-fresh-image.py" \
	--candidate-repo "$REPO" --commit "$NEW" --portable-inputs "$G/portable-inputs" \
	--inputs "$G/inputs.env" \
	--firmware "$FIRMWARE" --work "$D/image-$TAG" --scratch "$D/scratch-$TAG" \
	--root-uuid "$ROOT_UUID" --boot-uuid "$BOOT_UUID" \
	--existing-boot "$BOOT" --zstd-level 3
rm -rf "$W"
log "image at $D/image-$TAG/output"
