#!/bin/sh
# Native Settings launcher: install only inside the private preview after PASS.
# The native application accepts this exact override before bus registration.
set -eu
preview_root="$HOME/.local/share/luma-dev/lumaui-settings"
exec toolbox run -c luma-dev-f44 env \
  -u LUMA_SETTINGS_FIXTURE -u LUMA_SETTINGS_PAGE \
  -u LUMA_SETTINGS_VARIANT -u LUMA_SETTINGS_QUERY \
  -u LUMA_SETTINGS_SMOKE -u LUMA_SETTINGS_SMOKE_PHONE \
  -u GSETTINGS_BACKEND \
  LUMA_SETTINGS_APPLICATION_ID=org.gnome.Settings.LumaUIPreview \
  LD_LIBRARY_PATH="$preview_root/kit-prefix/lib64" \
  "$preview_root/bin/gnome-control-center" "$@"
