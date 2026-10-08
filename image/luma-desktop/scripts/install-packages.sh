#!/usr/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Image build step 1: packages. Runs inside `podman build` with the source
# checkout at /run/luma/source and the verified Luma rpm-md repository at
# /run/luma/packages. This is the container form of what
# scripts/vm/provision-desktop-image.sh does with rpm-ostree on a live guest:
#
#   * every pinned Luma NEVRA (config/desktop/packages.txt) is installed in one
#     dependency-resolved transaction, replacing the Fedora base packages it
#     supersedes (Shell, Mutter, Control Center, GTK 3/4, libadwaita,
#     libhandy, Files, Calculator, Terminal, the Plymouth family);
#   * Fedora additions and the shared/desktop application roles are resolved
#     in the same transaction;
#   * packages Luma does not ship are removed with rpm's dependency check;
#   * afterwards every pin must be installed byte-for-byte.

set -euo pipefail

src=/run/luma/source
packages=/run/luma/packages

strip_list() {
  sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$1"
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ -f "$packages/repodata/repomd.xml" ] || fail 'the Luma package repository has no metadata'

mapfile -t pins < <(strip_list "$src/config/desktop/packages.txt")
[ "${#pins[@]}" -gt 0 ] || fail 'the Luma pin list is empty'
for nevra in "${pins[@]}"; do
  [ -f "$packages/Packages/$nevra.rpm" ] || fail "pinned package is not in the build repository: $nevra"
done
mapfile -t pin_names < <(
  for nevra in "${pins[@]}"; do
    rpm -qp --nosignature --qf '%{NAME}\n' "$packages/Packages/$nevra.rpm"
  done | sort -u
)

mapfile -t removed < <(strip_list "$src/config/os/removed-packages.txt")
mapfile -t -O "${#removed[@]}" removed < <(strip_list "$src/config/shared/excluded-background-packages.txt")

# The build-only repository. It is never shipped: the image carries no
# file:// repository that could break dnf or rpm-ostree later. Luma packages
# are not RPM-signed; their integrity is the header digest check at the end of
# this script against the verified pool manifest, and then the GPG-signed
# OSTree commit that carries them.
cat >/etc/yum.repos.d/luma-image-build.repo <<'EOF'
[luma-image-build]
name=Luma packages for this image build
baseurl=file:///run/luma/packages
enabled=1
gpgcheck=0
repo_gpgcheck=0
priority=1
skip_if_unavailable=False
EOF

contains() {
  local needle=$1 item
  shift
  for item in "$@"; do
    [ "$item" = "$needle" ] && return 0
  done
  return 1
}

# Fedora additions and application roles. A role already supplied by a pinned
# Luma package, or one Luma removes, is not requested. Roles already present
# in the base stay as they are.
requests=()
while IFS= read -r name; do
  if contains "$name" "${pin_names[@]}"; then
    continue
  fi
  if contains "$name" "${removed[@]}"; then
    printf 'note: role %s is removed from the Luma image (config/os/removed-packages.txt)\n' "$name"
    continue
  fi
  if rpm -q "$name" >/dev/null 2>&1; then
    continue
  fi
  contains "$name" "${requests[@]}" || requests+=("$name")
done < <(
  strip_list "$src/config/os/fedora-packages.txt"
  strip_list "$src/config/shared/application-packages.txt"
  strip_list "$src/config/desktop/application-packages.txt"
)

# /opt is a symlink to /var/opt in Fedora's bootable images, and nothing a
# package unpacks below /var is part of the delivered tree. Let packages unpack
# there, then relocate each /opt entry into /usr/lib/opt with a tmpfiles link,
# exactly as rpm-ostree does for layered packages (rpm-ostree-autovar.conf).
if [ -L /opt ]; then
  install -d -m 0755 /var/opt
fi

printf 'Installing %d pinned Luma packages and %d Fedora packages\n' \
  "${#pins[@]}" "${#requests[@]}"
dnf5 -y --setopt=keepcache=False install "${pins[@]}" "${requests[@]}"

if [ -L /opt ] && [ -d /var/opt ]; then
  : >/usr/lib/tmpfiles.d/luma-opt.conf.new
  for entry in /var/opt/* /var/opt/.[!.]*; do
    [ -e "$entry" ] || [ -L "$entry" ] || continue
    name=$(basename "$entry")
    [ ! -e "/usr/lib/opt/$name" ] || fail "/usr/lib/opt/$name already exists"
    install -d -m 0755 /usr/lib/opt
    mv "$entry" "/usr/lib/opt/$name"
    printf 'L /opt/%s - - - - ../../usr/lib/opt/%s\n' "$name" "$name" >>/usr/lib/tmpfiles.d/luma-opt.conf.new
    printf 'Relocated /opt/%s to /usr/lib/opt/%s\n' "$name" "$name"
  done
  if [ -s /usr/lib/tmpfiles.d/luma-opt.conf.new ]; then
    mv /usr/lib/tmpfiles.d/luma-opt.conf.new /usr/lib/tmpfiles.d/luma-opt.conf
  else
    rm -f /usr/lib/tmpfiles.d/luma-opt.conf.new
  fi
  rmdir /var/opt
fi

# Removals: rpm -e refuses when anything still requires the package, which is
# the rpm-ostree `override remove` contract the VM composition relied on.
installed_removals=()
for name in "${removed[@]}"; do
  if rpm -q "$name" >/dev/null 2>&1; then
    installed_removals+=("$name")
  fi
done
if [ "${#installed_removals[@]}" -gt 0 ]; then
  printf 'Removing: %s\n' "${installed_removals[*]}"
  rpm -e "${installed_removals[@]}"
fi

# Verification: each pin is installed exactly, from the verified file.
status=0
for nevra in "${pins[@]}"; do
  expected=$(rpm -qp --nosignature --qf '%{SHA256HEADER}' "$packages/Packages/$nevra.rpm")
  name=$(rpm -qp --nosignature --qf '%{NAME}' "$packages/Packages/$nevra.rpm")
  actual=$(rpm -q --qf '%{NEVRA} %{SHA256HEADER}\n' "$name" 2>/dev/null || true)
  if [ "$actual" != "$nevra $expected" ]; then
    printf 'error: %s is not installed exactly (found: %s)\n' "$nevra" "${actual:-nothing}" >&2
    status=1
  fi
done
for name in "${removed[@]}"; do
  if rpm -q "$name" >/dev/null 2>&1; then
    printf 'error: %s must not be installed\n' "$name" >&2
    status=1
  fi
done
for name in "${requests[@]}"; do
  rpm -q "$name" >/dev/null 2>&1 || {
    printf 'error: requested Fedora package %s is not installed\n' "$name" >&2
    status=1
  }
done
[ "$status" -eq 0 ] || fail 'package verification failed'

rm -f /etc/yum.repos.d/luma-image-build.repo
dnf5 clean all >/dev/null
rm -rf /var/cache/libdnf5 /var/lib/dnf /var/log/dnf5.log* /var/log/hawkey.log
printf 'Package step complete: %d pins verified\n' "${#pins[@]}"
