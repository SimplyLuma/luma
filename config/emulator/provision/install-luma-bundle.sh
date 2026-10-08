#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Install a verified Luma package bundle into the emulator's factory image.
#
# This is the step that turns a Fedora guest into a Luma guest. It runs inside
# the guest during provisioning, after the Fedora substrate is in place, so
# Luma's downstream packages replace the stock ones rather than racing them.
#
# Every check here refuses rather than degrades. An image that half-installed
# Luma and carried on would produce screenshots that look right and are wrong,
# which is worse than no image at all.

set -euo pipefail

provision_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

bundle_root=${1:-/var/tmp/luma-emulator-bundle}

log() { printf '\n--- %s\n' "$1"; }

archive=$(find "$bundle_root" -maxdepth 1 \( -name '*.tar.gz' -o -name '*.tar.zst' \) | head -1)
if [ -z "$archive" ]; then
  echo "install-luma-bundle: no bundle archive under $bundle_root" >&2
  exit 2
fi

log "Extracting $(basename "$archive")"
work="$bundle_root/extracted"
rm -rf "$work"
mkdir -p "$work"
case "$archive" in
  *.tar.gz)  tar -xzf "$archive" -C "$work" ;;
  *.tar.zst) tar --use-compress-program=unzstd -xf "$archive" -C "$work" ;;
  *) echo "install-luma-bundle: unrecognised archive $archive" >&2; exit 2 ;;
esac

root=$(find "$work" -maxdepth 1 -mindepth 1 -type d | head -1)
test -n "$root" || { echo "install-luma-bundle: bundle has no root directory" >&2; exit 2; }
cd "$root"

test -f manifest.json || { echo "install-luma-bundle: no manifest.json" >&2; exit 2; }
test -f SHA256SUMS || { echo "install-luma-bundle: no SHA256SUMS" >&2; exit 2; }

log "Verifying every package digest"
# The Mac verified the archive as a whole; this verifies each file the guest is
# about to install, on the machine that will install it.
sha256sum --quiet --check SHA256SUMS

log "Verifying package headers against their filenames"
architecture=$(uname -m)
count=0
for package in rpms/*.rpm; do
  read -r name arch <<<"$(rpm -qp --nosignature --qf '%{NAME} %{ARCH}\n' "$package" 2>/dev/null)"
  base=$(basename "$package" .rpm)
  case "$base" in
    "$name"-*) ;;
    *) echo "install-luma-bundle: $base does not carry package $name" >&2; exit 3 ;;
  esac
  if [ "$arch" != "$architecture" ] && [ "$arch" != "noarch" ]; then
    echo "install-luma-bundle: $base is $arch, not $architecture or noarch" >&2
    exit 3
  fi
  count=$((count + 1))
done
echo "verified $count packages"

log "Installing the Luma package set as one transaction"
compatibility_arch=$(python3 -c 'import json; m=json.load(open("manifest.json")); print((m.get("compatibility") or {}).get("architecture", ""))')
software_video=$(python3 -c 'import json; m=json.load(open("manifest.json")); print((m.get("compatibility") or {}).get("softwareVideo", ""))')
if [ -n "$software_video" ]; then
  [ "$compatibility_arch" = aarch64 ] && [ "$software_video" = surfaceflinger-rgb-v1 ] || {
    echo "install-luma-bundle: unsupported software-video profile" >&2; exit 3;
  }
fi
compatibility_packages=()
if [ -n "$compatibility_arch" ]; then
  [ "$compatibility_arch" = "$architecture" ] || {
    echo "install-luma-bundle: compatibility image architecture mismatch" >&2
    exit 3
  }
  # Optional engines remain below the native OS. No x86 translation runner is
  # implicitly added to an ARM guest.
  compatibility_packages=(waydroid)
  if [ -n "$software_video" ]; then compatibility_packages+=(e2fsprogs); fi
  if [ "$architecture" = x86_64 ]; then
    compatibility_packages+=(wine ntsync-autoload wine-dxvk winetricks
      wine-mono mingw32-wine-gecko mingw64-wine-gecko)
  fi
fi
# One transaction so dependencies resolve together and a failure leaves nothing
# half-applied. Downgrades are allowed because Fedora's updates repository may
# carry a newer upstream build than the release Luma has accepted; the accepted
# Luma release is the one this image is supposed to have.
if ! dnf5 -y install --allow-downgrade rpms/*.rpm "${compatibility_packages[@]}"; then
  echo "install-luma-bundle: dnf5 refused the transaction" >&2
  exit 4
fi

log "Confirming every bundled package is the installed one"
missing=0
while read -r nevra; do
  [ -n "$nevra" ] || continue
  name=${nevra%-*-*}
  if ! rpm -q "$name" >/dev/null 2>&1; then
    echo "NOT INSTALLED: $name" >&2
    missing=$((missing + 1))
    continue
  fi
  installed=$(rpm -q --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}' "$name")
  if [ "$installed" != "$nevra" ]; then
    echo "MISMATCH: expected $nevra, installed $installed" >&2
    missing=$((missing + 1))
  fi
done < <(python3 -c "
import json
manifest = json.load(open('manifest.json'))
for package in manifest['packages']:
    if package.get('sha256'):
        print(package['nevra'])
")
if [ "$missing" -gt 0 ]; then
  echo "install-luma-bundle: $missing package(s) did not install as expected" >&2
  exit 5
fi

if [ -n "$compatibility_arch" ]; then
  log "Provisioning the pinned optional Android image pair"
  rpm -q luma-android-runtime luma-relay luma-application-installer
  # Factory provisioning only. Launches never download or initialize images.
  LUMA_ANDROID_IMAGE_CACHE="$PWD/runtime/cache" \
    bash runtime/scripts/android/prepare-pinned-images.sh "$architecture"
  android_images="$PWD/runtime/build/android/pinned/$architecture/images"
  graphics_args=()
  if [ -n "$software_video" ]; then
    python3 runtime/scripts/android/prepare-software-graphics-images.py \
      "$android_images" "$PWD/runtime/software-video" "$PWD/runtime/software-images"
    android_images="$PWD/runtime/software-images"
    graphics_args+=(--software-video-candidate)
  fi
  bash runtime/scripts/android/deploy-prairie-waydroid-images.sh "$android_images"
  python3 "$provision_dir/configure-android-graphics.py" "${graphics_args[@]}"
  waydroid init -f
fi

log "Recording what was admitted"
install -d -m 0755 /etc/luma-emulator
install -m 0644 manifest.json /etc/luma-emulator/luma-bundle-manifest.json
python3 -c "
import json
manifest = json.load(open('manifest.json'))
names = [p['nevra'] for p in manifest['packages'] if p.get('sha256')]
open('/etc/luma-emulator/luma-packages.txt', 'w').write('\n'.join(sorted(names)) + '\n')
# Bare names, so the Fedora step that follows can be told not to upgrade over
# anything Luma owns.
bare = sorted({p['name'] for p in manifest['packages'] if p.get('sha256')})
open('/etc/luma-emulator/luma-package-names.txt', 'w').write('\n'.join(bare) + '\n')
print(manifest['completeness'])
" > /etc/luma-emulator/luma-completeness.txt
cat /etc/luma-emulator/luma-completeness.txt

log "Applying the Luma composition overlay"
# Packages alone give upstream Phosh with Luma libraries under it. The overlay
# is what carries the session launcher, the device-class capabilities and the
# compiled dconf defaults — the dock, the layout, the wallpaper. Shared entries
# are installed now; scoped ones are staged and placed when a presentation mode
# is selected, mirroring composition installing only its device class.
if [ -d overlay ]; then
  install -d -m 0755 /etc/luma-emulator/overlay
  python3 - "$PWD" <<'OVERLAY'
import json, os, shutil, sys
root = sys.argv[1]
manifest = json.load(open(os.path.join(root, "manifest.json")))
staged = {}
for entry in manifest.get("overlay", []):
    source = os.path.join(root, "overlay", entry["scope"], entry["source"])
    if not os.path.isfile(source):
        raise SystemExit("overlay file missing from bundle: " + entry["source"])
    mode = int(entry["mode"], 8)
    if entry["scope"] == "shared":
        destination = entry["destination"]
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copy2(source, destination)
        os.chmod(destination, mode)
        print("installed " + destination)
    else:
        staging = os.path.join("/etc/luma-emulator/overlay", entry["scope"],
                               entry["source"])
        os.makedirs(os.path.dirname(staging), exist_ok=True)
        shutil.copy2(source, staging)
        os.chmod(staging, mode)
        staged.setdefault(entry["scope"], []).append(
            {"staged": staging, "destination": entry["destination"],
             "mode": entry["mode"]}
        )
        print("staged " + staging + " for " + entry["scope"])
with open("/etc/luma-emulator/overlay/placement.json", "w") as handle:
    json.dump(staged, handle, indent=2, sort_keys=True)
OVERLAY

  # A session entry for Luma's own handheld launcher. Fedora's phosh.desktop
  # runs upstream's phosh-session, which never exports the device class.
  if [ -f /etc/luma-emulator/overlay/mobile/scripts/mobile/luma-phosh-session ]; then
    install -D -m 0755 /etc/luma-emulator/overlay/mobile/scripts/mobile/luma-phosh-session \
      /usr/local/bin/luma-phosh-session
    install -d -m 0755 /usr/share/wayland-sessions
    cat > /usr/share/wayland-sessions/luma-handheld.desktop <<'SESSION'
[Desktop Entry]
Name=Luma Handheld
Comment=Project Luma handheld presentation
Exec=/usr/local/bin/luma-phosh-session
Type=Application
DesktopNames=Phosh:GNOME
SESSION
    echo "installed the Luma handheld session entry"
  fi
  dconf update || true
else
  echo "NO COMPOSITION OVERLAY IN THIS BUNDLE — the guest will run upstream Phosh chrome." >&2
fi

log "Verifying Figtree resolves"
# Fontconfig will silently substitute another face and render a screenshot that
# looks plausible with the wrong metrics, so this is checked explicitly rather
# than assumed from the package being installed.
if ! rpm -q google-figtree-fonts >/dev/null 2>&1; then
  echo "install-luma-bundle: google-figtree-fonts is not installed" >&2
  exit 6
fi
fc-cache -f >/dev/null 2>&1 || true
resolved=$(fc-match Figtree 2>/dev/null || true)
echo "fc-match Figtree -> $resolved"
case "$resolved" in
  Figtree*) ;;
  *)
    echo "install-luma-bundle: fontconfig does not resolve Figtree to Figtree" >&2
    exit 6
    ;;
esac
fc-list 2>/dev/null | grep -ci figtree | xargs echo "figtree faces:"

log "Luma bundle installed"
rm -rf "$work"
