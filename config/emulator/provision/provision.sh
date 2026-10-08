#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Turn a verified Fedora 44 AArch64 cloud image into the Luma Emulator factory
# image.
#
# This script is the factory image's recipe. It is version controlled, it runs
# once during `luma-emulator provision`, and its package list is recorded in the
# image manifest — so what is in a guest can be checked rather than believed.
# A running VM is never the source of truth.

set -euo pipefail

log() { printf '\n=== %s\n' "$1"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "provision.sh must run as root inside the guest" >&2
  exit 1
fi

log "Holding the graphical session until the recipe has finished"
# This must happen before anything else. If gdm reaches a graphical login while
# provisioning is still running, the emulator user gets a dconf profile built
# from Fedora's defaults, and Luma's defaults — installed later in this recipe —
# are permanently shadowed by those per-user values.
systemctl set-default multi-user.target >/dev/null 2>&1 || true
systemctl mask gdm.service >/dev/null 2>&1 || true

log "Guest identity"
uname -m
cat /etc/fedora-release

log "Installing the pinned emulator kernel"
# Factory composition owns this dependency; it is never replayed at login.
# Keep the previous kernel installed for rollback. DNF verifies Fedora's RPMs.
# shellcheck source=config/emulator/provision/kernel.env
. "$(dirname -- "${BASH_SOURCE[0]}")/kernel.env"
dnf5 -y install \
  "kernel-core-${LUMA_EMULATOR_KERNEL_VERSION}.$(uname -m)" \
  "kernel-modules-core-${LUMA_EMULATOR_KERNEL_VERSION}.$(uname -m)" \
  "kernel-modules-${LUMA_EMULATOR_KERNEL_VERSION}.$(uname -m)"

# The kernel talks to the host over the virtio console. Recording it on every
# future boot is what makes `luma-emulator logs --component boot` able to
# explain a failure that happens long before SSH exists.
log "Routing the kernel console to the virtio console"
grubby --update-kernel=ALL --args="console=hvc0" || true

log "Installing the Luma package set, if a bundle was supplied"
# Luma's packages go in BEFORE Fedora's desktop set, not after.
#
# Installed first, they are a fresh install and dnf resolves their dependencies
# from Fedora normally. Installed afterwards they would be a downgrade against
# whatever Fedora's updates repository happens to carry that week, and the
# transaction fails. The Fedora step below then excludes every name the bundle
# owns so nothing upgrades over it.
luma_exclude=""
if [ -d /var/tmp/luma-emulator-bundle ] && \
   find /var/tmp/luma-emulator-bundle -maxdepth 1 \( -name '*.tar.gz' -o -name '*.tar.zst' \) | grep -q .; then
  bash /tmp/luma-emulator-provision/install-luma-bundle.sh /var/tmp/luma-emulator-bundle
  luma_exclude=$(tr '\n' ' ' < /etc/luma-emulator/luma-package-names.txt | tr -s ' ' | sed 's/ $//;s/ /,/g')
  echo "excluding from the Fedora step: $luma_exclude"
else
  echo "NO LUMA BUNDLE SUPPLIED — this image is a Fedora substrate, not Luma."
  install -d -m 0755 /etc/luma-emulator
  echo "fedora-fallback" > /etc/luma-emulator/luma-completeness.txt
  : > /etc/luma-emulator/luma-packages.txt
fi

log "Installing the shells, toolkit and preview tooling"
# Grouped by why they are here, so a later reviewer can tell what may be
# dropped and what may not. Anything in `required` failing is a failed image.
required=(
  # Desktop presentation shell.
  gnome-shell gnome-session gnome-settings-daemon mutter gdm
  # Handheld presentation shell.
  phosh phoc
  # The toolkit Luma applications and the Luma Developer Platform are built on.
  gtk4 libadwaita python3-gobject python3-cairo gsettings-desktop-schemas
  # Application service boundaries the core apps genuinely use.
  evolution-data-server gstreamer1 gstreamer1-plugins-good
  # Preview and capture lane: a nested compositor and a framebuffer grabber.
  sway grim wlr-randr wtype
  # dbus-run-session, needed to start a nested GNOME Shell on its own bus.
  dbus-daemon
  # Clipboard between the Mac and the guest.
  spice-vdagent
  # Accessibility inspection.
  at-spi2-core at-spi2-atk python3-pyatspi
  # Deterministic scenarios.
  iproute-tc
  # Source synchronization and general control.
  git-core
)

exclude_flag=""
if [ -n "${luma_exclude:-}" ]; then
  exclude_flag="--exclude=$luma_exclude"
fi

# Drop anything the Luma bundle already owns from the Fedora request list.
# dnf treats "you asked for a package you also excluded" as an error, and the
# bundle's build is the one this image is supposed to have anyway. The exclude
# stays on so no Fedora *dependency* upgrades over a Luma package.
filter_luma_owned() {
  local name
  for name in "$@"; do
    if [ -s /etc/luma-emulator/luma-package-names.txt ] &&
       grep -qxF "$name" /etc/luma-emulator/luma-package-names.txt; then
      echo "skipping $name: the Luma bundle owns it" >&2
      continue
    fi
    printf '%s\n' "$name"
  done
}

mapfile -t fedora_required < <(filter_luma_owned "${required[@]}")
dnf5 -y install $exclude_flag "${fedora_required[@]}"

log "Installing optional packages, reporting anything Fedora does not carry"
# These are wanted but not fatal. An honest report beats a silent substitution:
# Luma's own font requirements in particular must not be quietly replaced by
# whatever fontconfig picks instead.
# Fedora 44 ships the handheld on-screen keyboard as phosh-osk-stevia;
# squeekboard and phosh-osk-stub are gone. Figtree is a Luma-built package and
# is not expected here, so its absence is reported rather than silently
# substituted by whatever fontconfig picks instead.
# Figtree is deliberately NOT here. It is a required Luma asset and arrives
# through the verified Luma package bundle; treating it as an optional Fedora
# package is how an image ends up rendering in a substitute face while still
# calling itself Luma.
optional=(
  phosh-osk-stevia squeekboard phosh-osk-stub
  ibm-plex-mono-fonts
)
missing=()
mapfile -t fedora_optional < <(filter_luma_owned "${optional[@]}")
for package in "${fedora_optional[@]}"; do
  if dnf5 -y install $exclude_flag "$package" >/dev/null 2>&1; then
    echo "optional installed: $package"
  else
    echo "OPTIONAL MISSING: $package is not in the configured repositories"
    missing+=("$package")
  fi
done
mkdir -p /etc/luma-emulator
printf '%s\n' "${missing[@]}" > /etc/luma-emulator/missing-packages.txt

# Apple's host audio queue can take over two seconds on first speaker startup.
# Keep the driver's supported control-message deadline bounded, but long enough
# for that cold start. This is an emulator device default, not a login replay.
install -d -m 0755 /etc/modprobe.d
cat > /etc/modprobe.d/luma-emulator-audio.conf <<'AUDIO'
options virtio_snd msg_timeout_ms=5000
AUDIO

# Emulator-only virtual audio defaults, installed once during composition.
# Physical desktop and phone audio policies do not consume this profile.
install -D -m 0644 "${BASH_SOURCE[0]%/*}/51-luma-emulator-audio.conf" \
  /etc/wireplumber/wireplumber.conf.d/51-luma-emulator-audio.conf
install -D -m 0644 "${BASH_SOURCE[0]%/*}/51-luma-emulator-android-audio.conf" \
  /etc/pipewire/pipewire-pulse.conf.d/51-luma-emulator-android-audio.conf

log "Setting a development login password"
# The cloud-init account is key-only, which means the handheld lock screen can
# never be unlocked: there is no password to type. This is a disposable guest on
# a host-only network reachable from this Mac alone, so a known development
# password is the right trade. It is documented, and it is not a Luma default.
echo "luma:luma" | chpasswd

log "Enabling a graphical session with automatic login"
# The emulator's guest has exactly one interactive user and no password
# authentication; automatic login is what makes a cold start land on the Luma
# session rather than a login prompt nobody can type into headlessly.
mkdir -p /etc/gdm
cat > /etc/gdm/custom.conf <<'GDM'
[daemon]
AutomaticLoginEnable=True
AutomaticLogin=luma
WaylandEnable=true

[security]

[xdmcp]

[chooser]

[debug]
GDM
systemctl set-default graphical.target

log "Recording the emulator's session selection contract"
mkdir -p /etc/luma-emulator
if [ ! -f /etc/luma-emulator/session ]; then
  echo gnome > /etc/luma-emulator/session
fi
install -D -m 0755 /tmp/luma-emulator-provision/luma-emulator-session \
  /usr/local/sbin/luma-emulator-session
cat > /etc/systemd/system/luma-emulator-session.service <<'UNIT'
[Unit]
Description=Apply the Luma Emulator presentation mode before the graphical session
Before=gdm.service
After=local-fs.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/luma-emulator-session

[Install]
WantedBy=graphical.target
UNIT
systemctl enable luma-emulator-session.service

log "Installing the guest-side emulator helpers"
install -D -m 0755 /tmp/luma-emulator-provision/luma-emulator-capture \
  /usr/bin/luma-emulator-capture
install -D -m 0755 /tmp/luma-emulator-provision/luma-emulator-a11y.py \
  /usr/libexec/luma-emulator-a11y.py
# The portal capture helper belongs in the image, not in the source-sync path.
# Screenshotting a guest must not depend on having connected a project first.
install -D -m 0755 /tmp/luma-emulator-provision/luma-emulator-portal-shot.py \
  /usr/libexec/luma-emulator-portal-shot.py

log "Enabling the clipboard agent"
systemctl enable spice-vdagentd.service || true

log "Preparing the developer's preview prefix"
# `install -d` applies ownership only to the final component, so creating
# ~/.local/share/luma-emulator in one call leaves ~/.local and ~/.local/share
# owned by root. The user then cannot create its own application data
# directories, and every Luma app that stores anything dies the instant it is
# launched — which looks exactly like a click that did nothing.
install -d -o luma -g luma -m 0755 /home/luma/.local
install -d -o luma -g luma -m 0755 /home/luma/.local/share
install -d -o luma -g luma -m 0755 /home/luma/.config
install -d -o luma -g luma -m 0755 /home/luma/.cache
install -d -o luma -g luma -m 0700 /home/luma/.local/share/prairie
install -d -o luma -g luma -m 0755 \
  /home/luma/.local/share/luma-emulator \
  /home/luma/.local/share/luma-emulator/source \
  /home/luma/.local/share/luma-emulator/screenshots
# Belt and braces: anything created above by an earlier step still belongs to
# the user who will actually run the session.
chown -R luma:luma /home/luma/.local /home/luma/.config /home/luma/.cache
loginctl enable-linger luma || true

log "Suppressing Fedora's first-run tour"
# gnome-tour greets the first login with "Welcome to Fedora Linux". This is a
# Luma image; Fedora's onboarding is not part of it.
if rpm -q gnome-tour >/dev/null 2>&1; then
  dnf5 -y remove gnome-tour >/dev/null 2>&1 || \
    install -D -m 0644 /dev/null /etc/xdg/autostart/org.gnome.Tour.desktop
  echo "gnome-tour suppressed"
fi

# Notes/Calendar/Terminal own these application roles. Preserve dependencies;
# remove only obsolete application packages from reused emulator baselines.
retired_apps=()
for package in foot gnome-calendar gnome-text-editor; do
  if rpm -q "$package" >/dev/null 2>&1; then retired_apps+=("$package"); fi
done
if [ "${#retired_apps[@]}" -gt 0 ]; then
  dnf5 -y remove --no-autoremove "${retired_apps[@]}"
fi

log "Releasing the graphical session now that defaults are in place"
# Unmask only now: every Luma default — dconf databases, session selection,
# wallpaper, device class — is compiled and on disk, so the first graphical
# login reads them rather than Fedora's.
systemctl unmask gdm.service >/dev/null 2>&1 || true
systemctl set-default graphical.target

log "Recording what was installed"
rpm -qa --qf '%{NAME}-%{EVR}.%{ARCH}\n' | sort > /etc/luma-emulator/packages.txt
wc -l < /etc/luma-emulator/packages.txt

log "Trimming the package cache so the factory image stays small"
dnf5 clean all || dnf clean all || true
rm -rf /var/cache/dnf /var/cache/libdnf5 || true

log "Provisioning complete"
