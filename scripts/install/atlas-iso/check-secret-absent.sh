#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Fail when a secret appears in any file under the given paths.
#
#   check-secret-absent.sh --secret FILE [--exclude PATH]... [--label TEXT] PATH...
#
# FILE holds the secret on one line. It is matched as a fixed string in every
# regular file (binary files included) under each PATH, following no symbolic
# links. An --exclude PATH is the one place the secret is meant to be; it must
# be given exactly as it will be found (the same prefix as the PATH argument).
# Only file names are printed, never the secret. Exit 0 when the secret is
# nowhere else, 1 when it is found, 2 on a usage or read error.

set -euo pipefail

secret="" label="" excludes=() paths=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --secret) secret=${2:?}; shift 2 ;;
    --exclude) excludes+=("${2:?}"); shift 2 ;;
    --label) label=${2:?}; shift 2 ;;
    --) shift; paths+=("$@"); break ;;
    -*) printf 'check-secret-absent: unknown option %s\n' "$1" >&2; exit 2 ;;
    *) paths+=("$1"); shift ;;
  esac
done
[ -n "$secret" ] && [ "${#paths[@]}" -gt 0 ] || {
  printf 'usage: check-secret-absent.sh --secret FILE [--exclude PATH]... [--label TEXT] PATH...\n' >&2; exit 2; }
[ -s "$secret" ] || { printf 'check-secret-absent: the secret file is missing or empty\n' >&2; exit 2; }
# One non-empty line: an empty pattern line would match every file.
[ "$(grep -c . "$secret")" = 1 ] && ! grep -q '^$' "$secret" || {
  printf 'check-secret-absent: the secret file must hold exactly one non-empty line\n' >&2; exit 2; }

found=0
for path in "${paths[@]}"; do
  [ -e "$path" ] || { printf 'check-secret-absent: %s does not exist\n' "$path" >&2; exit 2; }
  while IFS= read -r -d '' hit; do
    skip=0
    for exclude in "${excludes[@]}"; do
      [ "$hit" = "$exclude" ] && skip=1
    done
    [ "$skip" = 1 ] && continue
    printf 'error: the secret appears in %s\n' "$hit" >&2
    found=1
  done < <(grep -rlFaZ -D skip -f "$secret" -- "$path" || true)
done
if [ "$found" = 1 ]; then
  exit 1
fi
printf 'no copy of the secret %s\n' "${label:-under ${paths[*]}}"
