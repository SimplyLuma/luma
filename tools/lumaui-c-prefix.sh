#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build LumaUI-1 (src/luma-platform) from a worktree inside the ThinkPad's
# `luma-dev-f44` toolbox and install it into a dev prefix that C apps build
# and run against. Nothing installed on the host changes.
#
#   tools/lumaui-c-prefix.sh              sync, build, install
#   tools/lumaui-c-prefix.sh --test       ... and run the meson tests (parity too),
#                                         headless (Xvfb); LUMAUI_TESTS="name..." picks some
#   tools/lumaui-c-prefix.sh --no-install --test   build and test only (part development)
#   tools/lumaui-c-prefix.sh --env        print the environment for the prefix
#
# Options: --src <worktree> (default: the one this script is in),
#          --host <user@host> (default: nick@192.168.1.143),
#          --prefix <dir on the host> (default: ~/Documents/.luma-dev/lumaui-prefix).
# Run on the ThinkPad itself with --local (no ssh, no sync).
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="nick@192.168.1.143"
PREFIX='$HOME/Documents/.luma-dev/lumaui-prefix'
TOOLBOX="luma-dev-f44"
TEST=0
INSTALL=1
LOCAL=0
ENV_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --src) SRC="$(cd "$2" && pwd)"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    --test) TEST=1; shift ;;
    --no-install) INSTALL=0; shift ;;
    --local) LOCAL=1; shift ;;
    --env) ENV_ONLY=1; shift ;;
    -h|--help) sed -n '3,17p' "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done

env_lines() {
  cat <<EOF
P=$PREFIX
export PKG_CONFIG_PATH=\$P/lib64/pkgconfig:\$P/share/pkgconfig\${PKG_CONFIG_PATH:+:\$PKG_CONFIG_PATH}
export LD_LIBRARY_PATH=\$P/lib64\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}
export GI_TYPELIB_PATH=\$P/lib64/girepository-1.0\${GI_TYPELIB_PATH:+:\$GI_TYPELIB_PATH}
export XDG_DATA_DIRS=\$P/share:\${XDG_DATA_DIRS:-/usr/local/share:/usr/share}
EOF
}

if [ "$ENV_ONLY" = 1 ]; then
  env_lines
  exit 0
fi

# The sources the build needs: the platform, and the kit's Python twin and the
# tokens for the parity test.
NAME="$(basename "$SRC")"
STAGE='$HOME/Documents/.luma-dev/lumaui-src/'"$NAME"

build_script() {
  cat <<EOF
set -euo pipefail
P=$PREFIX
S=$STAGE
cd "\$S/src/luma-platform"
# Parts join the build when their .c appears, which meson sees at setup:
# reconfigure every time (cheap; ninja stays incremental).
if [ ! -f _build-prefix/build.ninja ]; then
  meson setup _build-prefix --prefix="\$P" --libdir=lib64 --buildtype=debugoptimized
else
  meson setup --reconfigure _build-prefix >/dev/null
fi
ninja -C _build-prefix
if [ "$INSTALL" = 1 ]; then meson install -C _build-prefix --quiet; fi
if [ "$TEST" = 1 ]; then
  # Headless: each test gets its own X server (Xvfb), so no window reaches the
  # desktop, tests don't take focus from each other, and the frame clock
  # still ticks (broadway stalls without a browser).
  # (xvfb-run -a races when tests start together: pick the display by pid.)
  W="\$PWD/_build-prefix/xvfb-wrap"
  printf '%s\n' '#!/bin/bash' 'exec xvfb-run -n \$(( \$\$ % 5000 + 200 )) -s "-screen 0 1600x1000x24" "\$@"' > "\$W"
  chmod +x "\$W"
  env -u WAYLAND_DISPLAY GDK_BACKEND=x11 meson test -C _build-prefix --print-errorlogs \
    --wrapper "\$W" ${LUMAUI_TESTS:-}
fi
if [ "$INSTALL" = 1 ]; then echo "LumaUI-1 installed in \$P"; fi
EOF
}

if [ "$LOCAL" = 1 ]; then
  STAGE="$SRC"
  toolbox run -c "$TOOLBOX" bash -c "$(build_script)"
  exit 0
fi

ssh "$HOST" "mkdir -p $STAGE"
rsync -a --delete --exclude '_build*' --exclude '.git' \
  --include '/src/' --include '/src/luma-platform/***' \
  --include '/config/' --include '/config/shared/***' \
  --include '/scripts/' --include '/scripts/developer/***' \
  --exclude '*' \
  "$SRC/" "$HOST:${STAGE/\$HOME/~}/"
ssh "$HOST" "toolbox run -c $TOOLBOX bash -c $(printf '%q' "$(build_script)")"
echo
echo "Use it (inside the toolbox):"
env_lines
