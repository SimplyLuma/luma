#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Move an existing Luma machine from a private ref (the Recent channel of
# ADR-015, a private parity ref, or a locally composed base) onto a public
# release channel (ADR-030 section 9). Run as root on the machine itself.
#
#   migrate-to-channel.sh --channel stable|beta|nightly
#                         [--url URL | --preview-credential-file FILE]
#                         [--release-key FILE] [--allow-downgrade] [--apply]
#
# Without --apply (the default) it changes nothing: it verifies the channel
# with the Luma OS Release key in a throwaway repository and prints the plan.
# With --apply it:
#   1. installs the release key at /etc/pki/ostree/luma-release.gpg (only when
#      its fingerprint is the one pinned below) and the `luma` remote in
#      /etc/ostree/remotes.d/luma.conf (world-readable, reading its URL from
#      the root-only mirror list /etc/luma/update-mirrorlist, as Luma images
#      do), keeping a backup of any previous definition of that remote;
#   2. resets base-package overrides (the release is a different base) and
#      re-applies removals of packages the release still contains;
#   3. rebases to luma:luma/1/x86_64/<channel>, uninstalling layered packages
#      the release already contains and keeping the others;
#   4. turns off the other updaters: the Recent timer of the retired update client,
#      rpm-ostreed's automatic policy, and the Fedora OCI Flatpak remote when
#      nothing is installed from it (otherwise it lists what is);
#   5. adds greenboot's GRUB boot counter to /boot/grub2/grub.cfg when the
#      machine's bootloader was installed without it (automatic rollback needs
#      it; bootupd only writes it when a bootloader is installed), and, on a
#      bootupd-generated grub.cfg, the release's hidden-menu piece, so the
#      firmware logo stays on screen from power-on to the boot splash;
#   6. comments out an /etc/fstab entry for / (with composefs, / is an overlay
#      that cannot be remounted, so systemd-remount-fs.service fails every
#      boot; the kernel command line already mounts the root), refusing
#      beforehand when the command line lacks the entry's subvolume;
#   7. records the channel for luma-update and stops. It never reboots.
# A preview credential follows luma-update's contract (docs/os/luma-update.md):
# it lives only in root-only /etc/luma/update-preview-credential and in the
# mirror list.
# User data in /var and /home, Flatpaks and their data are untouched. The
# current deployment stays as the rollback deployment.
#
# Refuses when: not an rpm-ostree machine; a transaction is in progress; the
# machine already follows a luma/1 channel; the channel fails signature
# verification; or the plan would downgrade a package and --allow-downgrade
# was not given.

set -euo pipefail

RELEASE_KEY_FINGERPRINT=7D3DAFCE2BCA13A2B68F2761C8CE1A1B51D96CE2
COLLECTION_ID=org.projectluma.OS
REF_PREFIX=luma/1/x86_64
STABLE_URL=https://dl.simplyluma.com/os/repo
PREVIEW_URL_TEMPLATE=https://dl.simplyluma.com/os/preview/%s/repo
KEY_URL=https://dl.simplyluma.com/os/keys/luma-os-release.gpg
KEY_PATH=/etc/pki/ostree/luma-release.gpg
REMOTE=luma

channel=
url=
credential_file=
release_key=
allow_downgrade=0
apply=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --channel) channel=${2:?}; shift 2 ;;
    --url) url=${2:?}; shift 2 ;;
    --preview-credential-file) credential_file=${2:?}; shift 2 ;;
    --release-key) release_key=${2:?}; shift 2 ;;
    --allow-downgrade) allow_downgrade=1; shift ;;
    --apply) apply=1; shift ;;
    -h|--help) sed -n '3,33p' "$0"; exit 0 ;;
    *) printf 'unknown option: %s (see --help)\n' "$1" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

case "$channel" in
  stable|beta|nightly) ;;
  *) fail '--channel must be stable, beta or nightly' ;;
esac
ref="$REF_PREFIX/$channel"
[ "$(id -u)" -eq 0 ] || fail 'run as root on the machine being migrated'
[ -e /run/ostree-booted ] || fail 'this is not a booted OSTree system'
for tool in rpm-ostree ostree gpg python3 curl; do
  command -v "$tool" >/dev/null 2>&1 || fail "required tool is missing: $tool"
done
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
planner="$here/migration_plan.py"
[ -f "$planner" ] || fail "migration_plan.py must sit beside this script: $planner"

# Where to pull from. A preview credential is read from a root-only file and
# never printed.
display_url=
if [ -n "$credential_file" ]; then
  [ "$channel" != stable ] || fail 'stable is public; do not pass a preview credential'
  [ -r "$credential_file" ] || fail "cannot read $credential_file"
  [ "$(stat -c %a "$credential_file")" = 600 ] || fail "$credential_file must be mode 0600"
  credential=$(tr -d '\r\n' <"$credential_file")
  [[ "$credential" =~ ^[A-Za-z0-9_-]{16,128}$ ]] || fail 'the preview credential has an unexpected format'
  # shellcheck disable=SC2059
  url=$(printf "$PREVIEW_URL_TEMPLATE" "$credential")
  # shellcheck disable=SC2059
  display_url=$(printf "$PREVIEW_URL_TEMPLATE" '<credential>')
fi
if [ -z "$url" ]; then
  [ "$channel" = stable ] || fail "$channel needs --preview-credential-file (or --url)"
  url=$STABLE_URL
fi
display_url=${display_url:-$url}
case "$url" in
  https://*) ;;
  http://127.0.0.1:*|http://localhost:*) say "note: plain HTTP to a local test server; signatures are still required" ;;
  *) fail 'the channel URL must use HTTPS' ;;
esac

status_json=$(mktemp)
work=$(mktemp -d)
trap 'rm -rf "$status_json" "$work"' EXIT
rpm-ostree status --json >"$status_json"
python3 - "$status_json" "$ref" <<'PY' || exit 1
import json, sys
status = json.load(open(sys.argv[1]))
if status.get("transaction"):
    sys.exit("error: an rpm-ostree transaction is in progress; try again when it finishes")
booted = [d for d in status["deployments"] if d.get("booted")][0]
origin = booted.get("origin") or ""
if origin.endswith(":" + sys.argv[2]) or ":luma/1/" in origin:
    sys.exit(f"error: this machine already follows {origin}")
if any(d.get("staged") for d in status["deployments"]):
    print("note: a staged deployment exists; the migration builds on top of it")
PY

# Trust: the pinned release key, from a file or from Luma's key path.
if [ -z "$release_key" ]; then
  release_key="$work/luma-os-release.gpg"
  curl --fail --silent --show-error --proto '=https' --max-time 60 -o "$release_key" "$KEY_URL" ||
    fail "cannot download the release key from $KEY_URL; pass --release-key"
fi
install -d -m 0700 "$work/gnupg"
fingerprint=$(gpg --batch --homedir "$work/gnupg" --with-colons --show-keys "$release_key" 2>/dev/null |
  awk -F: '$1 == "fpr" { print $10; exit }')
[ "$fingerprint" = "$RELEASE_KEY_FINGERPRINT" ] ||
  fail "release key fingerprint ${fingerprint:-unknown} is not the pinned Luma OS Release key"
gpg --batch --homedir "$work/gnupg" --dearmor <"$release_key" >"$work/key.gpg" 2>/dev/null ||
  cp "$release_key" "$work/key.gpg"

# Verify the channel as a fresh client before touching anything.
ostree init --repo="$work/repo" --mode=bare-user >/dev/null
ostree remote add --repo="$work/repo" --gpg-import="$work/key.gpg" \
  --set=gpg-verify=true --set=gpg-verify-summary=true \
  --collection-id="$COLLECTION_ID" check "$url" "$ref" >/dev/null
ostree remote summary --repo="$work/repo" check >/dev/null 2>&1 ||
  fail "the summary at $display_url is missing or not signed by the Luma OS Release key"
ostree pull --repo="$work/repo" --commit-metadata-only check "$ref" >/dev/null 2>&1 ||
  fail "$ref at $display_url is missing or not signed by the Luma OS Release key"
target=$(ostree rev-parse --repo="$work/repo" "check:$ref")
target_version=$(ostree show --repo="$work/repo" --print-metadata-key=version "$target" 2>/dev/null | tr -d "'")
ostree show --repo="$work/repo" --print-metadata-key=rpmostree.rpmdb.pkglist "$target" >"$work/pkglist" 2>/dev/null ||
  fail "$target carries no package list; cannot plan the migration safely"
python3 "$planner" --status "$status_json" --target-pkglist "$work/pkglist" >"$work/plan.json"

read_plan() { python3 -c 'import json,sys; v=json.load(open(sys.argv[1]))[sys.argv[2]]; print(json.dumps(v) if isinstance(v,(dict,bool)) else "\n".join(v if isinstance(v,list) and all(isinstance(x,str) for x in v) else [json.dumps(x) for x in v]))' "$work/plan.json" "$1"; }
mapfile -t uninstall < <(read_plan uninstall)
mapfile -t keep < <(read_plan keep_layers)
mapfile -t reapply < <(read_plan reapply_removals)
mapfile -t downgrades < <(read_plan downgrades)
reset_overrides=$(read_plan reset_overrides)
[ "${#uninstall[@]}" -eq 1 ] && [ -z "${uninstall[0]}" ] && uninstall=()
[ "${#keep[@]}" -eq 1 ] && [ -z "${keep[0]}" ] && keep=()
[ "${#reapply[@]}" -eq 1 ] && [ -z "${reapply[0]}" ] && reapply=()
[ "${#downgrades[@]}" -eq 1 ] && [ -z "${downgrades[0]}" ] && downgrades=()

fedora_flatpak_refs=$(flatpak list --system --columns=application,origin 2>/dev/null | awk '$2 == "fedora" || $2 == "fedora-testing" { print $1 }' || true)
fedora_flatpak_remote=$(flatpak remotes --system --columns=name 2>/dev/null | grep -Ex 'fedora|fedora-testing' || true)
existing_remote_file=$(grep -lE "^\[remote \"$REMOTE\"\]" /etc/ostree/remotes.d/*.conf 2>/dev/null | head -n 1 || true)
# An fstab entry for / (Anaconda writes one) breaks systemd-remount-fs.service
# on composefs systems; the Fedora Atomic workaround is to comment it out once
# the kernel command line carries its mount options.
fstab_root=$(awk '$1 !~ /^#/ && $2 == "/" { print; exit }' /etc/fstab 2>/dev/null || true)
# Client-side initramfs regeneration (enabled by the legacy provisioning) runs
# dracut on every transaction; the release carries its own initramfs.
local_initramfs=$(python3 -c 'import json,sys; d=[x for x in json.load(open(sys.argv[1]))["deployments"] if x.get("booted")][0]; print("yes" if d.get("regenerate-initramfs") else "")' "$status_json")
if [ -n "$fstab_root" ]; then
  fstab_subvol=$(awk '{ n = split($4, o, ","); for (i = 1; i <= n; i++) if (o[i] ~ /^subvol=/) print substr(o[i], 8) }' <<<"$fstab_root")
  grep -Eq '(^| )root=' /proc/cmdline ||
    fail 'the kernel command line has no root= argument; the /etc/fstab root entry cannot be retired'
  if [ -n "$fstab_subvol" ] && ! grep -Eq "(^| )rootflags=([^ ]*,)?subvol=/?${fstab_subvol#/}(,| |$)" /proc/cmdline; then
    fail "the /etc/fstab root entry mounts subvolume $fstab_subvol but the kernel command line does not; add rootflags=subvol=$fstab_subvol with rpm-ostree kargs first"
  fi
fi
existing_repo_remote=$(ostree remote list --repo=/ostree/repo 2>/dev/null | grep -Fx "$REMOTE" || true)

say "Luma channel migration plan"
say "  from:     $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["origin"])' "$work/plan.json")"
say "  to:       $REMOTE:$ref at $display_url"
say "  release:  ${target_version:-unknown} ($target), signature verified with $RELEASE_KEY_FINGERPRINT"
say "  overrides: $([ "$reset_overrides" = true ] && echo 'reset all (the release is a new base)' || echo none)"
for name in "${reapply[@]}"; do say "  re-remove: $name (the release still contains it)"; done
for pkg in "${uninstall[@]}"; do say "  drop layer: $pkg (the release contains it)"; done
for pkg in "${keep[@]}"; do say "  keep layer: $pkg"; done
for entry in "${downgrades[@]}"; do say "  DOWNGRADE: $entry"; done
[ -z "$existing_remote_file" ] || say "  replace remote definition: $existing_remote_file (backup kept)"
[ -z "$existing_repo_remote" ] || say "  remove remote '$REMOTE' from /ostree/repo/config (backup kept)"
systemctl is-enabled --quiet luma-recent-update.timer 2>/dev/null && say '  disable: luma-recent-update.timer'
if [ -n "$fedora_flatpak_remote" ]; then
  if [ -n "$fedora_flatpak_refs" ]; then
    say "  keep Fedora Flatpak remote (installed from it: $(printf '%s ' $fedora_flatpak_refs))"
  else
    say "  remove Fedora Flatpak remote(s): $(printf '%s ' $fedora_flatpak_remote)"
  fi
fi
if ! grep -q boot_counter /boot/grub2/grub.cfg 2>/dev/null; then
  say '  add greenboot boot counting to /boot/grub2/grub.cfg (backup kept)'
fi
# bootupd's static preamble shows GRUB's menu for a second on every boot; the
# release hides it with its own helper (luma-boot-theme), which touches only
# bootupd's file and leaves an administrator's menu timing alone.
hidden_menu=
if grep -q '^# Generated by bootupd' /boot/grub2/grub.cfg 2>/dev/null &&
   ! grep -q '^### BEGIN 09_luma_hidden_menu.cfg ###$' /boot/grub2/grub.cfg 2>/dev/null; then
  hidden_menu=yes
  say '  hide the GRUB menu in /boot/grub2/grub.cfg unless its timing was customised; Esc, F8 or Shift still open it (backup kept)'
fi
[ -z "$fstab_root" ] || say '  comment out the / entry in /etc/fstab (composefs root; the kernel command line mounts it; backup kept)'
[ -z "$local_initramfs" ] || say '  disable local initramfs regeneration (the release carries its initramfs)'
say '  kernel arguments: append what the release declares in /usr/lib/bootc/kargs.d and the machine lacks'
say '  reboot:   never; restart when ready, `rpm-ostree rollback` returns to today'"'"'s system'

if [ "${#downgrades[@]}" -gt 0 ] && [ "$allow_downgrade" -ne 1 ]; then
  fail 'the plan downgrades packages; review them and pass --allow-downgrade to accept'
fi
if [ "$apply" -ne 1 ]; then
  say 'dry run: nothing changed (pass --apply to migrate)'
  exit 0
fi

log="/var/log/luma-migrate-to-channel-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$log") 2>&1
backup="/var/lib/luma/migration-backup-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$backup"
cp -a /etc/ostree/remotes.d "$backup/" 2>/dev/null || true
cp -a /ostree/repo/config "$backup/ostree-repo-config"
cp -a /etc/rpm-ostreed.conf "$backup/" 2>/dev/null || true
cp "$status_json" "$backup/rpm-ostree-status.json"
cp "$work/plan.json" "$backup/plan.json"

install -D -m 0644 "$work/key.gpg" "$KEY_PATH"
if [ -n "$existing_repo_remote" ]; then
  ostree remote delete --repo=/ostree/repo "$REMOTE"
fi
if [ -n "$existing_remote_file" ] && [ "$existing_remote_file" != "/etc/ostree/remotes.d/$REMOTE.conf" ]; then
  mv "$existing_remote_file" "$backup/"
fi
umask 022
install -d -m 0755 /etc/luma
cp -a /etc/luma/update-mirrorlist /etc/luma/update-preview-mirrorlist /etc/luma/update-preview-credential "$backup/" 2>/dev/null || true
umask 077
# The remote reads its URL from the root-only mirror list, the layout Luma
# images ship (docs/os/luma-update.md): the public repository for stable, the
# credential path for a preview channel. The remote file never holds a URL.
printf '%s\n' "$url" >/etc/luma/update-mirrorlist.new
mv /etc/luma/update-mirrorlist.new /etc/luma/update-mirrorlist
rm -f /etc/luma/update-preview-mirrorlist
if [ "$channel" != stable ]; then
  # A preview URL carries a credential: it goes only into root-only files,
  # whether it came from --preview-credential-file or --url.
  if [ -z "${credential:-}" ]; then
    credential=$(python3 -c 'import re, sys; m = re.search(r"/preview/([A-Za-z0-9_-]{16,256})/", sys.argv[1]); print(m.group(1) if m else "")' "$url")
  fi
  python3 -c 'import json, sys, time; print(json.dumps({"credential": sys.argv[1], "channel": sys.argv[2], "channels": [sys.argv[2]], "issued_at": int(time.time())}))' \
    "$credential" "$channel" >/etc/luma/update-preview-credential.new
  mv /etc/luma/update-preview-credential.new /etc/luma/update-preview-credential
else
  rm -f /etc/luma/update-preview-credential
fi
umask 022
{
  printf '[remote "%s"]\n' "$REMOTE"
  printf 'url=mirrorlist=file:///etc/luma/update-mirrorlist\n' 
  printf 'gpg-verify=true\ngpg-verify-summary=true\n'
  printf 'gpgkeypath=%s\ncollection-id=%s\n' "$KEY_PATH" "$COLLECTION_ID"
} >"/etc/ostree/remotes.d/$REMOTE.conf.new"
# World-readable: libostree parses every remote file whenever the sysroot is
# loaded; the credential, if any, is only in the root-only mirror list.
chmod 0644 "/etc/ostree/remotes.d/$REMOTE.conf.new"
mv "/etc/ostree/remotes.d/$REMOTE.conf.new" "/etc/ostree/remotes.d/$REMOTE.conf"
restorecon -F "$KEY_PATH" "/etc/ostree/remotes.d/$REMOTE.conf" /etc/luma/update-mirrorlist 2>/dev/null || true

if [ "$reset_overrides" = true ]; then
  rpm-ostree override reset --all
fi
if [ -n "$local_initramfs" ]; then
  rpm-ostree initramfs --disable
fi
rebase_args=()
for pkg in "${uninstall[@]}"; do rebase_args+=("--uninstall=$pkg"); done
# The exact commit that was verified and planned, not whatever the ref says now.
rpm-ostree rebase "$REMOTE:$ref" "$target" "${rebase_args[@]}"
if [ "${#reapply[@]}" -gt 0 ]; then
  rpm-ostree override remove "${reapply[@]}"
fi
# Kernel arguments the release declares for bootable-container installs
# (/usr/lib/bootc/kargs.d). Whether a rebase between OSTree refs applies them
# is not relied on: appending only what is missing is harmless either way.
mapfile -t release_kargs < <(
  ostree ls --repo=/ostree/repo "$target" /usr/lib/bootc/kargs.d 2>/dev/null |
    awk '$1 ~ /^-/ && $NF ~ /\.toml$/ { print $NF }' | LC_ALL=C sort |
    while read -r toml; do
      ostree cat --repo=/ostree/repo "$target" "$toml" | python3 -c '
import sys, tomllib
data = tomllib.loads(sys.stdin.read())
arches = data.get("match-architectures")
if not arches or "x86_64" in arches:
    for karg in data.get("kargs", []):
        print(karg)'
    done)
if [ "${#release_kargs[@]}" -gt 0 ]; then
  kargs_args=()
  for karg in "${release_kargs[@]}"; do kargs_args+=("--append-if-missing=$karg"); done
  rpm-ostree kargs "${kargs_args[@]}"
fi

if systemctl is-enabled --quiet luma-recent-update.timer 2>/dev/null; then
  systemctl disable --now luma-recent-update.timer
fi
if grep -Eq '^AutomaticUpdatePolicy=(check|stage|apply)' /etc/rpm-ostreed.conf 2>/dev/null; then
  sed -i 's/^AutomaticUpdatePolicy=.*/AutomaticUpdatePolicy=none/' /etc/rpm-ostreed.conf
  systemctl disable --now rpm-ostreed-automatic.timer 2>/dev/null || true
fi
if [ -n "$fedora_flatpak_remote" ] && [ -z "$fedora_flatpak_refs" ]; then
  for name in $fedora_flatpak_remote; do flatpak remote-delete --system "$name"; done
fi
# greenboot's boot counter, from the release being deployed, inserted before
# the configuration's BLS menu so GRUB counts failed boots of the new system.
if ! grep -q boot_counter /boot/grub2/grub.cfg 2>/dev/null; then
  snippet=$(ostree cat --repo=/ostree/repo "$target" /usr/lib/bootupd/grub2-static/configs.d/08_greenboot.cfg)
  grep -q boot_counter <<<"$snippet" || fail "release $target carries no greenboot GRUB counter"
  cp -a /boot/grub2/grub.cfg "$backup/grub.cfg"
  python3 - /boot/grub2/grub.cfg "$snippet" <<'PY'
import os, re, sys
path, snippet = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
match = re.search(r"(?m)^[ \t]*(blscfg|source \$prefix/luma\.cfg)[ \t]*$", text)
if not match:
    sys.exit("error: /boot/grub2/grub.cfg has no BLS menu line to put the boot counter before")
new = text[:match.start()] + "# greenboot boot counting (added by migrate-to-channel.sh)\n" + snippet.rstrip("\n") + "\n" + text[match.start():]
tmp = path + ".luma-migrate"
open(tmp, "w", encoding="utf-8").write(new)
os.chmod(tmp, os.stat(path).st_mode & 0o7777)
os.replace(tmp, path)
PY
  grep -q boot_counter /boot/grub2/grub.cfg || fail 'boot counter was not added to grub.cfg'
fi
# The hidden menu, with the helper and the piece from the release being
# deployed: the same code that keeps it current on every later boot.
if [ -n "$hidden_menu" ]; then
  helper_dir="$work/hidden-menu"
  install -d -m 0700 "$helper_dir"
  if ostree cat --repo=/ostree/repo "$target" /usr/libexec/luma-boot-hidden-menu >"$helper_dir/luma-boot-hidden-menu" 2>/dev/null &&
     ostree cat --repo=/ostree/repo "$target" /usr/lib/bootupd/grub2-static/configs.d/09_luma_hidden_menu.cfg >"$helper_dir/09_luma_hidden_menu.cfg" 2>/dev/null; then
    [ -e "$backup/grub.cfg" ] || cp -a /boot/grub2/grub.cfg "$backup/grub.cfg"
    python3 -sP "$helper_dir/luma-boot-hidden-menu" --source "$helper_dir/09_luma_hidden_menu.cfg" ||
      fail 'the hidden GRUB menu could not be added to grub.cfg'
  else
    say "note: release $target carries no hidden GRUB menu; grub.cfg keeps its menu"
  fi
fi

if [ -n "$fstab_root" ]; then
  cp -a /etc/fstab "$backup/fstab"
  python3 - /etc/fstab <<'PY2'
import os, sys
path = sys.argv[1]
out = []
done = False
for line in open(path, encoding="utf-8").read().splitlines(keepends=True):
    fields = line.split()
    if not done and len(fields) >= 2 and not fields[0].startswith("#") and fields[1] == "/":
        out.append("# Retired by migrate-to-channel.sh: / is composefs and is mounted from the kernel command line.\n")
        out.append("# " + line)
        done = True
    else:
        out.append(line)
tmp = path + ".luma-migrate"
open(tmp, "w", encoding="utf-8").write("".join(out))
os.chmod(tmp, os.stat(path).st_mode & 0o7777)
os.replace(tmp, path)
PY2
  restorecon -F /etc/fstab 2>/dev/null || true
  systemctl daemon-reload
fi

install -d -m 0755 /etc/luma
printf 'channel=%s\n' "$channel" >/etc/luma/update-channel.conf

rpm-ostree status
say "Migration staged: restart to boot Luma ${target_version:-} on $channel."
say "Backup of the previous remote and plan: $backup"
say "To undo before restarting: rpm-ostree cleanup -p (and restore $backup/remotes.d)."
say "To undo after restarting: rpm-ostree rollback, then restart."
