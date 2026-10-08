#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
# ADR-038 checks, run as root inside a disposable Luma VM that has
# luma-install-commands and a wheel account "tester" (sudo without a password,
# test VM only). Never run on a real computer: it installs, removes and
# overrides packages.
#   matrix.sh STEP     one step; prints PASS/FAIL lines, exits non-zero on FAIL
set -uo pipefail
as_tester() { runuser -u tester -- "$@"; }
pass() { printf 'PASS %s\n' "$*"; }
fail() { printf 'FAIL %s\n' "$*"; status=1; }
check() { local what=$1; shift; if "$@"; then pass "$what"; else fail "$what"; fi; }
status=0
log=/var/tmp/nohurdles-$1.log
exec > >(tee "$log") 2>&1

case $1 in
  links)
    check "dnf leads to the Luma front end" test "$(readlink -f /usr/bin/dnf)" = /usr/libexec/luma-install-commands/dnf
    check "yum leads to the Luma front end" test "$(readlink -f /usr/bin/yum)" = /usr/libexec/luma-install-commands/dnf
    check "dnf5 is still dnf5" test "$(readlink -f /usr/bin/dnf5)" = /usr/bin/dnf5
    check "SELinux enforcing" test "$(getenforce)" = Enforcing
    check "gpgcheck on for fedora" grep -qx 'gpgcheck=1' /etc/yum.repos.d/fedora.repo
    ;;
  install-htop)
    out=$(cd /tmp && as_tester sudo dnf install -y htop 2>&1); echo "$out"
    check "dnf install htop exits 0" grep -q 'Complete! htop is ready to use.' <<<"$out"
    check "htop runs immediately" as_tester htop --version
    check "htop recorded as added" sh -c 'rpm-ostree status --json | grep -q "\"htop\""'
    check "journal entry for Vitals" sh -c 'journalctl -b MESSAGE_ID=a08437fa35fc4db9a55f26f08c3e9847 -o cat | grep -q "install for htop: live"'
    ;;
  no-restart-nag)
    check "luma-update does not ask for a restart after a live install" \
      sh -c '[ "$(luma-update status --json | python3 -c "import json,sys; print(json.load(sys.stdin)[\"state\"])")" != restart-required ]'
    ;;
  htop-present)
    check "htop present" as_tester htop --version
    ;;
  already-installed)
    out=$(cd /tmp && as_tester sudo dnf install -y firefox 2>&1); code=$?; echo "$out"
    check "dnf install firefox says already installed" grep -q 'is already installed' <<<"$out"
    check "and nothing to do, exit 0" test "$code" = 0
    ;;
  no-sudo)
    out=$(cd /tmp && as_tester dnf install -y htop 2>&1); code=$?; echo "$out"
    check "without sudo dnf asks for superuser like dnf" grep -q 'superuser privileges' <<<"$out"
    check "without sudo exit 1" test "$code" = 1
    ;;
  read-only)
    check "dnf search passes through" sh -c 'cd /tmp && runuser -u tester -- dnf search htop 2>&1 | grep -q "^ htop"'
    check "dnf list --installed passes through" sh -c 'cd /tmp && runuser -u tester -- dnf list --installed htop 2>&1 | grep -q htop'
    check "dnf info passes through" sh -c 'cd /tmp && runuser -u tester -- dnf info firefox 2>&1 | grep -q "^Name *: firefox"'
    ;;
  upgrade-prompt)
    out=$(cd /tmp && { sleep 20; printf 'n\n'; } | python3 -c 'import pty; pty.spawn(["runuser", "-u", "tester", "--", "sudo", "dnf", "upgrade"])' 2>&1); echo "$out"
    check "dnf upgrade shows the available Luma version" grep -q 'is available' <<<"$out"
    check "dnf upgrade asks" grep -q 'Download and prepare it now' <<<"$out"
    check "no answer yes: nothing staged" sh -c '[ -z "$(luma-update status --json | python3 -c "import json,sys; print(json.load(sys.stdin)[\"staged_version\"])")" ]'
    ;;
  upgrade-yes)
    out=$(cd /tmp && as_tester sudo dnf upgrade -y 2>&1); echo "$out"
    check "dnf upgrade -y prepares the update" grep -q 'installed when you restart' <<<"$out"
    check "update staged, not applied" sh -c '[ "$(luma-update status --json | python3 -c "import json,sys; print(json.load(sys.stdin)[\"state\"])")" = staged ]'
    check "the computer did not restart" test "$(cat /proc/sys/kernel/random/boot_id)" = "$(cat /var/tmp/nohurdles-boot-id)"
    ;;
  apt)
    out=$(cd /tmp && as_tester sudo apt install -y htop 2>&1); echo "$out"
    check "apt install htop announces dnf" grep -q 'Luma uses dnf; running: sudo dnf install -y htop' <<<"$out"
    out=$(cd /tmp && as_tester sudo apt-get install -y firefox 2>&1); code=$?; echo "$out"
    check "apt-get install firefox announces dnf" grep -q 'Luma uses dnf; running: sudo dnf install -y firefox' <<<"$out"
    check "apt-get install firefox exit 0" test "$code" = 0
    out=$(cd /tmp && as_tester sudo apt install -y libssl-dev 2>&1); echo "$out" | tail -3
    check "apt maps libssl-dev" grep -q 'libssl-dev is called openssl-devel on Fedora' <<<"$out"
    out=$(cd /tmp && as_tester apt install nosuchthing-dev 2>&1); code=$?; echo "$out"
    check "apt unknown name exit 100" test "$code" = 100
    ;;
  remove-htop)
    out=$(cd /tmp && as_tester sudo dnf remove -y htop 2>&1); echo "$out"
    check "dnf remove htop completes" grep -q 'Complete! htop removed.' <<<"$out"
    check "htop gone immediately" sh -c '! command -v htop >/dev/null'
    ;;
  base-override)
    out=$(cd /tmp && as_tester sudo dnf swap -y wget2-wget wget1-wget 2>&1); echo "$out"
    check "swap of an image package completes" grep -q 'Complete!' <<<"$out"
    check "swap says restart" grep -q 'after you restart\|ready to use' <<<"$out"
    ;;
  copr)
    out=$(cd /tmp && as_tester sudo dnf copr enable -y "$COPR" 2>&1); echo "$out" | tail -3
    out=$(cd /tmp && as_tester sudo dnf install -y "$COPR_PACKAGE" 2>&1); echo "$out"
    check "COPR package installs" grep -q 'Complete!' <<<"$out"
    ;;
  firefox-remove)
    out=$(cd /tmp && as_tester sudo dnf remove -y firefox 2>&1); echo "$out"
    check "dnf remove firefox explains" grep -q "part of Luma's system image" <<<"$out"
    check "dnf remove firefox completes" grep -q 'Complete!' <<<"$out"
    ;;
  firefox-install)
    out=$(cd /tmp && as_tester sudo dnf install -y firefox 2>&1); echo "$out"
    check "dnf install firefox restores it" grep -q 'Complete! firefox' <<<"$out"
    check "firefox runs" sh -c 'runuser -u tester -- firefox --version | grep -q Firefox'
    ;;
  flatpak)
    check "flathub system remote" sh -c 'flatpak remotes --system --columns=name,filter | grep -q "^flathub"'
    check "flathub unfiltered" sh -c '! flatpak remotes --system --columns=name,filter | grep "^flathub" | grep -q "/"'
    out=$(cd /tmp && as_tester flatpak install --user -y --noninteractive flathub org.gnome.Mahjongg 2>&1); echo "$out" | tail -2
    check "flatpak install as a person" as_tester flatpak info --user org.gnome.Mahjongg
    out=$(cd /tmp && as_tester sudo flatpak install -y --noninteractive firefox 2>&1); echo "$out" | tail -2
    check "sudo flatpak install firefox by name" flatpak info --system org.mozilla.firefox
    as_tester flatpak uninstall --user -y --noninteractive org.gnome.Mahjongg >/dev/null 2>&1
    flatpak uninstall --system -y --noninteractive org.mozilla.firefox >/dev/null 2>&1
    check "flatpak removal" sh -c '! flatpak info --system org.mozilla.firefox >/dev/null 2>&1'
    ;;
  *) echo "unknown step $1"; exit 2 ;;
esac
exit $status
