#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Release gate checks: no Fedora (or other configured) repository can replace
# a package the Luma image owns. Run as root inside a disposable VM installed
# from the candidate image or a nightly medium, with network access to the
# configured repositories. Prints one JSON line per check ({"check", "result":
# "pass"|"fail"|"skip", "detail"}) and exits 1 if any check failed.
#
#   fedora-cannot-replace-luma.sh
#
# image/luma-desktop/scripts/configure-system.sh writes
# /usr/share/luma/luma-owned-packages.txt (every package installed from the
# Luma pin list) and adds excludepkgs for those names to every repository in
# /etc/yum.repos.d. The dnf front end's refusal is checked by
# no-install-hurdles.sh, which owns luma-install-commands.
set -uo pipefail
list=/usr/share/luma/luma-owned-packages.txt
failed=0
emit() {
  python3 -c 'import json, sys; print(json.dumps({"check": sys.argv[1], "result": sys.argv[2], "detail": sys.argv[3][-600:]}))' "$1" "$2" "$3"
  [ "$2" = fail ] && failed=1
  return 0
}

if [ ! -s "$list" ]; then
  emit owned-list fail "$list is missing or empty"
  exit 1
fi
mapfile -t owned <"$list"
missing=
for name in gnome-shell mutter gtk3 gtk4 libadwaita gnome-control-center nautilus; do
  grep -qx "$name" "$list" || missing="$missing $name"
done
if [ -z "$missing" ]; then emit owned-list pass "${#owned[@]} Luma-owned packages, including the forked desktop core"
else emit owned-list fail "the forked desktop core is not listed:$missing"; fi

not_installed=
for name in "${owned[@]}"; do rpm -q "$name" >/dev/null 2>&1 || not_installed="$not_installed $name"; done
if [ -z "$not_installed" ]; then emit owned-installed pass "every listed package is installed"
else emit owned-installed fail "listed but not installed:$not_installed"; fi

# Every repository section in /etc/yum.repos.d excludes every owned name.
out=$(python3 - "$list" <<'CHECK'
import configparser, glob, sys
owned = set(open(sys.argv[1]).read().split())
problems, sections = [], 0
for path in sorted(glob.glob("/etc/yum.repos.d/*.repo")):
    parser = configparser.RawConfigParser(strict=False)
    parser.read(path)
    for section in parser.sections():
        sections += 1
        excluded = set()
        for key in ("excludepkgs", "exclude"):
            if parser.has_option(section, key):
                excluded |= set(parser.get(section, key).replace(",", " ").split())
        lacking = owned - excluded
        if lacking:
            problems.append("%s [%s] lacks %d, e.g. %s" % (path, section, len(lacking), ", ".join(sorted(lacking)[:3])))
if sections == 0:
    problems.append("no repository sections in /etc/yum.repos.d")
print("; ".join(problems) if problems else "%d repository sections exclude every Luma-owned package" % sections)
sys.exit(1 if problems else 0)
CHECK
)
if [ $? = 0 ]; then emit repositories-exclude-owned pass "$out"; else emit repositories-exclude-owned fail "$out"; fi

# What the package manager actually resolves: the enabled Fedora repositories
# offer none of the owned names.
cache=$(mktemp -d /var/tmp/luma-gate-repoquery.XXXXXX)
offered=$(dnf5 --quiet --setopt=cachedir="$cache" --setopt=system_cachedir="$cache" --refresh \
  repoquery --available --queryformat '%{name} %{repoid}\n' gnome-shell mutter gtk4 libadwaita gnome-control-center nautilus 2>"$cache/err")
code=$?
if [ "$code" != 0 ]; then
  emit fedora-offers-no-owned-package fail "dnf5 repoquery failed (exit $code): $(tail -n 3 "$cache/err")"
elif [ -n "$offered" ]; then
  emit fedora-offers-no-owned-package fail "still offered: $(tr '\n' ';' <<<"$offered")"
else
  emit fedora-offers-no-owned-package pass "no enabled repository offers gnome-shell, mutter, gtk4, libadwaita, gnome-control-center or nautilus"
fi
# And a control: the same query does resolve an ordinary Fedora package, so an
# empty answer above is the exclusion, not a broken repository.
if dnf5 --quiet --setopt=cachedir="$cache" --setopt=system_cachedir="$cache" \
     repoquery --available --queryformat '%{name}\n' htop 2>/dev/null | grep -qx htop; then
  emit fedora-repositories-answer pass "the same repositories offer htop"
else
  emit fedora-repositories-answer fail "the repositories offer no htop either; the check above proves nothing"
fi
rm -rf "$cache"

exit "$failed"
