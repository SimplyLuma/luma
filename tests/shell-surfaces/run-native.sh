#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
set -eu
export PRESENTATION_SOURCE=${PRESENTATION_SOURCE:-/source}
export PRESENTATION_AUTOMATION="$PRESENTATION_SOURCE/tests/shell-surfaces/native.js"
export XDG_CONFIG_HOME=/tmp/presentation-home/.config
mkdir -p "$XDG_CONFIG_HOME" /tmp/presentation-home/{Downloads,Documents,Pictures}
cat > "$XDG_CONFIG_HOME/user-dirs.dirs" <<'EOF'
XDG_DOWNLOAD_DIR="$HOME/Downloads"
XDG_DOCUMENTS_DIR="$HOME/Documents"
XDG_PICTURES_DIR="$HOME/Pictures"
EOF
exec bash "$PRESENTATION_SOURCE/tests/gnome-shell/presentation/run.sh"
