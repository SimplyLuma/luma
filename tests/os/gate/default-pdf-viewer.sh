#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Release gate checks: Viewer (luma-viewer) opens PDFs and images, and GNOME's
# Document Viewer (Papers, Evince) is not part of Luma. Run as root inside a
# disposable VM installed from the candidate image or from a nightly medium.
# Needs no display and no network. Prints one JSON line per check ({"check",
# "result": "pass"|"fail"|"skip", "detail"}) and exits 1 if any check failed.
# The throwaway accounts it creates are removed again.
#
#   default-pdf-viewer.sh
#
# Checks run in the environment a GNOME session would have:
# XDG_CURRENT_DESKTOP=GNOME plus what systemd's environment.d generators give
# a user session (the launcher policy's XDG_DATA_DIRS).
set -uo pipefail
viewer=org.projectluma.Viewer.desktop
pdf_types="application/pdf application/x-bzpdf application/x-gzpdf application/x-xzpdf application/x-ext-pdf"
image_types="image/png image/jpeg image/gif image/bmp image/tiff image/webp image/avif image/heif image/svg+xml"
fresh=luma-gate-pdf
migrated=luma-gate-pdf-old
failed=0
work=$(mktemp -d /var/tmp/luma-gate-pdf.XXXXXX)
chmod 0755 "$work"

emit() {
  python3 -c 'import json, sys; print(json.dumps({"check": sys.argv[1], "result": sys.argv[2], "detail": sys.argv[3][-600:]}))' "$1" "$2" "$3"
  [ "$2" = fail ] && failed=1
  return 0
}
cleanup() {
  for u in "$fresh" "$migrated"; do pkill -KILL -u "$u" >/dev/null 2>&1 || true; done
  sleep 1
  for u in "$fresh" "$migrated"; do userdel -r "$u" >/dev/null 2>&1 || true; done
  rm -rf "$work"
}
trap cleanup EXIT

session_env() {
  local u=$1 home runtime line gen
  home=$(getent passwd "$u" | cut -d: -f6)
  runtime="$work/run-$u"
  install -d -m 0700 -o "$u" -g "$u" "$runtime"
  env_lines=("HOME=$home" "USER=$u" "LOGNAME=$u" "PATH=/usr/local/bin:/usr/bin:/bin"
             "XDG_CURRENT_DESKTOP=GNOME" "XDG_SESSION_DESKTOP=gnome" "XDG_SESSION_TYPE=wayland"
             "XDG_RUNTIME_DIR=$runtime" "DBUS_SESSION_BUS_ADDRESS=unix:path=$runtime/no-session-bus")
  for gen in /usr/lib/systemd/user-environment-generators/*; do
    [ -x "$gen" ] || continue
    while IFS= read -r line; do
      case $line in [A-Za-z_]*=*) env_lines+=("$line") ;; esac
    done < <(cd /tmp && runuser -u "$u" -- env -i "${env_lines[@]}" "$gen" 2>/dev/null)
  done
}
as_session() {
  local u=$1
  shift
  session_env "$u"
  (cd /tmp && runuser -u "$u" -- env -i "${env_lines[@]}" "$@")
}
defaults_not_viewer() {
  # defaults_not_viewer USER TYPES...: the types whose default is not Viewer.
  local u=$1 type got bad=
  shift
  for type in "$@"; do
    got=$(as_session "$u" gio mime "$type" 2>&1 | sed -n 's/^Default application for .*: //p' | head -n 1)
    [ "$got" = "$viewer" ] || bad="$bad $type=${got:-none}"
  done
  printf '%s' "$bad"
}

# 1. Viewer is installed and declares every PDF type.
if ! rpm -q luma-viewer >/dev/null 2>&1; then
  emit viewer-installed fail 'luma-viewer is not installed'
  exit 1
fi
emit viewer-installed pass "$(rpm -q luma-viewer)"
declared=$(sed -n 's/^MimeType=//p' "/usr/share/applications/$viewer")
missing=
for type in $pdf_types; do
  case ";$declared" in *";$type;"*) ;; *) missing="$missing $type" ;; esac
done
if [ -z "$missing" ]; then emit viewer-declares-pdf-types pass "$pdf_types"
else emit viewer-declares-pdf-types fail "Viewer's desktop entry does not declare:$missing"; fi

# 2. Document Viewer is not on the machine: no system Flatpak, no launcher.
flatpaks=$(flatpak list --system --app --columns=application 2>/dev/null | grep -E '^org\.gnome\.(Papers|Evince)$' | tr '\n' ' ')
if [ -z "$flatpaks" ]; then emit no-document-viewer-flatpak pass 'org.gnome.Papers and org.gnome.Evince are not installed'
else emit no-document-viewer-flatpak fail "installed system Flatpaks: $flatpaks"; fi
if grep -Fxq 'org.gnome.Papers luma-viewer' /usr/share/luma/system-flatpak-replacements.txt 2>/dev/null; then
  emit papers-on-replacement-list pass 'installs that still carry the Papers Flatpak lose it at boot'
else
  emit papers-on-replacement-list fail 'org.gnome.Papers is missing from /usr/share/luma/system-flatpak-replacements.txt'
fi

useradd -m "$fresh" || { emit fresh-account fail "useradd $fresh failed"; exit 1; }
visible=$(as_session "$fresh" python3 -W ignore -c '
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio
hits = [a.get_id() for a in Gio.AppInfo.get_all()
        if a.should_show() and any(n in (a.get_id() or "").lower() for n in ("papers", "evince"))]
print(" ".join(sorted(hits)))
' 2>"$work/visible.err" || tail -n 2 "$work/visible.err")
if [ -z "$visible" ]; then emit no-document-viewer-launcher pass 'no visible Papers or Evince launcher'
else emit no-document-viewer-launcher fail "visible: $visible"; fi

# 3. A fresh account opens PDFs and images in Viewer.
bad=$(defaults_not_viewer "$fresh" $pdf_types)
if [ -z "$bad" ]; then emit gio-mime-pdf-types pass "every PDF type defaults to $viewer"
else emit gio-mime-pdf-types fail "not $viewer:$bad"; fi
bad=$(defaults_not_viewer "$fresh" $image_types)
if [ -z "$bad" ]; then emit gio-mime-image-types pass "every image type defaults to $viewer"
else emit gio-mime-image-types fail "not $viewer:$bad"; fi

# 4. An account from before: its personal list names Papers for PDFs and a
#    browser for compressed PDFs, and keeps a deliberate PNG choice. The
#    once-per-account step (enabled for every user) hands the first two back.
if [ -L /usr/lib/systemd/user/default.target.wants/luma-default-apps-migration.service ]; then
  emit migration-enabled pass 'luma-default-apps-migration.service is wanted by every user session'
else
  emit migration-enabled fail 'luma-default-apps-migration.service is not enabled for user sessions'
fi
useradd -m "$migrated" || { emit migrated-account fail "useradd $migrated failed"; exit 1; }
home=$(getent passwd "$migrated" | cut -d: -f6)
install -d -o "$migrated" -g "$migrated" "$home/.config" "$home/.local/share/applications"
printf '[Desktop Entry]\nType=Application\nName=Gate Browser\nExec=true %%u\nCategories=Network;WebBrowser;\nMimeType=application/pdf;application/x-gzpdf;x-scheme-handler/https;\n' \
  >"$home/.local/share/applications/luma-gate-browser.desktop"
printf '[Desktop Entry]\nType=Application\nName=Gate Painter\nExec=true %%f\nCategories=Graphics;\nMimeType=image/png;\n' \
  >"$home/.local/share/applications/luma-gate-painter.desktop"
printf '[Default Applications]\napplication/pdf=org.gnome.Papers.desktop\napplication/x-gzpdf=luma-gate-browser.desktop\nimage/png=luma-gate-painter.desktop\n' \
  >"$home/.config/mimeapps.list"
chown -R "$migrated:$migrated" "$home/.config" "$home/.local"
out=$(as_session "$migrated" /usr/libexec/luma-default-apps-migration 2>&1)
bad=$(defaults_not_viewer "$migrated" application/pdf application/x-gzpdf)
png=$(as_session "$migrated" gio mime image/png 2>&1 | sed -n 's/^Default application for .*: //p' | head -n 1)
if [ -z "$bad" ] && [ "$png" = luma-gate-painter.desktop ]; then
  emit migration-hands-pdf-to-viewer pass "$out"
else
  emit migration-hands-pdf-to-viewer fail "not Viewer:${bad:- none}; image/png=$png; $out"
fi
if [ -e "$home/.local/state/luma/default-apps-migration/viewer-1" ]; then
  emit migration-runs-once pass 'the account is stamped; later logins skip the step'
else
  emit migration-runs-once fail 'no stamp after the migration ran'
fi

exit "$failed"
