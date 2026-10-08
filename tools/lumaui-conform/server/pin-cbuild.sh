#!/bin/sh
# lumaui-conform C/C++ build image: the ThinkPad toolbox's -devel packages, each at the
# version-release of its source package on the ThinkPad (thinkpad-srcvr.txt), so the
# headers match the libraries the image runs. Luma-built sources come from rpms-dev/
# (the build host's pool); where the pool has no -devel, Fedora's headers are laid
# in without dependencies, as the ThinkPad toolbox does (its libadwaita-devel is Fedora's).
set -eu
export LC_ALL=C
cd /tmp/cbuild
# Headers match the libraries this image runs: each source package's version-release here.
rpm -qa --qf '%{SOURCERPM} %{VERSION}-%{RELEASE}\n' | sed 's/-[^-]*-[^-]*\.src\.rpm / /' | sort -u > thinkpad-srcvr.txt
# The patched-upstream apps' runtime dependencies (from their Luma RPMs in rpms-app/: Filer,
# Settings), so their services' schemas and libraries are there; the capture runs the fresh build.
if ls rpms-app/*.rpm >/dev/null 2>&1; then
  reqs=$(rpm -qp --nosignature --requires rpms-app/*.rpm | grep -v -E '^(rpmlib\(|/|config\()|luma|nautilus|gnome-control-center' | sed 's/ .*//' | sort -u)
  dnf -y -q install --setopt=install_weak_deps=False $reqs
fi
tools="gcc gcc-c++ meson ninja-build vala sassc ccache python3-devel gettext desktop-file-utils itstool
 libappstream-glib appstream blueprint-compiler rpm-build patch git-core xmlto docbook-style-xsl libxslt gi-docgen
 glib2-devel gobject-introspection-devel"
names="$(cat cbuild-devel.txt) $tools"
dnf -q repoquery --latest-limit=1 --qf '%{name} %{source_name}\n' $names 2>/dev/null | sort -u > map.txt
want= nodeps=
while read -r name src; do
  vr=$(awk -v s="$src" '$1 == s {print $2}' thinkpad-srcvr.txt | tail -1)
  luma=$(awk -v s="$src" '$1 == s {print $2}' thinkpad-srcvr.txt | grep '\.luma\.' | tail -1)
  if [ -n "$luma" ]; then
    f=$(ls rpms-dev/"$name-$luma".*.rpm 2>/dev/null | head -1)
    if [ -n "$f" ]; then want="$want $PWD/$f"; else nodeps="$nodeps $name"; fi
  elif [ -n "$vr" ]; then
    want="$want $name-$vr"
  else
    want="$want $name"  # a build tool the ThinkPad does not have
  fi
done < map.txt
# Headers first, so nothing below asks for a Fedora runtime in place of a Luma one.
if [ -n "$nodeps" ]; then
  mkdir -p nd && (cd nd && dnf -q download $nodeps)
  rpm -i --nodeps --excludedocs nd/*.rpm
  echo "headers without dependencies (Luma runtime, Fedora headers):$nodeps"
fi
# The ThinkPad's Luma-built runtime packages are off limits to this transaction.
exclude=$(grep '\.luma\.' /usr/share/lumaui-conform/rpmqa.txt | awk '{print $1}' | paste -sd, -)
dnf -y -q install --setopt=install_weak_deps=False --exclude="$exclude" $want
# Put back any runtime package a -devel dependency moved, then check nothing is left moved.
back=$(join <(rpm -qa --qf '%{NAME} %{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n' | sort) /usr/share/lumaui-conform/rpmqa.txt | awk '$2 != $3 {print $3}')
[ -z "$back" ] || dnf -y -q install --allowerasing --setopt=install_weak_deps=False $back
# The runtime stays the ThinkPad's: nothing above may have moved a library it shares.
join <(rpm -qa --qf '%{NAME} %{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n' | sort) /usr/share/lumaui-conform/rpmqa.txt \
  | awk '$2 != $3 {print "moved " $3 " -> " $2}' | tee moved.txt
[ ! -s moved.txt ] || { echo "the build tools moved runtime packages" >&2; exit 3; }
