#!/usr/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Image build step 2: system policy. The files installed here are the ones
# scripts/vm/provision-desktop-image.sh installs on a live guest, plus the
# ADR-030 section 3 update and trust policy. Everything is a persistent
# default in /usr or /etc of the image; nothing is replayed at boot or login.

set -euo pipefail

src=/run/luma/source
rootfs=$src/image/luma-desktop/rootfs

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

# Desktop defaults and account policy (as provision-desktop-image.sh).
install -D -m 0644 "$src/config/desktop/dconf/profile/user" /etc/dconf/profile/user
install -D -m 0644 "$src/config/desktop/dconf/db/luma.d/00-luma-desktop" /etc/dconf/db/luma.d/00-luma-desktop
install -D -m 0644 "$src/config/desktop/dconf/db/gdm.d/00-prairie-login" /etc/dconf/db/gdm.d/00-prairie-login
install -D -m 0644 "$src/config/desktop/useradd" /etc/default/useradd
install -D -m 0644 "$src/config/desktop/gtk-3.0/settings.ini" /etc/gtk-3.0/settings.ini
install -D -m 0644 "$src/config/desktop/gtk-4.0/settings.ini" /etc/gtk-4.0/settings.ini
install -D -m 0644 "$src/config/boot/plymouthd.conf" /etc/plymouth/plymouthd.conf
install -D -m 0644 "$src/config/desktop/fprintd-no-idle-exit.conf" \
  /etc/systemd/system/fprintd.service.d/10-luma-no-idle-exit.conf
install -D -m 0644 "$src/config/desktop/waydroid-container-ordering.conf" \
  /etc/systemd/system/waydroid-container.service.d/10-luma-ordering.conf
dconf update

# Update and trust policy (ADR-030 sections 3 and 5). The image files are the
# reviewed copies under image/luma-desktop/rootfs.
(cd "$rootfs" && find . -type f -print0) | while IFS= read -r -d '' file; do
  file=${file#./}
  mode=0644
  [ -x "$rootfs/$file" ] && mode=0755
  install -D -m "$mode" "$rootfs/$file" "/$file"
done

# The immutable OS contains only public, signed app bundles. Flatpak deploys
# missing apps into persistent /var at first handoff; later OS updates and
# rollbacks preserve independent app updates and deliberate removals.
baseline=/run/luma/app-baseline
: "${LUMA_APP_BASELINE_SHA256:?The declared signed app baseline is required}"
python3 "$src/scripts/os/lib/app_baseline.py" "$baseline" \
  --shipping "$src/config/os/first-party-app-baseline.json" >/tmp/luma-app-input.json
[ "$(sha256sum "$baseline/manifest.json" | awk '{print $1}')" = "$LUMA_APP_BASELINE_SHA256" ] ||
  fail 'the declared application baseline changed'
install -d -m 0755 /usr/share/luma/app-baseline
for input in "$baseline"/*; do
  install -m 0644 "$input" "/usr/share/luma/app-baseline/$(basename "$input")"
done
python3 "$src/scripts/os/lib/app_baseline.py" /usr/share/luma/app-baseline \
  --shipping "$src/config/os/first-party-app-baseline.json" >/tmp/luma-app-installed-input.json
cmp /tmp/luma-app-input.json /tmp/luma-app-installed-input.json || fail 'the composed application baseline differs'
install -m 0644 "$baseline/roles.json" /usr/share/luma/first-party-app-roles.json
systemctl enable luma-app-baseline.service

# The Luma OS Release public key (the only key the luma remote trusts) and the
# update-graph public key luma-update verifies the graph with.
install -d -m 0755 /etc/pki/ostree /usr/share/luma/update
gpg --batch --quiet --no-options --homedir /tmp --dearmor \
  <"$src/config/os/keys/luma-os-release.asc" >/etc/pki/ostree/luma-release.gpg
chmod 0644 /etc/pki/ostree/luma-release.gpg
install -m 0644 "$src/config/os/keys/luma-update-graph.pub" /usr/share/luma/update/luma-update-graph.pub

expected_fpr=$(sed -n 's/^LUMA_OS_RELEASE_KEY_FINGERPRINT=//p' "$src/config/os/release.env")
actual_fpr=$(gpg --batch --no-options --homedir /tmp --with-colons --show-keys \
  /etc/pki/ostree/luma-release.gpg | awk -F: '$1 == "fpr" { print $10; exit }')
[ "$actual_fpr" = "$expected_fpr" ] ||
  fail "shipped release key $actual_fpr does not match the contract $expected_fpr"
rm -rf /tmp/pubring.* /tmp/trustdb.gpg /tmp/public-keys.d

# The remote's repository URL lives in a root-only mirror list.
chmod 0600 /etc/luma/update-mirrorlist
chown root:root /etc/luma/update-mirrorlist
grep -Fxq 'url=mirrorlist=file:///etc/luma/update-mirrorlist' /etc/ostree/remotes.d/luma.conf ||
  fail 'the luma remote does not read its URL from /etc/luma/update-mirrorlist'
chmod 0644 /etc/ostree/remotes.d/luma.conf

# No Fedora OSTree remotes. The base image ships none; an installed Fedora
# system gets them from the installer, which Luma's installer does not add.
for remote in /etc/ostree/remotes.d/*.conf; do
  [ "$remote" = /etc/ostree/remotes.d/luma.conf ] ||
    fail "unexpected OSTree remote definition in the image: $remote"
done

# Firmware comes from LVFS stable releases only (depot_firmware.py): the stable
# remote on, the testing and embargo remotes off, whatever fwupd ships by default.
for name in lvfs-testing lvfs-embargo; do
  conf=/etc/fwupd/remotes.d/$name.conf
  [ ! -f "$conf" ] || sed -i 's/^Enabled=.*/Enabled=false/' "$conf"
done
[ -f /etc/fwupd/remotes.d/lvfs.conf ] && sed -i 's/^Enabled=.*/Enabled=true/' /etc/fwupd/remotes.d/lvfs.conf ||
  fail 'fwupd has no LVFS stable remote at /etc/fwupd/remotes.d/lvfs.conf'
for conf in /etc/fwupd/remotes.d/*.conf; do
  case "$(basename "$conf")" in lvfs.conf|vendor-directory.conf) continue ;; esac
  ! grep -qx 'Enabled=true' "$conf" || fail "firmware remote $(basename "$conf" .conf) must stay off (LVFS stable only)"
done

# Service policy through presets, applied now so the image's /etc matches.
systemctl preset flatpak-add-fedora-repos.service rpm-ostreed-automatic.timer waydroid-container.service
if systemctl is-enabled --quiet waydroid-container.service; then
  fail 'unused Android support would start at boot'
fi
for unit in greenboot-healthcheck.service greenboot-set-rollback-trigger.service; do
  [ -f "/usr/lib/systemd/system/$unit" ] || fail "greenboot unit is missing: $unit"
done
systemctl preset greenboot-healthcheck.service greenboot-set-rollback-trigger.service
systemctl is-enabled --quiet greenboot-healthcheck.service ||
  fail 'greenboot health check is not enabled'
if systemctl is-enabled --quiet flatpak-add-fedora-repos.service; then
  fail 'the Fedora OCI Flatpak remote would still be added at boot'
fi
# Luma's checks enforce only on trial boots through luma-update's helper, and
# luma-update ships the luma-updated check itself.
[ -r /usr/lib/luma-update/luma-greenboot-common.sh ] ||
  fail 'luma-update trial-boot helper is missing; the image checks would restart healthy machines'
[ -x /usr/lib/greenboot/check/required.d/43-luma-updated.sh ] ||
  fail 'luma-update greenboot check is missing'
if rpm -q greenboot-default-health-checks >/dev/null 2>&1; then
  fail 'greenboot-default-health-checks would roll back machines that boot offline'
fi

# Boot counting. Installs use bootupd's static GRUB configuration, which
# grub2-mkconfig's boot counter never reaches; greenboot ships its own static
# snippet for exactly this. Refuse an image without it: a failed boot would
# never fall back.
grep -qs 'boot_counter' /usr/lib/bootupd/grub2-static/configs.d/08_greenboot.cfg ||
  fail 'greenboot boot counting is missing from the static GRUB configuration'

# Classic snaps (snap install --classic code) need /snap. rpm-ostree refuses a
# top-level path inside a package, but the image may carry one the same way it
# carries /home -> var/home; snapd's own state stays below /var/lib/snapd
# (ADR-017, ADR-038).
if [ -e /snap ] && [ ! -L /snap ]; then
  fail '/snap exists in the base image and is not a link'
fi
ln -sfn var/lib/snapd/snap /snap
[ "$(readlink /snap)" = var/lib/snapd/snap ] || fail '/snap does not lead to /var/lib/snapd/snap'

# luma-install-commands (ADR-038): dnf and yum lead to its front end and
# Flathub is an unfiltered static system remote.
if rpm -q luma-install-commands >/dev/null 2>&1; then
  for command in dnf yum; do
    [ "$(readlink -f /usr/bin/$command)" = /usr/libexec/luma-install-commands/dnf ] ||
      fail "$command does not lead to the Luma front end"
  done
  [ -f /usr/share/flatpak/remotes.d/flathub.flatpakrepo ] || fail 'the Flathub remote definition is missing'
  if grep -q '^Filter=' /usr/share/flatpak/remotes.d/flathub.flatpakrepo; then
    fail 'the Flathub remote is filtered'
  fi
fi

# Depot's Luma app source is a system-wide Flatpak remote out of the box, like
# Flathub: the descriptor served at dl.simplyluma.com/luma.flatpakrepo (Luma
# Depot signing key inside), applied by flatpak as a static remote.
install -D -m 0644 "$src/config/os/flatpak/luma.flatpakrepo" /usr/share/flatpak/remotes.d/luma.flatpakrepo
grep -Fxq 'Url=https://dl.simplyluma.com/repo/' /usr/share/flatpak/remotes.d/luma.flatpakrepo &&
  grep -q '^GPGKey=' /usr/share/flatpak/remotes.d/luma.flatpakrepo ||
  fail 'the Luma Flatpak remote definition is missing its URL or signing key'

# The signed browser owns the canonical launcher/window identity on Luma OS.
# Standalone native deployments retain their existing visible alias. Only
# composed unlocked defaults change; personal favorites are never rewritten.
if rpm -q viola-browser-stable >/dev/null 2>&1; then
  viola_desktops=$(python3 "$src/scripts/os/lib/viola_desktop_policy.py") ||
    fail 'Viola launcher ownership could not be composed'
  while IFS= read -r desktop; do
    desktop-file-validate "$desktop" || fail "the Viola desktop override does not validate: $desktop"
  done <<<"$viola_desktops"
  dconf update
fi

# Fedora never replaces Luma. Every package installed from the Luma pin list
# (config/desktop/packages.txt) is recorded in /usr/share/luma/luma-owned-packages.txt
# and excluded (excludepkgs) from every repository the image defines, so a
# layered install through dnf or rpm-ostree resolves those names only to the
# image's own builds; luma-install-commands refuses to replace them from any
# other source. tests/os/gate/fedora-cannot-replace-luma.sh checks both.
mapfile -t owned < <(
  sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$src/config/desktop/packages.txt" |
    while read -r nevra; do rpm -q --qf '%{NAME}\n' "$nevra" || echo "MISSING:$nevra"; done | sort -u
)
[ "${#owned[@]}" -gt 0 ] || fail 'no Luma-owned packages found'
for name in "${owned[@]}"; do
  case $name in MISSING:*) fail "pinned package is not installed: ${name#MISSING:}" ;; esac
done
install -d -m 0755 /usr/share/luma
printf '%s\n' "${owned[@]}" >/usr/share/luma/luma-owned-packages.txt
chmod 0644 /usr/share/luma/luma-owned-packages.txt
exclude_list=$(IFS=,; printf '%s' "${owned[*]}")
shopt -s nullglob
repo_files=(/etc/yum.repos.d/*.repo)
shopt -u nullglob
[ "${#repo_files[@]}" -gt 0 ] || fail 'the image defines no package repositories'
for repo_file in "${repo_files[@]}"; do
  python3 - "$repo_file" "$exclude_list" <<'PY' || fail "cannot exclude Luma packages from $repo_file"
import re, sys
path, names = sys.argv[1], sys.argv[2]
lines = open(path).read().splitlines()
out, sections = [], 0
for line in lines:
    if re.match(r"\s*(excludepkgs|exclude)\s*=", line):
        sys.exit("%s already sets %s" % (path, line.split("=")[0].strip()))
    out.append(line)
    if re.match(r"\s*\[[^\]]+\]\s*$", line):
        out.append("# Luma: packages the Luma image owns never come from this repository")
        out.append("excludepkgs=" + names)
        sections += 1
if sections == 0:
    sys.exit("%s defines no repository" % path)
open(path, "w").write("\n".join(out) + "\n")
PY
done

printf 'System policy step complete\n'
