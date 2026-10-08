#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-twin/inputs.env"

build_dir=${LUMA_TWIN_BUILD_DIR:-$repo_root/build/mobile/fp6-twin}
ssh_key="$build_dir/luma-fp6-twin-ed25519"
ssh_port=${LUMA_TWIN_SSH_PORT_OVERRIDE:-$LUMA_TWIN_SSH_PORT}

[ -f "$ssh_key" ] || {
  printf 'error: missing twin SSH key; run prepare-fp6-twin.sh first\n' >&2
  exit 1
}

ssh_options=(
  -i "$ssh_key"
  -p "$ssh_port"
  -o BatchMode=yes
  -o ConnectTimeout=5
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
)

package_inputs=$(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' \
  "$repo_root/config/mobile/ui-packages.txt" \
  "$repo_root/config/mobile/fp6-twin/ui-packages.txt" | tr '\n' ' ')

# Package values are source-controlled RPM NEVRAs containing no shell syntax.
# shellcheck disable=SC2086
ssh "${ssh_options[@]}" luma@127.0.0.1 \
  "sudo dnf install -y $package_inputs"

ssh "${ssh_options[@]}" luma@127.0.0.1 'sudo install -m 0644 /dev/stdin /etc/gdm/custom.conf' <<'GDM_CONFIG'
[daemon]
AutomaticLoginEnable=True
AutomaticLogin=luma
DefaultSession=phosh
InitialSetupEnable=false
WaylandEnable=true
GDM_CONFIG

ssh "${ssh_options[@]}" luma@127.0.0.1 \
  'sudo install -d -m 0755 /var/lib/AccountsService/users && sudo install -m 0600 /dev/stdin /var/lib/AccountsService/users/luma' <<'ACCOUNT_CONFIG'
[User]
Session=phosh
SystemAccount=false
ACCOUNT_CONFIG

ssh "${ssh_options[@]}" luma@127.0.0.1 \
  "gsettings set org.gnome.desktop.session idle-delay 'uint32 0' && gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'"

if ! ssh "${ssh_options[@]}" luma@127.0.0.1 \
  'test -f /var/lib/luma-fp6-twin/phosh-config-v1'; then
  ssh "${ssh_options[@]}" luma@127.0.0.1 \
    'sudo systemctl enable gdm.service --force && sudo systemctl set-default graphical.target && sudo systemctl restart --no-block gdm.service'
fi

ssh "${ssh_options[@]}" luma@127.0.0.1 \
  "for attempt in \$(seq 1 180); do for socket in /run/user/1000/wayland-*; do [ -S \"\$socket\" ] || continue; display=\${socket##*/}; if env XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=\"\$display\" wlr-randr --output Virtual-1 --mode ${FP6_DISPLAY_WIDTH}x${FP6_DISPLAY_HEIGHT}; then sudo install -d -m 0755 /var/lib/luma-fp6-twin; sudo touch /var/lib/luma-fp6-twin/phosh-config-v1; exit 0; fi; done; sleep 1; done; printf 'error: Phosh output did not accept the FP6 mode\n' >&2; exit 1"

printf 'FP6 twin Phosh session enabled at %sx%s.\n' "$FP6_DISPLAY_WIDTH" "$FP6_DISPLAY_HEIGHT"
