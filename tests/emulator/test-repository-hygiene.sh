#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# The emulator deals in disk images, SSH keys and guest logs. This suite exists
# so none of that can reach the repository by accident.

set -uo pipefail
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/../.." && pwd)
. "$here/lib.sh"

printf '== repository hygiene\n'

tracked=$(git -C "$repo" ls-files 'tools/luma-emulator' 'scripts/emulator' 'config/emulator' 'tests/emulator')

printf '%s' "$tracked" | while read -r path; do :; done

check_no_tracked() {
  local name=$1 pattern=$2
  local hits
  hits=$(git -C "$repo" ls-files | grep -Ei "$pattern" || true)
  if [ -z "$hits" ]; then
    pass "$name"
  else
    fail "$name" "tracked: $(printf '%s' "$hits" | head -3 | tr '\n' ' ')"
  fi
}

check_no_tracked "no disk images are tracked" '\.(raw|qcow2|img|iso|vdi|vmdk|vhd|ova|box)$'
check_no_tracked "no EFI variable stores are tracked" '\.(nvram|fd)$'
check_no_tracked "no private keys are tracked" '(^|/)(id_|guest_)(rsa|ed25519|ecdsa)$|\.(pem|p12|pfx)$'
check_no_tracked "no known_hosts is tracked" '(^|/)known_hosts$'
check_no_tracked "no SwiftPM build tree is tracked" 'tools/luma-emulator/\.build/'
check_no_tracked "no application bundle is tracked" '\.app/'

printf '== the emulator writes nothing into the repository at run time\n'
if git -C "$repo" check-ignore -q build/emulator; then
  pass "build/emulator is ignored"
else
  fail "build/emulator is ignored" "git would track the emulator build output"
fi
for candidate in \
  "tools/luma-emulator/.build" \
  "config/emulator/provision/guest_ed25519" \
  "tests/emulator/known_hosts"
do
  if git -C "$repo" check-ignore -q "$candidate"; then
    pass "$candidate is ignored"
  else
    fail "$candidate is ignored" "not covered by .gitignore"
  fi
done

printf '== no secrets or machine-specific paths are baked into the source\n'
sources=$(git -C "$repo" ls-files 'tools/luma-emulator/**' 'scripts/emulator/**' 'config/emulator/**' 'tests/emulator/**')
leak=""
for file in $sources; do
  case "$file" in
    tests/emulator/*) continue ;;
  esac
  if grep -nE 'BEGIN [A-Z ]*PRIVATE KEY|ssh-ed25519 AAAA|/Users/[a-z]' "$repo/$file" >/dev/null 2>&1; then
    leak="$leak $file"
  fi
done
if [ -z "$leak" ]; then
  pass "no keys or hard-coded home paths in emulator sources"
else
  fail "no keys or hard-coded home paths in emulator sources" "$leak"
fi

printf '== the emulator adds no dependency to any Luma package\n'
if grep -rl "luma-emulator" "$repo/packaging" >/dev/null 2>&1; then
  fail "no Luma package references the emulator" "found a reference under packaging/"
else
  pass "no Luma package references the emulator"
fi
if grep -rl "luma-emulator" "$repo/config/desktop" "$repo/config/shared" >/dev/null 2>&1; then
  fail "no composition input references the emulator" "found a reference in composition inputs"
else
  pass "no composition input references the emulator"
fi

printf '== the emulator does not route Luma function through Android\n'
if grep -rniE 'waydroid|android' \
     "$repo/tools/luma-emulator/Sources" \
     >/dev/null 2>&1; then
  fail "no Android or Waydroid dependency" "an Android reference appeared in emulator sources"
else
  pass "no Android or Waydroid dependency"
fi
if python3 "$repo/tests/unit/test_emulator_compatibility.py"; then
  pass "compatibility is optional, architecture-gated and image-verified"
else
  fail "compatibility admission" "native default or optional image admission failed"
fi

printf '== shell sources are syntactically valid\n'
for script in "$repo"/scripts/emulator/*.sh "$repo"/tests/emulator/*.sh \
              "$repo"/config/emulator/provision/provision.sh \
              "$repo"/config/emulator/provision/luma-emulator-capture \
              "$repo"/config/emulator/provision/luma-emulator-session; do
  [ -f "$script" ] || continue
  if bash -n "$script" 2>/dev/null; then
    pass "bash -n $(basename "$script")"
  else
    fail "bash -n $(basename "$script")" "$(bash -n "$script" 2>&1 | head -2)"
  fi
done

if python3 -c "import ast,sys; ast.parse(open('$repo/config/emulator/provision/luma-emulator-a11y.py').read())" 2>/dev/null; then
  pass "the accessibility helper parses"
else
  fail "the accessibility helper parses" "syntax error"
fi

summary "repository hygiene"
