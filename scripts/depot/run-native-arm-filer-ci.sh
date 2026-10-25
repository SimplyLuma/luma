#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Disposable GitHub native ARM runner. No signing, publication or secret mounts.
set -euo pipefail
test "${RUNNER_ENVIRONMENT:-}" = github-hosted
test "${RUNNER_ARCH:-}" = ARM64
test "$(uname -m)" = aarch64
: "${GITHUB_WORKSPACE:?}" "${RUNNER_TEMP:?}" "${GITHUB_RUN_ID:?}" "${GITHUB_RUN_ATTEMPT:?}"
[[ "$GITHUB_RUN_ID:$GITHUB_RUN_ATTEMPT" =~ ^[0-9]+:[0-9]+$ ]]
CI_SOURCE=$GITHUB_WORKSPACE
PRODUCT_SOURCE=$GITHUB_WORKSPACE/product-source
CI_OUT=$GITHUB_WORKSPACE/build/native-arm-filer-ci
CI_INPUTS=$GITHUB_WORKSPACE/build/native-arm-filer-inputs
DEPOT_ROOT=$GITHUB_WORKSPACE/build/native-arm-filer-depot
test ! -e "$CI_OUT" && test ! -e "$CI_INPUTS" && test ! -e "$DEPOT_ROOT"
mkdir -p "$CI_OUT" "$CI_INPUTS" "$DEPOT_ROOT"
test "$(git -C "$PRODUCT_SOURCE" rev-parse HEAD)" = bdaf62fd08989b790decfac941a6947f8f72273a
test -z "$(git -C "$PRODUCT_SOURCE" status --porcelain --untracked-files=no)"
# Refuse unrendered/unreviewed transport before fetching anything.
python3 - "$CI_SOURCE/config/depot/native-arm-filer-transport.json" > "$CI_OUT/downloads.tsv" <<'PY'
import json,re,sys
from urllib.parse import urlsplit
d=json.load(open(sys.argv[1]));assert d['schema']=='org.projectluma.depot.native-filer-transport/v1'
for key,name,limit in [('runtime_archive','runtime-input.tar',8*1024**3),('filer_archive','filer-input.tar',256*1024**2)]:
    r=d[key];assert set(r)=={'url','sha256','bytes'}
    assert re.fullmatch('[a-f0-9]{64}',r.get('sha256') or '') and type(r.get('bytes')) is int and 0<r['bytes']<=limit
    u=urlsplit(r['url']);assert u.scheme=='https' and u.netloc=='dl.simplyluma.com' and u.path=='/inputs/depot-build/'+r['sha256']+'/'+name and not u.query and not u.fragment
    print(name,r['url'],r['sha256'],r['bytes'],sep='\t')
PY
# Only unused preinstalled tools on this disposable worker are removed.
sudo rm -rf -- /usr/local/lib/android /usr/share/dotnet /opt/ghc
test "$(df --output=avail -B1 "$GITHUB_WORKSPACE" | tail -n 1 | tr -d ' ')" -ge 34359738368
while IFS=$'\t' read -r name url digest bytes; do
  curl --fail --silent --show-error --proto '=https' --max-time 180 --max-filesize "$bytes" --output "$CI_INPUTS/$name.part" --url "$url"
  test "$(stat -c %s "$CI_INPUTS/$name.part")" = "$bytes"
  printf '%s  %s\n' "$digest" "$CI_INPUTS/$name.part" | sha256sum --check --status
  mv --no-clobber "$CI_INPUTS/$name.part" "$CI_INPUTS/$name"
done < "$CI_OUT/downloads.tsv"
# Match the maintained desktop-depot worker sandbox, limited to this job.
profile=$RUNNER_TEMP/luma-native-filer.apparmor
name=luma-native-filer-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT
cleanup() {
  if [ -s "$CI_OUT/container-id" ]; then
    local id
    id=$(cat "$CI_OUT/container-id")
    [[ "$id" =~ ^[a-f0-9]{64}$ ]]
    test "$(docker inspect --format '{{ index .Config.Labels "org.projectluma.native-filer-run" }}' "$id")" = "$GITHUB_RUN_ID:$GITHUB_RUN_ATTEMPT"
    docker inspect --format '{{json .State}}' "$id" > "$CI_OUT/CONTAINER-BEFORE-CLEANUP.json"
    local stop_status=0
    timeout --signal=TERM --kill-after=2s 15s docker stop -t 10 "$id" > "$CI_OUT/container-stop.txt" 2>&1 || stop_status=$?
    printf 'Docker stop exit status: %s\n' "$stop_status"
    timeout --signal=TERM --kill-after=2s 10s docker rm --force "$id" > "$CI_OUT/container-removal.txt"
    remaining=$(docker ps --all --no-trunc --filter "id=$id" --format '{{.ID}}')
    test -z "$remaining"
    python3 - "$CI_OUT" "$id" <<'PY'
import json,pathlib,sys
p=pathlib.Path(sys.argv[1]);before=json.loads((p/'CONTAINER-BEFORE-CLEANUP.json').read_text())
(p/'CONTAINER-CLOSED.json').write_text(json.dumps(dict(container_id=sys.argv[2],container_removed=True,remaining_matching_containers=0,state_before_removal=before),indent=2)+'\n')
PY
  fi
  if [ -f "$profile" ]; then sudo apparmor_parser --remove "$profile"; fi
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
cat > "$profile" <<'APPARMOR'
abi <abi/4.0>,
profile luma-native-filer flags=(unconfined) {
  userns,
}
APPARMOR
sudo apparmor_parser --replace "$profile"
cpus=$(python3 -c 'import os; a=sorted(os.sched_getaffinity(0))[:4]; assert a; print(",".join(map(str,a)))')
image=registry.fedoraproject.org/fedora@sha256:22dbf85bb441d15ebec58522f98636699b4dc722d89f19a3f01c8ed382695b57
status=0
docker run --name "$name" --cidfile "$CI_OUT/container-id" \
  --label "org.projectluma.native-filer-run=$GITHUB_RUN_ID:$GITHUB_RUN_ATTEMPT" \
  --cpuset-cpus "$cpus" --cpus=4 --memory=4g --memory-swap=4g --pids-limit=2048 \
  --cap-add SYS_ADMIN --cap-add NET_ADMIN --device /dev/fuse \
  --security-opt apparmor=luma-native-filer --security-opt seccomp=unconfined \
  --security-opt systempaths=unconfined \
  --env "PRODUCT_SOURCE=$PRODUCT_SOURCE" --env "CI_SOURCE=$CI_SOURCE" \
  --env "CI_OUT=$CI_OUT" --env "CI_INPUTS=$CI_INPUTS" --env "DEPOT_ROOT=$DEPOT_ROOT" \
  --volume "$GITHUB_WORKSPACE:$GITHUB_WORKSPACE" --volume "$PRODUCT_SOURCE:$PRODUCT_SOURCE:ro" \
  --workdir "$GITHUB_WORKSPACE" "$image" \
  bash "$CI_SOURCE/scripts/depot/build-native-arm-filer-ci.sh" > "$CI_OUT/runner.log" 2>&1 || status=$?
printf 'Docker build exit status: %s\n' "$status"
if [ "$status" = 0 ]; then
  python3 - "$CI_OUT/PROVIDER.json" "$CI_OUT/provider.tar" <<'PY'
import hashlib,json,pathlib,sys
d=json.load(open(sys.argv[1]));p=pathlib.Path(sys.argv[2]);assert d['source']=='bdaf62fd08989b790decfac941a6947f8f72273a' and d['architecture']=='aarch64'
assert d['result']=='NATIVE_ARM_FILER_MAINTAINED_UNSIGNED_PROVIDER_NOT_RUNTIME_OR_RELEASE_QUALIFIED'
with p.open('rb') as stream:assert hashlib.file_digest(stream,'sha256').hexdigest()==d['archive_sha256']
assert p.stat().st_size==d['archive_bytes']
PY
fi
exit "$status"
