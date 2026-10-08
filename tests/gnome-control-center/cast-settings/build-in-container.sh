#!/bin/bash
# Build gnome-control-center 50.4 with the Luma series 0001-0014 + 0015 (audio policy) + 0016 (Cast)
# in a disposable Fedora 44 container; rpmbuild and podman storage live on an
# exec loop volume (the secondary disk is noexec). Release 1.luma.16.preview20260915.1.
set -euo pipefail
W=/mnt/luma-secondary/luma-cast-ui
P="podman --root $W/fs/podman/storage --runroot /run/luma-cast-ui-podman-runroot --storage-opt overlay.imagestore=/var/lib/containers/storage"
R=$W/fs/gcc-rpmbuild
REL=1.luma.16.preview20260915.1
rm -rf "$R"; mkdir -p "$R"/{BUILD,BUILDROOT,RPMS,SRPMS,SPECS} "$R/unpack" "$R/in"
cd "$R/unpack"
rpm2cpio /home/nick/shell-menu-contract/build/settings-gcc3-admission/gnome-control-center-50.4-1.fc44.src.rpm > ../payload.cpio
cpio -idm --quiet < ../payload.cpio && rm ../payload.cpio
mv gnome-control-center.spec "$R/SPECS/"
cd "$R" && mv unpack SOURCES
cp $W/gcc/series/000[1-9]-*.patch $W/gcc/series/001[0-4]-*.patch $W/gcc/series3/001[56]-*.patch SOURCES/
rm -f SOURCES/0000-luma-fedora-spec.patch
(cd SPECS && patch -p1 --no-backup-if-mismatch < $W/gcc/series3/0000-luma-fedora-spec.patch)
cp /mnt/luma-secondary/luma-build/depot/fs/rpms/x86_64/luma-developer-platform-0.1.0-1.luma.62~preview.20260910.1.fc44.x86_64.rpm \
   /mnt/luma-secondary/luma-build/depot/fs/rpms/x86_64/luma-developer-platform-devel-0.1.0-1.luma.62~preview.20260910.1.fc44.x86_64.rpm in/
mkdir -p $W/fs/podman/storage
$P rm -f luma-cast-ui-gcc-build >/dev/null 2>&1 || true
$P run --rm --name luma-cast-ui-gcc-build --security-opt label=disable -v "$R:/build" -w /build \
  --memory 12g --cpus 12 registry.fedoraproject.org/fedora:44 bash -c "
    set -euo pipefail
    printf '%%_smp_build_ncpus 12\n%%_lto_cflags %%{nil}\n' > /root/.rpmmacros
    dnf5 -y -q install rpm-build dnf5-plugins >/dev/null
    dnf5 -y -q install in/luma-developer-platform-0.1.0-*.rpm in/luma-developer-platform-devel-*.rpm >/dev/null
    dnf5 -y -q builddep SPECS/gnome-control-center.spec >/dev/null
    rpmbuild -ba --nocheck --define '_topdir /build' SPECS/gnome-control-center.spec
    rpm2cpio RPMS/x86_64/gnome-control-center-50.4-$REL.fc44.x86_64.rpm > /tmp/p.cpio
    mkdir -p /tmp/x && cd /tmp/x && cpio -idm --quiet < /tmp/p.cpio
    echo sound-strings \$(strings usr/bin/gnome-control-center | grep -c 'New Output Devices')
    echo cast-strings \$(strings usr/bin/gnome-control-center | grep -c 'Remembered Screens')
    echo bg-strings \$(strings usr/bin/gnome-control-center | grep -c 'background-activity')
    echo BUILD-OK"
