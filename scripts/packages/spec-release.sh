# SPDX-License-Identifier: Apache-2.0
# shellcheck shell=bash
#
# Sourced by the package build scripts that apply a Luma spec patch
# (patches/<package>/0000-luma-fedora-spec.patch) to a Fedora spec.
#
# Inside a Git work tree (a build folder that is a clone, not an rsync) git
# apply resolves the patch's paths from the top of that tree, finds the spec
# outside the current directory, skips it as excluded and exits 0 having
# changed nothing. The build then produces the stock Fedora package under a
# Fedora release, and nothing says so. The build scripts set
# GIT_CEILING_DIRECTORIES so Git cannot look above rpmbuild, and then assert
# the result here: the exit status of the apply is not evidence, the spec's
# Release is.

# The Release a spec will build, with %{?dist} left off. A Release of
# "%autorelease -e X" is read the way rpmautospec expands it: the
# release_number its generated block sets, then ".X".
luma_spec_release() {
  local spec=$1 count line number numbers
  count=$(grep -cE '^Release:' "$spec" || true)
  if [ "$count" != 1 ]; then
    printf 'error: %s has %s Release: lines, expected 1\n' "$spec" "$count" >&2
    return 1
  fi
  line=$(sed -nE 's/^Release:[[:space:]]*//p' "$spec")
  line=${line%"${line##*[![:space:]]}"}
  line=${line%'%{?dist}'}
  case $line in
    '%autorelease -e '*)
      numbers=$(sed -nE 's/^[[:space:]]*release_number = ([0-9]+);[[:space:]]*$/\1/p' "$spec")
      if [ -z "$numbers" ] || [ "$(printf '%s\n' "$numbers" | wc -l)" -ne 1 ]; then
        printf 'error: %s uses %%autorelease but has no single rpmautospec release_number\n' \
          "$spec" >&2
        return 1
      fi
      number=$numbers
      line="$number.${line#'%autorelease -e '}"
      ;;
  esac
  printf '%s\n' "$line"
}

# The Release a spec patch sets: its one added Release: line, %{?dist} left
# off. For packages whose build script keeps no release of its own.
luma_patch_release() {
  local patch=$1 count line
  count=$(grep -cE '^\+Release:' "$patch" || true)
  if [ "$count" != 1 ]; then
    printf 'error: %s adds %s Release: lines, expected 1\n' "$patch" "$count" >&2
    return 1
  fi
  line=$(sed -nE 's/^\+Release:[[:space:]]*//p' "$patch")
  line=${line%"${line##*[![:space:]]}"}
  printf '%s\n' "${line%'%{?dist}'}"
}

# Fail, naming both values, unless the spec is at the intended Luma release.
luma_assert_spec_release() {
  local spec=$1 expected=$2 actual
  if [ -z "$expected" ]; then
    printf 'error: no intended Luma release was given for %s\n' "$spec" >&2
    return 1
  fi
  actual=$(luma_spec_release "$spec") || return 1
  if [ "$actual" != "$expected" ]; then
    printf 'error: %s is at Release %s after its Luma spec patch, expected %s; the patch did not apply and this would build the stock package\n' \
      "$(basename -- "$spec")" "$actual" "$expected" >&2
    return 1
  fi
  printf '%s: Release %s after the Luma spec patch\n' "$(basename -- "$spec")" "$actual"
}
