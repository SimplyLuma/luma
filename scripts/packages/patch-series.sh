# SPDX-License-Identifier: Apache-2.0
# shellcheck shell=bash
#
# luma_assert_patch_series PACKAGE SPEC SOURCES [FILE:REASON ...]
#
# Fails unless every numbered patch file in patches/PACKAGE (except the spec
# patch and the listed exclusions, each of which must give a reason) is
# declared as a PatchNNNN tag in SPEC, in filename order, and was copied into
# SOURCES, and SPEC's %prep applies every declared patch with %autosetup.
# A patch file that exists in the repository but is not in the built package
# is how the Filer and Settings redesigns were silently left out of a release.
luma_assert_patch_series() {
  local package=$1 spec=$2 sources=$3
  shift 3
  local dir="$repo_root/patches/$package" excluded='' entry file expected declared count
  for entry in "$@"; do
    file=${entry%%:*}
    [ -n "$file" ] && [ "$file" != "$entry" ] && [ -n "${entry#*:}" ] || {
      printf 'error: patch exclusion needs FILE:REASON, got %s\n' "$entry" >&2
      return 1
    }
    [ -f "$dir/$file" ] || {
      printf 'error: excluded patch %s does not exist\n' "$file" >&2
      return 1
    }
    excluded="$excluded$file"$'\n'
  done
  expected=$(cd "$dir" && for file in 0[0-9][0-9][0-9]-*.patch; do
      [ "$file" = 0000-luma-fedora-spec.patch ] && continue
      printf '%s' "$excluded" | grep -qxF -- "$file" && continue
      printf '%s\n' "$file"
    done | LC_ALL=C sort)
  declared=$(sed -n 's/^Patch\([0-9][0-9]*\):[[:space:]]*\(0[0-9][0-9][0-9]-[^[:space:]]*\.patch\)[[:space:]]*$/\1 \2/p' "$spec" |
    sort -n | cut -d' ' -f2)
  count=$(printf '%s\n' "$expected" | grep -c .)
  [ "$count" -gt 0 ] || { printf 'error: no patch files found in %s\n' "$dir" >&2; return 1; }
  if [ "$declared" != "$expected" ]; then
    printf 'error: %s spec does not declare exactly its %d patch files in filename order:\n' \
      "$package" "$count" >&2
    diff <(printf '%s\n' "$expected") <(printf '%s\n' "$declared") >&2 || true
    return 1
  fi
  while IFS= read -r file; do
    [ -f "$sources/$file" ] || {
      printf 'error: %s was not copied into SOURCES\n' "$file" >&2
      return 1
    }
  done <<<"$expected"
  grep -Eq '^%autosetup -p1( |$)' "$spec" && ! grep -Eq '^%autosetup.* -N( |$)' "$spec" || {
    printf 'error: %s %%prep does not apply every declared patch with %%autosetup -p1\n' \
      "$package" >&2
    return 1
  }
  printf '%s patch series: %d of %d patch files declared, copied and applied by %%autosetup\n' \
    "$package" "$count" "$count"
}
