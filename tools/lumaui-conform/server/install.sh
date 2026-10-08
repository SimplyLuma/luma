#!/bin/sh
# lumaui-conform: install or update the conform service on the build server, from the Mac.
#   tools/lumaui-conform/server/install.sh [--image] [--cbuild] [--design]
# Always: tools/lumaui-conform -> /srv/lumaui-conform/tools/lumaui-conform (the server's
#         plumbing and default measuring parts), queue-head.py and conform-serve.sh.
# --image:  rebuild localhost/lumaui-conform-thinkpad (Luma RPMs copied from the build
#           host's pool, each checked against the ThinkPad's digest; the ThinkPad's
#           hicolor/locolor icons and MIME database), then remove the previous build.
#           Re-run when the ThinkPad's packages or installed apps change.
# --cbuild: rebuild localhost/lumaui-conform-cbuild (the C/C++ route: toolchain, -devel
#           packages at the ThinkPad's versions, the harness preload library).
# --design: rsync the design folder (LUMAUI_CONFORM_STUDIO_ROOT) to /srv/lumaui-conform/design.
# Touches nothing else on the server: not /srv/luma-build, the pipeline or its volumes.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
tools=$(dirname "$here")
server=${LUMAUI_CONFORM_SERVER:?Set LUMAUI_CONFORM_SERVER to your build host}
port=${LUMAUI_CONFORM_PORT:-6767}
design=${LUMAUI_CONFORM_STUDIO_ROOT:-$HOME/Downloads/Operating system window treatment}
ssh_s() { ssh -p "$port" "$server" "$@"; }
rsync_s() { rsync -e "ssh -p $port" "$@"; }

ssh_s 'mkdir -p /srv/lumaui-conform/tools /srv/lumaui-conform/runs /srv/lumaui-conform/slots /srv/lumaui-conform/image/rpms /srv/lumaui-conform/design'
rsync_s -a --delete --exclude __pycache__ --exclude '*.20[0-9][0-9][01][0-9][0-3][0-9]' "$tools/" "$server:/srv/lumaui-conform/tools/lumaui-conform/"
# Publish the helper first, then the service entrypoint. Each rename is atomic;
# in-flight shell functions keep running, and new invocations always see a helper.
rsync_s -a "$here/queue-head.py" "$server:/srv/lumaui-conform/queue-head.py.new"
ssh_s 'python3 -m py_compile /srv/lumaui-conform/queue-head.py.new && chmod 644 /srv/lumaui-conform/queue-head.py.new && mv -f /srv/lumaui-conform/queue-head.py.new /srv/lumaui-conform/queue-head.py'
rsync_s -a "$here/conform-serve.sh" "$server:/srv/lumaui-conform/conform-serve.sh.new"
ssh_s 'bash -n /srv/lumaui-conform/conform-serve.sh.new && chmod 755 /srv/lumaui-conform/conform-serve.sh.new && mv -f /srv/lumaui-conform/conform-serve.sh.new /srv/lumaui-conform/conform-serve.sh'
echo "tools, queue-head.py and conform-serve.sh installed"

for arg in "$@"; do
  case "$arg" in
    --design)
      # A conform run holds a shared lock from reference keying through capture.
      # rsync holds the exclusive lock for its entire remote transfer.
      rsync_s -a --delete --exclude '*.bak*' --rsync-path='flock /srv/lumaui-conform/design.lock rsync' "$design/" "$server:/srv/lumaui-conform/design/"
      echo "design folder synced" ;;
    --image)
      # The ThinkPad's app icons and MIME database: which apps are installed there decides how a
      # file or app icon looks (read-only tar; nothing on the ThinkPad changes).
      # (When the ThinkPad is offline, the server keeps the last copy.)
      share=$(mktemp -d "${TMPDIR:-/tmp}/lumaui-conform-share.XXXXXX")
      if ssh -o BatchMode=yes -o ConnectTimeout=10 "${LUMAUI_HOST:-nick@192.168.1.143}" 'tar -C / -cf - usr/share/icons/hicolor usr/share/icons/locolor usr/share/mime' > "$share/thinkpad-share.tar"; then
        rsync_s -a "$share/thinkpad-share.tar" "$server:/srv/lumaui-conform/image/"
      else
        echo "the ThinkPad is offline: keeping the server's thinkpad-share.tar"
      fi
      rsync_s -a "$here/Containerfile" "$here/pin.sh" "$here/thinkpad-rpmqa.txt" "$server:/srv/lumaui-conform/image/"
      rm -rf "$share"
      # name  ThinkPad SIGMD5  path in the build host's pool (read-only)
      # The Luma packages at exactly the NEVRAs that ship: the nightly's own pin list
      # (read-only), each found in the build host's pool.
      ssh_s 'set -eu; cd /srv/lumaui-conform/image/rpms; rm -f *.rpm
C=/mnt/luma-secondary/luma-build/os-release/fs/src/nightly/config/desktop/packages.txt
for n in gtk4 libadwaita luma-developer-platform luma-developer-platform-sdk prairie-core-apps prairie-icon-theme \
         google-figtree-fonts google-caveat-fonts mutter mutter-common luma-shell-state gtk3 gnome-settings-daemon \
         gnome-control-center-filesystem ibus-libs libhandy gnome-calculator fwupd nautilus-extensions luma-stage-preview; do
  nevra=$(grep -E "^$n-[0-9]" "$C" | head -1)
  [ -n "$nevra" ] || { echo "$n is not in the nightly pin list" >&2; exit 3; }
  f=$(find /mnt/luma-secondary/luma-build/os-release/incoming /mnt/luma-secondary /srv/luma-build -maxdepth 7 -name "$nevra.rpm" -not -path "*/os-release/fs/*" 2>/dev/null | head -1)
  [ -n "$f" ] || { echo "$nevra is not in the pool" >&2; exit 3; }
  cp -p "$f" .
done
cp "$C" ../ship-packages.txt
# Every other package at the versions of the candidate build (its rpm -qa manifest).
m=$(ls -t /mnt/luma-secondary/luma-build/os-release/fs/builds/*/packages-installed.tsv | head -1)
awk -F"\t" "{print \$1, \$1\"-\"\$3\"-\"\$4\".\"\$5}" "$m" | sort > ../ship-rpmqa.txt
echo "Fedora packages pinned to $m"
cd /srv/lumaui-conform/image
old=$(podman image inspect -f "{{.Id}}" localhost/lumaui-conform-thinkpad 2>/dev/null || true)
podman build --pull=never --layers=false -t localhost/lumaui-conform-thinkpad . > build.log 2>&1 || { tail -30 build.log >&2; exit 4; }
grep -E "^packages:|cannot pin" build.log || true
new=$(podman image inspect -f "{{.Id}}" localhost/lumaui-conform-thinkpad)
# Only this image'"'"'s previous build goes; nothing else on the server is pruned.
[ -n "$old" ] && [ "$old" != "$new" ] && podman rmi "$old" >/dev/null 2>&1 || true
podman images localhost/lumaui-conform-thinkpad'
      ;;
    --cbuild)
      # The C/C++ route's image, on top of the one above (Containerfile.cbuild).
      ssh_s 'mkdir -p /srv/lumaui-conform/image-cbuild/cbuild/rpms-dev'
      rsync_s -a "$here/Containerfile.cbuild" "$server:/srv/lumaui-conform/image-cbuild/Containerfile"
      rsync_s -a "$here/pin-cbuild.sh" "$here/preload.c" "$here/cbuild-devel.txt" "$here/thinkpad-srcvr.txt" "$server:/srv/lumaui-conform/image-cbuild/cbuild/"
      ssh_s 'set -eu; cd /srv/lumaui-conform/image-cbuild/cbuild/rpms-dev; rm -f *.rpm
f=/mnt/luma-secondary/save-sheet-20260919/tuple/gtk4-devel-4.22.4-1.luma.19.preview1.fc44.x86_64.rpm
[ "$(rpm -qp --nosignature --qf %{VERSION}-%{RELEASE} "$f")" = 4.22.4-1.luma.19.preview1.fc44 ] && cp -p "$f" .
mkdir -p ../rpms-app && cd ../rpms-app && rm -f *.rpm
C=/mnt/luma-secondary/luma-build/os-release/fs/src/nightly/config/desktop/packages.txt
for n in nautilus nautilus-extensions gnome-control-center; do
  nevra=$(grep -E "^$n-[0-9]" "$C" | head -1)
  f=$(find /mnt/luma-secondary/luma-build/os-release/incoming -maxdepth 4 -name "$nevra.rpm" 2>/dev/null | head -1)
  [ -n "$f" ] || { echo "$nevra is not in the pool" >&2; exit 3; }
  cp -p "$f" .
done
# Patched-upstream apps build from their Luma SRPM (the upstream tarball) with the series from the worktree.
mkdir -p /srv/lumaui-conform/srpms && cd /srv/lumaui-conform/srpms
I=/mnt/luma-secondary/luma-build/os-release/incoming
for f in $I/shell-135-filer-58-dock-folders-20260920/SRPMS/nautilus-50.2.2-1.luma.58.surfacepreview20260920.fc44.src.rpm \
         $I/scroll-speed-20260921/SRPMS/gnome-control-center-50.4-1.luma.29.preview20260921.1.fc44.src.rpm; do
  [ -e "$(basename "$f")" ] || cp -p "$f" .
done
cd /srv/lumaui-conform/image-cbuild
old=$(podman image inspect -f "{{.Id}}" localhost/lumaui-conform-cbuild 2>/dev/null || true)
podman build --pull=never --layers=false -t localhost/lumaui-conform-cbuild . > build.log 2>&1 || { tail -40 build.log >&2; exit 4; }
grep -E "headers without|moved" build.log || true
new=$(podman image inspect -f "{{.Id}}" localhost/lumaui-conform-cbuild)
[ -n "$old" ] && [ "$old" != "$new" ] && podman rmi "$old" >/dev/null 2>&1 || true
podman images localhost/lumaui-conform-cbuild'
      ;;
    *) echo "install.sh: unknown option $arg" >&2; exit 2 ;;
  esac
done
