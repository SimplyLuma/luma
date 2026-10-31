#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Ordinary maintained Filer producer, inside the disposable native Fedora tools.
set -euo pipefail
: "${PRODUCT_SOURCE:?}" "${CI_SOURCE:?}" "${CI_OUT:?}" "${CI_INPUTS:?}" "${DEPOT_ROOT:?}"
export DEPOT_ARCH=aarch64 DEPOT_TOOLS_ARCH=aarch64 DEPOT_INSIDE_TOOLS=1
export DEPOT_KEYS=/no-signing-keys-native-filer DEPOT_BUILD_JOBS=4
export DEPOT_BUILD_MIN_FREE_BYTES=10737418240
export FLATPAK_USER_DIR="$DEPOT_ROOT/flatpak-user" TMPDIR="$DEPOT_ROOT/tmp"
test "$(uname -m)" = aarch64
if [ "${1:-}" != --user ]; then
  test "$(id -u)" = 0
  install -d "$DEPOT_ROOT/tmp"
  bash "$PRODUCT_SOURCE/scripts/depot/install-tools-packages.sh"
  dnf5 -q -y install shadow-utils dbus-daemon dbus-tools
  rpm -qa --qf '%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' | sort > "$CI_OUT/tools-rpms.tsv"
  python3 -B "$CI_SOURCE/scripts/depot/native-filer-inputs.py" \
    "$CI_SOURCE/config/depot/native-arm-filer-inputs.json" \
    "$CI_SOURCE/config/depot/native-arm-filer-transport.json" "$CI_INPUTS" > "$CI_OUT/INPUTS-VERIFIED.json"
  useradd --create-home --uid 1000 luma-ci
  install -d -o luma-ci -g luma-ci "$DEPOT_ROOT" "$DEPOT_ROOT/tmp" "$DEPOT_ROOT/logs" "$DEPOT_ROOT/site" "$DEPOT_ROOT/inputs"
  install -d "$DEPOT_ROOT/repo" "$DEPOT_ROOT/inputs/filer"
  mount --bind "$CI_INPUTS/runtime/repo" "$DEPOT_ROOT/repo"
  mount -o remount,bind,ro "$DEPOT_ROOT/repo"
  mount --bind "$CI_INPUTS/filer" "$DEPOT_ROOT/inputs/filer"
  mount -o remount,bind,ro "$DEPOT_ROOT/inputs/filer"
  install -m 0444 "$CI_INPUTS/runtime/site/luma.flatpakrepo" "$DEPOT_ROOT/site/luma.flatpakrepo"
  chown -R luma-ci:luma-ci "$CI_OUT"
  exec runuser -u luma-ci -- env HOME=/home/luma-ci PRODUCT_SOURCE="$PRODUCT_SOURCE" \
    CI_SOURCE="$CI_SOURCE" CI_OUT="$CI_OUT" CI_INPUTS="$CI_INPUTS" DEPOT_ROOT="$DEPOT_ROOT" \
    DEPOT_ARCH="$DEPOT_ARCH" DEPOT_TOOLS_ARCH="$DEPOT_TOOLS_ARCH" DEPOT_INSIDE_TOOLS="$DEPOT_INSIDE_TOOLS" \
    DEPOT_KEYS="$DEPOT_KEYS" DEPOT_BUILD_JOBS="$DEPOT_BUILD_JOBS" DEPOT_BUILD_MIN_FREE_BYTES="$DEPOT_BUILD_MIN_FREE_BYTES" \
    FLATPAK_USER_DIR="$FLATPAK_USER_DIR" TMPDIR="$TMPDIR" \
    dbus-run-session -- bash "$CI_SOURCE/scripts/depot/build-native-arm-filer-ci.sh" --user
fi
test "$(id -u)" = 1000
test "$(git -c safe.directory="$PRODUCT_SOURCE" -C "$PRODUCT_SOURCE" rev-parse HEAD)" = bdaf62fd08989b790decfac941a6947f8f72273a
test -z "$(git -c safe.directory="$PRODUCT_SOURCE" -C "$PRODUCT_SOURCE" status --porcelain --untracked-files=no)"
test ! -e "$DEPOT_KEYS"
bwrap --unshare-user --unshare-pid --ro-bind / / /usr/bin/true
python3 - "$DEPOT_ROOT" "$CI_OUT" <<'PY'
import json,os,pathlib,subprocess,sys
root,out=map(pathlib.Path,sys.argv[1:]);affinity=sorted(os.sched_getaffinity(0))
assert 1<=len(affinity)<=4 and int(subprocess.check_output(['nproc']))==len(affinity)
assert os.statvfs(root/'repo').f_flag&os.ST_RDONLY and os.statvfs(root/'inputs/filer').f_flag&os.ST_RDONLY
assert pathlib.Path('/sys/fs/cgroup/memory.max').read_text().strip()=='4294967296'
assert pathlib.Path('/sys/fs/cgroup/memory.swap.max').read_text().strip()=='0'
(out/'HOST-RESOURCES.json').write_text(json.dumps(dict(affinity=affinity,nproc=len(affinity),memory_max=4294967296,swap_max=0,read_only_inputs=True,uid=os.getuid()),indent=2)+'\n')
PY
# Full mirror integrity and normal signed deployment checks. No GPG exceptions.
ostree --repo="$DEPOT_ROOT/repo" fsck > "$CI_OUT/runtime-fsck.txt" 2>&1
cd "$PRODUCT_SOURCE"
bash scripts/depot/install-build-runtime.sh
python3 - "$CI_SOURCE/config/depot/native-arm-filer-inputs.json" "$DEPOT_ROOT" "$CI_OUT" <<'PY'
import json,pathlib,subprocess,sys
profile=json.load(open(sys.argv[1]));root=pathlib.Path(sys.argv[2]);out=pathlib.Path(sys.argv[3]);actual={}
for kind in ('Platform','Sdk'):
    actual[kind]=subprocess.check_output(['flatpak','info','--user','--show-commit','org.projectluma.'+kind+'//44'],text=True).strip()
    assert actual[kind]==profile['refs']['runtime/org.projectluma.'+kind+'/aarch64/44']
actual['linter']=subprocess.check_output(['flatpak','info','--user','--show-commit','org.flatpak.Builder'],text=True).strip()
(out/'INSTALLED-INPUTS.json').write_text(json.dumps(actual,indent=2)+'\n')
PY
# A real SDK process checks affinity before any application compiler is started.
flatpak build-init --arch=aarch64 "$DEPOT_ROOT/affinity-probe" org.projectluma.NativeFilerAffinityProbe org.projectluma.Sdk org.projectluma.Platform 44
flatpak build "$DEPOT_ROOT/affinity-probe" /usr/bin/python3 -B -I -c \
  'import os,platform,json,subprocess; a=sorted(os.sched_getaffinity(0)); assert platform.machine()=="aarch64" and 1<=len(a)<=4 and int(subprocess.check_output(["nproc"]))==len(a); print(json.dumps(dict(machine=platform.machine(),affinity=a,nproc=len(a))))' > "$CI_OUT/SDK-AFFINITY.json"
python3 - "$CI_OUT" <<'PY'
import json,pathlib,sys
p=pathlib.Path(sys.argv[1]);assert json.loads((p/'SDK-AFFINITY.json').read_text())['affinity']==json.loads((p/'HOST-RESOURCES.json').read_text())['affinity']
PY
bash scripts/depot/build-app.sh "$DEPOT_ROOT/inputs/filer/org.gnome.Nautilus.yml" beta
python3 -B "$CI_SOURCE/scripts/depot/finish-native-filer-ci.py" "$DEPOT_ROOT" "$CI_OUT" "$CI_SOURCE/config/depot/native-arm-filer-inputs.json"
