#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
# Build from the pinned Fedora Plymouth source already obtained by build-plymouth.
# No download, daemon, device, or installed-system mutation is performed.
if [[ $# != 3 ]]; then
  echo 'usage: boot-interpreter.sh PLYMOUTH_SOURCE LIBPLY_SHARED_OBJECT OUTPUT_DIR' >&2
  exit 2
fi
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
upstream=$(realpath "$1")
libply=$(realpath "$2")
mkdir -p "$3"
output=$(realpath "$3")
script=$upstream/src/plugins/splash/script
# Catch plausible-looking API names that parse but are silently unregistered.
python3 - "$script/script-lib-plymouth.c" "$repo_root/assets/boot" <<'PYTHON'
from pathlib import Path
import re, sys
api = Path(sys.argv[1]).read_text()
for file in Path(sys.argv[2]).glob('*.script'):
    for call in re.findall(r'Plymouth\.(\w+)\(', file.read_text()):
        assert f'"{call}"' in api, f'{file}: unsupported upstream API {call}'
PYTHON
for lib in math string; do
  python3 "$script/generate_script_string_header.py" "$script/script-lib-$lib.script" \
    >"$output/script-lib-$lib.script.h"
done
cc -O0 -g -D_GNU_SOURCE -I"$script" -I"$upstream/src/libply" -I"$output" \
  "$repo_root/tests/fixtures/boot/interpreter.c" \
  "$script"/{script,script-debug,script-execute,script-object,script-parse,script-scan,script-lib-math,script-lib-string}.c \
  "$libply" -Wl,-rpath,"${libply%/*}" -lm -o "$output/interpreter"
for presentation in luma-loading luma-loading-handheld; do
  "$output/interpreter" "$repo_root/tests/fixtures/boot/renderer.script" \
    "$repo_root/assets/boot/$presentation.script" \
    "$repo_root/tests/fixtures/boot/assertions.script"
done
# Execute every desktop profile through the native interpreter with the same
# behavioral assertions; renderer doubles are explicitly not visual evidence.
python3 - "$repo_root" "$output" <<'PYTHON'
from pathlib import Path
import importlib.util,itertools,subprocess,sys
root,output=map(Path,sys.argv[1:])
spec=importlib.util.spec_from_file_location('compiler',root/'scripts/boot/compile-theme.py')
compiler=importlib.util.module_from_spec(spec);spec.loader.exec_module(compiler)
template=(root/'assets/boot/luma-theme.script.in').read_text()
count=0
for values in itertools.product(*compiler.CHOICES.values()):
    profile=dict(zip(compiler.CHOICES,values))
    theme=output/'matrix.script';theme.write_text(compiler.script(profile,template))
    subprocess.run([str(output/'interpreter'),str(root/'tests/fixtures/boot/renderer.script'),
                    str(theme),str(root/'tests/fixtures/boot/assertions.script')],check=True,stdout=subprocess.DEVNULL)
    count+=1
print(f'Plymouth interpreter desktop profile matrix: {count} PASS')
PYTHON
printf 'fun invalid( {\n' >"$output/invalid.script"
if "$output/interpreter" "$output/invalid.script"; then
  echo 'error: parser accepted invalid syntax' >&2; exit 1
fi
