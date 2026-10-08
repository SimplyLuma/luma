#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Run build-installer-iso.sh end to end with lorax, lsinitrd and mkksiso
# replaced by small stand-ins, to test what the real build does around them:
# the runtime template (rendered with Mako and its runcmd lines run, as lorax
# does), the stage 2 checks, the preview credential's mode and leak checks,
# and the sidecars. It needs no network, a few MB of disk and a minute.
#
#   test-build-credential.sh RPMS_DIR RELEASE_KEY
#
# As root on the build host, from an Atlas checkout. Uses the atlas-iso tools
# image, which must already exist.

set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
rpms=$(realpath "${1:?RPMS_DIR}")
release_key=$(realpath "${2:?RELEASE_KEY}")
t=$(mktemp -d)
trap 'rm -rf "$t"' EXIT
passed=0 failed=0
ok() { printf 'ok   %s\n' "$1"; passed=$((passed + 1)); }
bad() { printf 'FAIL %s\n' "$1"; failed=$((failed + 1)); }

mkdir -p "$t/bin" "$t/stubs"

# podman: skip the image build; give `run` the stand-ins first on PATH.
cat > "$t/bin/podman" <<EOF
#!/usr/bin/env bash
case "\$1" in
  build) exit 0 ;;
  run) shift; exec /usr/bin/podman run --security-opt label=disable --volume $t/stubs:/stubs:ro --env LEAK="\${LEAK:-}" \
         --env PATH=/stubs:/usr/local/bin:/usr/bin:/usr/sbin "\$@" ;;
  *) exec /usr/bin/podman "\$@" ;;
esac
EOF

cat > "$t/stubs/lorax" <<'EOF'
#!/usr/bin/python3
# Stand-in for lorax: render the runtime template the way lorax does and run
# its runcmd lines against a small root, then write the outputs the build uses.
import os, shlex, subprocess, sys
from mako.template import Template
args = sys.argv[1:]
out = args[-1]
variables, templates = {}, []
for i, a in enumerate(args):
    if a == "--add-template-var":
        k, v = args[i + 1].split("=", 1); variables[k] = v
    if a == "--add-template":
        templates.append(args[i + 1])
root = "/tmp/stage2-root"
os.makedirs(root + "/usr/share/luma-installer-atlas", exist_ok=True)
os.makedirs(root + "/usr/share/anaconda", exist_ok=True)
os.makedirs(root + "/usr/share/ostree/trusted.gpg.d", exist_ok=True)
open(root + "/usr/share/ostree/trusted.gpg.d/README-gpg", "w").write("fedora\n")
open(root + "/usr/share/luma-installer-atlas/app-collections.json", "w").write("{}\n")
for template in templates:
    text = Template(filename=template).render(root=root, **variables)
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("runcmd "):
            subprocess.run(shlex.split(line[len("runcmd "):]), check=True)
leak = os.environ.get("LEAK", "")
secret = variables.get("luma_atlas_preview_credential", "none")
if leak == "stage2" and secret != "none":
    os.makedirs(root + "/usr/share/doc", exist_ok=True)
    subprocess.run(["cp", secret, root + "/usr/share/doc/leak.txt"], check=True)
os.makedirs(out + "/images/pxeboot", exist_ok=True)
open(out + "/images/pxeboot/initrd.img", "w").write("initrd\n")
subprocess.run(["mksquashfs", root, out + "/images/install.img", "-noappend", "-quiet", "-no-progress"], check=True,
               stdout=subprocess.DEVNULL)
os.makedirs("/tmp/iso-root/images", exist_ok=True)
subprocess.run(["cp", out + "/images/install.img", "/tmp/iso-root/images/install.img"], check=True)
if leak == "isoroot" and secret != "none":
    subprocess.run(["cp", secret, "/tmp/iso-root/leak.cfg"], check=True)
subprocess.run(["xorriso", "-as", "mkisofs", "-quiet", "-R", "-V", "STUB", "-o", out + "/images/boot.iso", "/tmp/iso-root"],
               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
with open("/w/logs/program.log", "w") as log:
    log.write("stub lorax\n")
    if leak == "log" and secret != "none":
        log.write(open(secret).read() + "\n")
EOF

cat > "$t/stubs/lsinitrd" <<'EOF'
#!/bin/sh
echo usr/lib/systemd/systemd-sysroot-fstab-check
EOF
cat > "$t/stubs/mkksiso" <<'EOF'
#!/bin/sh
# the last two arguments are the input and output ISO
for last; do :; done
eval "input=\${$(($# - 1))}"
cp "$input" "$last"
EOF
chmod +x "$t/bin/podman" "$t/stubs/"*
# Never let the real lorax run if the stand-ins are not found first.
/usr/bin/podman run --rm --security-opt label=disable --volume "$t/stubs:/stubs:ro" \
    --env PATH=/stubs:/usr/local/bin:/usr/bin:/usr/sbin localhost/luma-atlas-iso-tools:44 \
    bash -c '[ "$(command -v lorax)" = /stubs/lorax ] && [ "$(command -v mkksiso)" = /stubs/mkksiso ]' ||
  { echo 'the stand-ins are not first on PATH in the tools container; stopping' >&2; exit 1; }

python3 -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(30)).decode().rstrip("="))' > "$t/credential"
chmod 0600 "$t/credential"

build() {
  # $1 work name, then extra arguments
  local name=$1; shift
  PATH="$t/bin:$PATH" bash "$here/build-installer-iso.sh" --rpms "$rpms" --work "$t/$name" \
      --output "$t/$name.iso" --channel nightly --release-key "$release_key" \
      --test-payload-url https://mirror.example/fedora --test-payload-ref fedora/44/x86_64/silverblue \
      --test-payload-key "$release_key" --volid Luma-Stub-Test "$@" > "$t/$name.out" 2>&1
}
leaks() { grep -rlF -f "$t/credential" "$@" 2>/dev/null | grep -q .; }

# 1. With the credential and nothing leaking: the build passes its checks.
if build clean --preview-credential-file "$t/credential"; then ok 'build with a credential succeeds'; else bad 'build with a credential succeeds'; tail -20 "$t/clean.out"; fi
grep -q 'no copy of the secret elsewhere in the installer image' "$t/clean.out" && ok 'stage 2 leak check ran' || bad 'stage 2 leak check ran'
grep -q 'no copy of the secret in the files on the medium' "$t/clean.out" && ok 'medium leak check ran' || bad 'medium leak check ran'
grep -q 'no copy of the secret in the sidecars, templates or build logs' "$t/clean.out" && ok 'sidecar and log leak check ran' || bad 'sidecar and log leak check ran'
grep -qx 'preview_credential=carried' "$t/clean.iso.media" && ok '.media says carried' || bad '.media says carried'
! leaks "$t/clean.out" "$t/clean.iso.media" "$t/clean.iso.sha256" "$t/clean/logs" "$t/clean/templates" && ok 'no copy in output, sidecars or logs' || bad 'no copy in output, sidecars or logs'
[ ! -e "$t/clean/secret" ] && ok 'work copy of the credential removed' || bad 'work copy of the credential removed'
/usr/bin/podman run --rm --privileged --security-opt label=disable --volume "$t:/t" localhost/luma-atlas-iso-tools:44 bash -c '
  mkdir /iso && mount -o ro,loop /t/clean.iso /iso && unsquashfs -no-xattrs -d /s2 /iso/images/install.img >/dev/null &&
  stat -c "%a %u %g" /s2/usr/share/luma-installer-atlas/preview-credential &&
  cmp /t/credential /s2/usr/share/luma-installer-atlas/preview-credential >/dev/null 2>&1 || cmp <(tr -d "\n" < /t/credential) /s2/usr/share/luma-installer-atlas/preview-credential' > "$t/inspect.out" 2>&1 &&
  grep -qx '600 0 0' "$t/inspect.out" && ok 'installer copy is root:root 0600 and matches' || { bad 'installer copy is root:root 0600 and matches'; cat "$t/inspect.out"; }

# 2. Without the credential: no copy in stage 2, .media says none.
if build none; then ok 'build without a credential succeeds'; else bad 'build without a credential succeeds'; tail -20 "$t/none.out"; fi
grep -qx 'preview_credential=none' "$t/none.iso.media" && ok '.media says none' || bad '.media says none'

# 3-5. A copy anywhere else fails the build and names the file, not the value.
for where in stage2 isoroot log; do
  if LEAK=$where build "leak-$where" --preview-credential-file "$t/credential"; then
    bad "a copy in $where fails the build"
  else
    ok "a copy in $where fails the build"
  fi
  grep -q 'error: the secret appears in' "$t/leak-$where.out" && ok "$where failure names the file" || { bad "$where failure names the file"; tail -5 "$t/leak-$where.out"; }
  ! grep -qF -f "$t/credential" "$t/leak-$where.out" && ok "$where failure does not print it" || bad "$where failure does not print it"
  [ ! -e "$t/leak-$where/secret" ] && ok "$where failure removes the work copy" || bad "$where failure removes the work copy"
done

printf '\n%d passed, %d failed\n' "$passed" "$failed"
[ "$failed" = 0 ]
