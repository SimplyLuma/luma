#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
#
# Release gate checks for ADR-038 (no install hurdles). Run as root inside a
# disposable gate VM installed from the candidate, with network access to
# Fedora's mirrors, Flathub and the Snap Store. Prints one JSON line per check
# ({"check", "result": "pass"|"fail"|"skip", "detail"}) and exits 1 if any
# check failed. Everything it adds is removed again.
#
#   no-install-hurdles.sh [--quick]
#
# --quick skips the large downloads (the Firefox Flatpak and Snap).
set -uo pipefail
quick=0
[ "${1:-}" = --quick ] && quick=1
user=luma-gate-hurdles
failed=0

emit() {
  python3 -c 'import json, sys; print(json.dumps({"check": sys.argv[1], "result": sys.argv[2], "detail": sys.argv[3][-600:]}))' "$1" "$2" "$3"
  [ "$2" = fail ] && failed=1
  return 0
}
run_check() {
  # run_check NAME EXPECT_REGEX COMMAND...
  local name=$1 expect=$2 out code
  shift 2
  out=$("$@" 2>&1); code=$?
  if [ "$code" = 0 ] && grep -Eq "$expect" <<<"$out"; then emit "$name" pass "$(tail -n 3 <<<"$out")"
  else emit "$name" fail "exit $code: $(tail -n 8 <<<"$out")"; fi
}
as_user() { (cd /tmp && runuser -u "$user" -- "$@"); }
# in_session COMMAND...: in the user's own systemd user session (session bus,
# runtime directory), as a person's terminal runs it.
in_session() { systemd-run --user -M "$user@" --wait --pipe --quiet --collect -p WorkingDirectory=/tmp "$@"; }

cleanup() {
  rm -f /etc/sudoers.d/90-luma-gate-hurdles
  loginctl disable-linger "$user" >/dev/null 2>&1 || true
  loginctl terminate-user "$user" >/dev/null 2>&1 || true
  sleep 2
  userdel -r "$user" >/dev/null 2>&1 || true
}
trap cleanup EXIT
# A run that was interrupted may have left the account behind: start clean.
loginctl terminate-user "$user" >/dev/null 2>&1 || true
userdel -r "$user" >/dev/null 2>&1 || true
rm -rf "/var/home/$user"
useradd -m -G wheel "$user" || { emit gate-account fail "useradd $user failed"; exit 1; }
loginctl enable-linger "$user"
for _ in $(seq 60); do [ -S "/run/user/$(id -u "$user")/bus" ] && break; sleep 1; done
printf '%s ALL=(ALL) NOPASSWD: ALL\n' "$user" >/etc/sudoers.d/90-luma-gate-hurdles
chmod 0440 /etc/sudoers.d/90-luma-gate-hurdles

# Image contract.
run_check dnf-is-front-end 'luma-install-commands' readlink -f /usr/bin/dnf
run_check yum-is-front-end 'luma-install-commands' readlink -f /usr/bin/yum
run_check dnf5-reachable '^dnf5 version' dnf5 --version
run_check snap-link '^var/lib/snapd/snap$' readlink /snap
run_check selinux-enforcing '^Enforcing$' getenforce

# dnf and apt, live.
# Start from a computer without htop, whatever an earlier run left.
if rpm -q htop >/dev/null 2>&1; then as_user sudo dnf remove -y htop >/dev/null 2>&1; fi
run_check sudo-dnf-install-htop 'Complete! htop is ready to use' as_user sudo dnf install -y htop
run_check htop-runs-now '^htop ' as_user htop --version
run_check sudo-dnf-install-luma-terminal-installed 'is already installed' as_user sudo dnf install -y luma-terminal
run_check sudo-dnf-remove-htop 'Complete! htop removed' as_user sudo dnf remove -y htop
if command -v htop >/dev/null 2>&1; then emit htop-gone-now fail "$(command -v htop)"; else emit htop-gone-now pass ""; fi
run_check sudo-apt-install-htop 'Luma uses dnf; running: sudo dnf install -y htop' as_user sudo apt install -y htop
run_check htop-runs-after-apt '^htop ' as_user htop --version
run_check sudo-apt-get-install-htop 'Luma uses dnf; running: sudo dnf install -y htop' as_user sudo apt-get install -y htop
run_check sudo-dnf-upgrade-asks-never-restarts 'Luma|Nothing to do|update' sh -c "cd /tmp && runuser -u $user -- sudo dnf upgrade --assumeno; true"
as_user sudo dnf remove -y htop >/dev/null 2>&1
run_check vitals-journal 'install for htop' journalctl -b MESSAGE_ID=a08437fa35fc4db9a55f26f08c3e9847 -o cat

# rpm -i of a package file goes into the system image, live; queries stay rpm.
rpm_dir=$(mktemp -d /var/tmp/luma-gate-rpm.XXXXXX)
if (cd /tmp && dnf5 download -q --destdir "$rpm_dir" htop >/dev/null 2>&1); then
  run_check sudo-rpm-ivh-file 'Luma installs package files' as_user sudo rpm -ivh "$(ls "$rpm_dir"/htop-*.rpm | head -n 1)"
  run_check htop-runs-after-rpm '^htop ' as_user htop --version
  as_user sudo dnf remove -y htop >/dev/null 2>&1
else
  emit sudo-rpm-ivh-file fail "dnf5 download htop failed"
fi
rm -rf "$rpm_dir"
run_check rpm-query-unchanged '^rpm-' as_user rpm -q rpm
run_check opt-writable 'ok' sh -c 'touch /opt/.luma-gate && rm /opt/.luma-gate && echo ok'
run_check usr-local-writable 'ok' sh -c 'touch /usr/local/bin/.luma-gate && rm /usr/local/bin/.luma-gate && echo ok'

# Fedora never replaces a package Luma builds (luma-owned-packages.txt).
owned_list=/usr/share/luma/luma-owned-packages.txt
if [ -s "$owned_list" ]; then
  for pkg in gtk4 gnome-shell; do
    grep -qx "$pkg" "$owned_list" || { emit "owned-$pkg-listed" fail "$pkg missing from $owned_list"; continue; }
    before=$(rpm -q "$pkg")
    out=$(cd /tmp && runuser -u "$user" -- sudo dnf upgrade -y "$pkg" 2>&1); code=$?
    if [ "$code" != 0 ] && grep -q "Luma's own build" <<<"$out" && [ "$(rpm -q "$pkg")" = "$before" ]; then
      emit "sudo-dnf-upgrade-$pkg-refused" pass "$(tail -n 1 <<<"$out")"
    else
      emit "sudo-dnf-upgrade-$pkg-refused" fail "exit $code: $(tail -n 4 <<<"$out")"
    fi
    out=$(cd /tmp && runuser -u "$user" -- sudo dnf install -y "$pkg" 2>&1); code=$?
    if grep -q "Luma's own build" <<<"$out" && [ "$(rpm -q "$pkg")" = "$before" ]; then
      emit "sudo-dnf-install-$pkg-unchanged" pass "$(tail -n 1 <<<"$out")"
    else
      emit "sudo-dnf-install-$pkg-unchanged" fail "exit $code: $(tail -n 4 <<<"$out")"
    fi
  done
  found=$(cd /tmp && dnf5 repoquery -q --repo=fedora --repo=updates gnome-shell 2>/dev/null)
  if [ -z "$found" ]; then emit fedora-gnome-shell-excluded pass ""; else emit fedora-gnome-shell-excluded fail "$found"; fi
else
  emit luma-owned-packages-list fail "$owned_list is missing"
fi

# Flathub.
run_check flathub-remote '^flathub' flatpak remotes --system --columns=name
if flatpak remotes --system --columns=name,filter | awk '$1 == "flathub" && $2 != "-"' | grep -q .; then
  emit flathub-unfiltered fail "$(flatpak remotes --system --columns=name,filter)"
else
  emit flathub-unfiltered pass ""
fi
run_check flathub-remote-ls 'org\.mozilla\.firefox' as_user flatpak remote-ls --app flathub
# In a terminal, flatpak offers matches for a short name (it matches only
# when stdin and stdout are terminals, on every distribution).
out=$(cd /tmp && { sleep 60; printf '0\n'; } | python3 -c 'import pty, sys; pty.spawn(["flatpak", "install", "--system", "--no-deploy", "firefox"])' 2>&1)
if grep -q 'app/org.mozilla.firefox/' <<<"$out"; then emit flatpak-install-short-name-offers-firefox pass ""
else emit flatpak-install-short-name-offers-firefox fail "$(tail -n 6 <<<"$out")"; fi
if [ "$quick" = 1 ]; then
  emit flatpak-install-firefox skip "--quick"
else
  # Flathub's own setup for a per-user install adds the remote for the user first.
  run_check flatpak-install-firefox-user 'org\.mozilla\.firefox' in_session sh -c "flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo && flatpak install --user -y --noninteractive flathub org.mozilla.firefox && flatpak list --user --app --columns=application"
  as_user flatpak uninstall --user -y --noninteractive --delete-data org.mozilla.firefox >/dev/null 2>&1
fi

# Snap.
run_check snap-seeded 'seed' sh -c 'timeout 300 snap wait system seed.loaded && echo seed.loaded'
if [ "$quick" = 1 ]; then
  run_check snap-install-hello-world 'installed' timeout 600 snap install hello-world
  snap remove --purge hello-world >/dev/null 2>&1
else
  run_check snap-install-firefox 'installed' timeout 1800 snap install firefox
  run_check snap-firefox-runs 'Mozilla Firefox' in_session snap run firefox --version
  snap remove --purge firefox >/dev/null 2>&1
fi

exit "$failed"
