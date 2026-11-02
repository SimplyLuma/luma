#!/bin/sh
# lumaui-conform: install or update the conform service on the build server, from the Mac.
#   tools/lumaui-conform/server/install.sh [--image] [--cbuild] [--design]
# Always: tools/lumaui-conform -> /srv/lumaui-conform/tools/lumaui-conform (the server's
#         plumbing and default measuring parts), queue-head.py and conform-serve.sh.
# --image:  rebuild localhost/lumaui-conform-thinkpad at the newest nightly (server/build-images.sh:
#           Luma RPMs from that nightly's pool at its manifest's NEVRAs, Fedora converged on the
#           same manifest; the ThinkPad's hicolor/locolor icons and MIME database), then remove
#           the previous build. Re-run after a nightly, or when the ThinkPad's apps change.
# --cbuild: rebuild localhost/lumaui-conform-cbuild (the C/C++ route: toolchain, -devel
#           headers, the harness preload library) on top of it.
# Images and runs live in /srv/dev-storage/projects/lumaui-conform (containers/storage, runs,
# caches), not on the server's root filesystem.
# --design: rsync the design folder (LUMAUI_CONFORM_STUDIO_ROOT) to /srv/lumaui-conform/design.
# Touches nothing else on the server: not /srv/luma-build, the pipeline or its volumes.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
tools=$(dirname "$here")
server=${LUMAUI_CONFORM_SERVER:?Set LUMAUI_CONFORM_SERVER to your build host}
port=${LUMAUI_CONFORM_PORT:-6767}
design=${LUMAUI_CONFORM_STUDIO_ROOT:-$HOME/Downloads/Operating system window treatment}
key_opts=${LUMAUI_CONFORM_KEY:+-i $LUMAUI_CONFORM_KEY -o IdentitiesOnly=yes}
ssh_s() { ssh -p "$port" $key_opts "$server" "$@"; }
rsync_s() { rsync -e "ssh -p $port $key_opts" "$@"; }

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
      [ -f "$here/thinkpad-rpmqa.txt" ] && rsync_s -a "$here/thinkpad-rpmqa.txt" "$server:/srv/lumaui-conform/image/"
      rm -rf "$share"
      # Built on the server in the service's storage on the data disk, through luma-heavy.
      ssh_s 'bash /srv/lumaui-conform/tools/lumaui-conform/server/build-images.sh thinkpad'
      ;;
    --cbuild)
      # The C/C++ route's image, on top of the one above (Containerfile.cbuild).
      ssh_s 'bash /srv/lumaui-conform/tools/lumaui-conform/server/build-images.sh cbuild'
      ;;
    *) echo "install.sh: unknown option $arg" >&2; exit 2 ;;
  esac
done
