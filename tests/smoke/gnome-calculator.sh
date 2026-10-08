#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Calculator's product surface, asserted against the patch set rather than a
# running desktop: the patches are the source of truth for what the package
# builds, and this test must pass without an emulator, a session, or an
# installed file.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
fail() { printf 'Calculator smoke test: FAIL: %s\n' "$1" >&2; exit 1; }

patch_dir="$repo_root/patches/gnome-calculator"
spec_patch="$patch_dir/0000-luma-fedora-spec.patch"
product="$patch_dir/0009-luma-advanced-mode-and-readout.patch"
build_script="$repo_root/scripts/packages/build-gnome-calculator.sh"

for required in "$spec_patch" "$product" "$build_script"; do
  [ -f "$required" ] || fail "missing source contract: $required"
done

# The package carries the patch, and the release moved with it.
grep -Fq '+Patch1008:      0009-luma-advanced-mode-and-readout.patch' "$spec_patch" || \
  fail 'the spec does not apply the advanced-mode patch'
grep -Fq '+Release:        1.luma.9%{?dist}' "$spec_patch" || \
  fail 'the release was not bumped for this change'
grep -Fq '0009-luma-advanced-mode-and-readout.patch' "$build_script" || \
  fail 'the build script does not install the advanced-mode patch'

# Basic is the default view and Advanced is a real one, chosen by a setting the
# window listens to rather than a panel that is pinned in code.
grep -Fq '+        panel_stack.visible_child = load_mode (_mode);' "$product" || \
  fail 'the keypad is still pinned to Basic'
grep -Fq '+        add_action (settings.create_action ("button-mode"));' "$product" || \
  fail 'the view is not offered as a setting'
grep -Fq '+      action: "win.button-mode";' "$product" || \
  fail 'the menu does not offer the view'
grep -Fq '+      target: "advanced";' "$product" || fail 'Advanced is not offered'
grep -Fq '+      target: "basic";' "$product" || fail 'Basic is not offered'

# The scientific keypad is the design's four rows, in the design's order, and
# every key drives an action the engine already has.
for key in 'cal.square' "'³'" "'^'" "'e'" "'√'" "'∛'" "'⁻¹'" "'10'" \
           "'ln'" "'log'" "'!'" "'π'" "'sin'" "'cos'" "'tan'"; do
  grep -Fq "$key" "$product" || fail "the scientific keypad is missing $key"
done
grep -Fq '+    Grid scientific {' "$product" || fail 'there is no scientific keypad'
grep -Fq '+      visible: false;' "$product" || \
  fail 'the scientific keypad is not hidden in Basic'
grep -Fq '+  Box keypads {' "$product" || fail 'the keypads do not share one row'
grep -Fq '+    spacing: 8;' "$product" || fail 'the keypad gap is not the design gap'

# The answer is the largest thing in the window, and it sits where the design
# puts it.
grep -Fq '+    font-size: 40px;' "$product" || fail 'the result is not the design size'
grep -Fq '+    font-weight: 650;' "$product" || fail 'the result is not the design weight'
grep -Fq '+      yalign: 1;' "$product" || fail 'the result is not at the bottom'
grep -Fq 'xalign: 1;' "$product" || fail 'the result is not at the right'

# A result format the user picks has to be visible in the readout.
grep -Fq '+        equation.display_changed.connect' "$product" || \
  fail 'the readout does not follow a result-format change'

# The clear key says which clear it is.
grep -Fq '+      Button calc_clear_button {' "$product" || \
  fail 'the clear key is not stateful'
grep -Fq 'equation.is_empty ? "AC" : "C"' "$product" || \
  fail 'the clear key does not distinguish AC from C'

printf 'Calculator smoke test: OK\n'
