#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Run only on an ephemeral native ARM GitHub host. Product sandboxing stays on.
set -euo pipefail
[ "${GITHUB_ACTIONS:-}" = true ]
[ "${RUNNER_OS:-}" = Linux ] && [ "$(uname -m)" = aarch64 ]
[ ! -f /.dockerenv ]
[[ ${GITHUB_RUN_ID:-} =~ ^[0-9]+$ && ${GITHUB_RUN_ATTEMPT:-} =~ ^[0-9]+$ ]]
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
out="$root/build/browser-runtime-evidence"
mkdir -p "$out"
profile="luma-browser-ci-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
profile_file=$(mktemp)
seccomp_file=$(mktemp)
container=
profile_loaded=false
owner_uid=$(id -u)
owner_gid=$(id -g)
cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [ -n "$container" ]; then
    # The proof creates UID 1100 mode-0600 evidence. The host artifact action
    # needs to read it after the container has exited; never chown source files.
    docker exec "$container" chown -R "$owner_uid:$owner_gid" \
      /workspace/build/browser-runtime-evidence || status=1
    docker rm -f "$container" >/dev/null || status=1
  fi
  if [ "$profile_loaded" = true ]; then
    sudo apparmor_parser --remove "$profile_file" || status=1
  fi
  rm -f "$profile_file" "$seccomp_file"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Ubuntu restricts even plain "unconfined" unprivileged user namespaces.
# Give only this disposable container an explicit userns rule, without changing
# host sysctls or any Flatpak/Chromium permission or sandbox.
# https://ubuntu.com/blog/ubuntu-23-10-restricted-unprivileged-user-namespaces
cat > "$profile_file" <<EOF
abi <abi/4.0>,
profile $profile flags=(default_allow) {
  userns,
}
EOF
sudo apparmor_parser --replace "$profile_file"
profile_loaded=true
container=$(docker create --name "$profile" --cap-add=SYS_ADMIN \
  --security-opt "apparmor=$profile" --shm-size=512m \
  --mount "type=bind,source=$root,target=/workspace" --workdir /workspace \
  --env "BROWSER_GATE_SIGNED_INPUT_URL=${BROWSER_GATE_SIGNED_INPUT_URL:-}" \
  --env "BROWSER_GATE_SIGNED_INPUT_SHA256=${BROWSER_GATE_SIGNED_INPUT_SHA256:-}" \
  registry.fedoraproject.org/fedora@sha256:22dbf85bb441d15ebec58522f98636699b4dc722d89f19a3f01c8ed382695b57 \
  sleep infinity)
docker inspect --format '{{.AppArmorProfile}}' "$container" > "$out/container-apparmor.txt"
[ "$(cat "$out/container-apparmor.txt")" = "$profile" ]
docker start "$container" >/dev/null
# Docker's default filter denies pivot_root, which Bubblewrap needs inside its
# own mount namespace. Preserve this host's exact resolved default filter and
# add only that syscall for the disposable outer fixture container.
sudo python3 - "$container" "$seccomp_file" "$out" <<'SECCOMP'
import hashlib,json,os,sys
from pathlib import Path
identity,destination,evidence=sys.argv[1:]
assert len(identity)==64 and all(c in '0123456789abcdef' for c in identity)
paths=[Path('/run/containerd/io.containerd.runtime.v2.task/moby')/identity/'config.json',
       Path('/run/docker/runtime-runc/moby')/identity/'config.json']
found=[p for p in paths if p.is_file()]
assert len(found)==1, 'Cannot identify exact disposable container OCI bundle'
policy=json.loads(found[0].read_text())['linux']['seccomp']
assert policy['defaultAction']=='SCMP_ACT_ERRNO'
assert 'SCMP_ARCH_AARCH64' in policy['architectures']
allowed={name for rule in policy['syscalls'] if rule['action']=='SCMP_ACT_ALLOW' for name in rule['names']}
assert {'read','write','execve','mount','unshare'} <= allowed, 'Expected default container syscall rules are missing'
assert not any('pivot_root' in rule['names'] for rule in policy['syscalls'])
original=json.dumps(policy,sort_keys=True).encode()
policy['syscalls'].append({'names':['pivot_root'],'action':'SCMP_ACT_ALLOW'})
encoded=json.dumps(policy,sort_keys=True).encode()
Path(destination).write_bytes(encoded);os.chmod(destination,0o644)
Path(evidence,'outer-seccomp-adjustment.json').write_text(json.dumps({
 'scope':'DISPOSABLE_OUTER_CI_CONTAINER_ONLY',
 'source_container':identity,'base_sha256':hashlib.sha256(original).hexdigest(),
 'effective_sha256':hashlib.sha256(encoded).hexdigest(),
 'added_syscalls':['pivot_root'],'default_action':policy['defaultAction'],
 'app_sandbox_changed':False},indent=2)+'\n')
SECCOMP
docker rm -f "$container" >/dev/null
container=$(docker create --name "$profile" --cap-add=SYS_ADMIN \
  --security-opt "apparmor=$profile" --security-opt "seccomp=$seccomp_file" --shm-size=512m \
  --mount "type=bind,source=$root,target=/workspace" --workdir /workspace \
  --env "BROWSER_GATE_SIGNED_INPUT_URL=${BROWSER_GATE_SIGNED_INPUT_URL:-}" \
  --env "BROWSER_GATE_SIGNED_INPUT_SHA256=${BROWSER_GATE_SIGNED_INPUT_SHA256:-}" \
  registry.fedoraproject.org/fedora@sha256:22dbf85bb441d15ebec58522f98636699b4dc722d89f19a3f01c8ed382695b57 \
  sleep infinity)
docker start "$container" >/dev/null
[ "$(docker inspect --format '{{.AppArmorProfile}}' "$container")" = "$profile" ]
sudo python3 - "$container" "$seccomp_file" "$out" <<'VERIFY_SECCOMP'
import hashlib,json,sys
from pathlib import Path
identity,expected,evidence=sys.argv[1:]
assert len(identity)==64 and all(c in '0123456789abcdef' for c in identity)
paths=[Path('/run/containerd/io.containerd.runtime.v2.task/moby')/identity/'config.json',
       Path('/run/docker/runtime-runc/moby')/identity/'config.json']
found=[p for p in paths if p.is_file()]
assert len(found)==1
actual=json.loads(found[0].read_text())['linux']['seccomp']
assert actual==json.loads(Path(expected).read_text()), 'Outer seccomp differs from reviewed policy'
p=Path(evidence,'outer-seccomp-adjustment.json');record=json.loads(p.read_text())
record['verified_container']=identity
record['actual_effective_sha256']=hashlib.sha256(json.dumps(actual,sort_keys=True).encode()).hexdigest()
assert record['actual_effective_sha256']==record['effective_sha256']
p.write_text(json.dumps(record,indent=2)+'\n')
VERIFY_SECCOMP
docker exec -i "$container" bash -seuo pipefail <<'CONTAINER'
dnf5 -y install git python3 curl findutils shadow-utils util-linux bubblewrap
# Fail before downloading the signed 1 GB archive if host namespace mediation
# still blocks a normal unprivileged Bubblewrap sandbox. Numeric UID/GID needs
# no login account, so the main probe can create its fresh normal user later.
cat /proc/self/attr/current > build/browser-runtime-evidence/container-apparmor-current.txt
timeout 15 setpriv --reuid=1100 --regid=1100 --clear-groups \
  bwrap --die-with-parent --unshare-user --unshare-pid --unshare-uts --unshare-ipc \
  --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/lib64 /lib64 \
  --proc /proc --dev /dev /usr/bin/true \
  > build/browser-runtime-evidence/host-namespace-preflight.stdout \
  2> build/browser-runtime-evidence/host-namespace-preflight.stderr
exec bash scripts/os/probe-desktop-browser-runtime.sh
CONTAINER
