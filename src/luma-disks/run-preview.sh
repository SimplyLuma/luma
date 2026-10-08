#!/bin/sh
# Launch the staged Disks preview without enabling real-device writes.
set -eu
preview_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
export PYTHONPATH="$preview_dir/python:$preview_dir/src/luma-disks"
export LUMA_APPKIT_ICON_PATH="$preview_dir/python/icons"
unset LUMA_DISKS_FIXTURE LUMA_DISKS_SELECTED_DRIVE LUMA_DISKS_SELECTED_VOLUME LUMA_DISKS_VIEW
export LUMA_DISKS_ALLOW_WRITES=0
exec python3 -m luma_disks "$@"
