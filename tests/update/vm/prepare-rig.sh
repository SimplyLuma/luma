#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Run as root on a disposable Fedora Silverblue 44 VM that already has the
# luma-update RPM and greenboot layered and booted. Creates a signed local
# OSTree channel from the booted tree, a graph signing key, a loopback
# CDN/Hub stand-in, and points the agent at them. Never run on a real machine.
set -euo pipefail
rig=/var/lib/luma-update-test
here=$(cd "$(dirname "$0")" && pwd)
[ -e /run/ostree-booted ] || { echo "not an OSTree system" >&2; exit 1; }
grep -q '^VARIANT_ID=silverblue' /usr/lib/os-release || { echo "expected a Silverblue test VM" >&2; exit 1; }

install -d -m 0700 "$rig" "$rig/gnupg" "$rig/evidence"
install -d -m 0755 "$rig/repo" "$rig/graph" "$rig/overlays"
install -m 0755 "$here/test-server.py" "$rig/test-server.py"
install -m 0755 "$here/make-release.sh" "$rig/make-release.sh"
install -m 0755 "$here/publish-graph.py" "$rig/publish-graph.py"

# OpenPGP key standing in for the Luma OS Release key.
export GNUPGHOME=$rig/gnupg
if ! gpg --list-secret-keys luma-update-rig >/dev/null 2>&1; then
  gpg --batch --pinentry-mode loopback --passphrase '' \
      --quick-generate-key 'Luma update rig (test only) <luma-update-rig@invalid>' ed25519 sign never
fi
fpr=$(gpg --with-colons --list-secret-keys luma-update-rig | awk -F: '/^fpr:/{print $10; exit}')
echo "$fpr" > "$rig/gpg-fingerprint"
install -d /etc/pki/ostree
gpg --export "$fpr" > /etc/pki/ostree/luma-release.gpg

# Graph signing key (minisign format) standing in for the update-graph key.
if [ ! -f "$rig/minisign.secret" ]; then
  head -c 32 /dev/urandom > "$rig/minisign.secret"
  chmod 0600 "$rig/minisign.secret"
fi
install -d /etc/luma/update-graph-keys.d
PYTHONPATH=/usr/lib/python3.14/site-packages python3 - "$rig" <<'PY'
import sys
from pathlib import Path
from luma_update import minisign
rig = Path(sys.argv[1])
public, _ = minisign.sign_for_tests(rig.joinpath("minisign.secret").read_bytes(), b"LUMARIG1", b"", "")
Path("/etc/luma/update-graph-keys.d/luma-update-rig.pub").write_text(public)
PY

if [ ! -f "$rig/repo/config" ]; then
  ostree --repo="$rig/repo" init --mode=archive
fi

# The loopback CDN and Hub.
cat > /etc/systemd/system/luma-update-test-server.service <<UNIT
[Unit]
Description=Loopback Luma CDN and Hub stand-in (update agent VM rig only)
[Service]
ExecStart=/usr/bin/python3 $rig/test-server.py $rig 8471
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now luma-update-test-server.service
systemctl restart luma-update-test-server.service

cat > /etc/ostree/remotes.d/luma.conf <<CONF
[remote "luma"]
url=http://127.0.0.1:8471/os/repo
gpg-verify=true
gpg-verify-summary=true
gpgkeypath=/etc/pki/ostree/luma-release.gpg
CONF

cat > /etc/luma/update.conf <<CONF
# VM rig only: loopback CDN and Hub over plain HTTP. Never in an image.
[update]
allow_insecure_urls = true
graph_url = http://127.0.0.1:8471/os/graph/{channel}.json
stable_repo_url = http://127.0.0.1:8471/os/repo
preview_repo_url = http://127.0.0.1:8471/os/preview/{credential}/repo
events_url = http://127.0.0.1:8471/api/updates/events
preview_credentials_url = http://127.0.0.1:8471/api/updates/preview-credentials
minimum_check_spacing_seconds = 0
CONF
# greenboot's GRUB boot counter. bootupd writes /boot/grub2/grub.cfg from
# /usr/lib/bootupd/grub2-static/configs.d only when the bootloader is
# installed, so a machine installed before greenboot was added lacks the
# counter and greenboot's retry count would never decrease. A fresh install
# of an image that already contains greenboot has it; this rig emulates that.
if ! grep -q '08_greenboot.cfg' /boot/grub2/grub.cfg; then
  cp /boot/grub2/grub.cfg "$rig/grub.cfg.before-greenboot"
  python3 - <<'PY'
from pathlib import Path
cfg = Path("/boot/grub2/grub.cfg")
block = Path("/usr/lib/bootupd/grub2-static/configs.d/08_greenboot.cfg").read_text()
text = cfg.read_text()
marker = "### BEGIN 10_blscfg.cfg ###"
assert marker in text
cfg.write_text(text.replace(marker, "### BEGIN 08_greenboot.cfg ###\n" + block + "### END 08_greenboot.cfg ###\n\n" + marker))
PY
fi
echo "rig ready: gpg $fpr"
