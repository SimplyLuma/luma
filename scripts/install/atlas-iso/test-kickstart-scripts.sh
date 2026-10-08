#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Exercise the channel scripts of config/install/atlas/interactive-defaults.ks
# without an installer: the %pre that chooses the payload source, and the
# %post that gives the installed system its remote, key, origin and channel.
# Runs as root inside the atlas-iso tools image (it writes under /run, /mnt and
# /usr/share/luma-installer-atlas of that throwaway container):
#
#   podman run --rm -v "$PWD:/src:ro" localhost/luma-atlas-iso-tools:44 \
#       bash /src/scripts/install/atlas-iso/test-kickstart-scripts.sh

set -euo pipefail

src=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
ks="$src/config/install/atlas/interactive-defaults.ks"
t=$(mktemp -d)
passed=0
failed=0

ok() { printf 'ok   %s\n' "$1"; passed=$((passed + 1)); }
bad() { printf 'FAIL %s\n' "$1"; failed=$((failed + 1)); }
expect() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# Section bodies from the kickstart.
awk '/^%pre /{on=1; next} on && /^%end/{exit} on' "$ks" > "$t/pre.sh"
# Python %post sections in order: 1 the channel, 2 the fstab root entry, 3 kargs.d,
# 4 the first-boot log check for a preview credential.
awk -v dir="$t" '/^%post --nochroot --erroronfail --interpreter=\/usr\/bin\/python3/{n++; on=1; next} on && /^%end/{on=0; next} on {print > (dir "/python-post-" n ".py")}' "$ks"
cp "$t/python-post-1.py" "$t/post.py" 2>/dev/null || :
cp "$t/python-post-2.py" "$t/fstab.py" 2>/dev/null || :
cp "$t/python-post-3.py" "$t/kargs.py" 2>/dev/null || :
cp "$t/python-post-4.py" "$t/scrub.py" 2>/dev/null || :
grep -q 'atlas-scrub-install-logs' "$t/scrub.py" || { echo 'could not find the preview credential log check %post' >&2; exit 1; }
grep -q 'update-channel.conf' "$t/post.py" && grep -q 'Retired by the Luma installer' "$t/fstab.py" &&
    grep -q 'bootc/kargs.d' "$t/kargs.py" ||
    { echo 'could not find the channel, fstab and kargs.d %post sections' >&2; exit 1; }
[ -s "$t/pre.sh" ] || { echo 'could not find the %pre section' >&2; exit 1; }

# A throwaway signing key and a tiny signed repository shaped like a channel.
export GNUPGHOME="$t/gnupg"
install -d -m 0700 "$GNUPGHOME"
gpg --batch --passphrase '' --quick-gen-key 'Kickstart script test key' ed25519 sign 1d >/dev/null 2>&1
key_id=$(gpg --with-colons --list-keys | awk -F: '/^fpr:/ { print $10; exit }')
gpg --export > "$t/release.gpg"
mkdir -p "$t/tree/usr/lib" && echo 'NAME="Luma"' > "$t/tree/usr/lib/os-release"

media_env() {
    # $1 channel, $2 test payload url, $3 test payload ref, $4 repository URL
    mkdir -p /usr/share/luma-installer-atlas
    rm -f /usr/share/luma-installer-atlas/preview-credential
    cp "$t/release.gpg" /usr/share/luma-installer-atlas/luma-release.gpg
    cat > /usr/share/luma-installer-atlas/media.env <<EOF
# test
channel=$1
channels=stable beta nightly
remote=luma
https_url=${4:-https://dl.simplyluma.com/os/repo}
mirrorlist=/etc/luma/update-mirrorlist
preview_channels=beta nightly
preview_url_prefix=https://dl.simplyluma.com/os/preview
preview_record=/etc/luma/update-preview-credential
collection_id=org.projectluma.OS
ref=luma/1/x86_64/$1
stateroot=luma
medium_repo=luma/repo
target_key=/etc/pki/ostree/luma-release.gpg
volid=Luma-Test-Volume-Not-Present
test_payload_url=${2:-}
test_payload_ref=${3:-}
EOF
}

medium_repo() {
    # $1 ref to commit
    rm -rf /run/install/repo && mkdir -p /run/install/repo/luma
    ostree init --repo=/run/install/repo/luma/repo --mode=archive --collection-id=org.projectluma.OS
    # These repositories are kilobytes; do not let a full build disk refuse them.
    ostree config --repo=/run/install/repo/luma/repo set core.min-free-space-percent 0
    ostree commit --repo=/run/install/repo/luma/repo --branch="$1" --tree=dir="$t/tree" --gpg-sign="$key_id" >/dev/null
    ostree summary --repo=/run/install/repo/luma/repo --update --gpg-sign="$key_id"
}

run_pre() { rm -rf /run/luma-atlas; bash "$t/pre.sh" > "$t/pre.out" 2>&1; }

# 1. The medium carries the channel: install from it.
media_env nightly
medium_repo luma/1/x86_64/nightly
run_pre
expect 'pre: medium with the channel ref is used' \
    "grep -qx 'ostreesetup --osname=luma --remote=luma --url=file:///run/install/repo/luma/repo --ref=luma/1/x86_64/nightly' /run/luma-atlas/payload.ks"
expect 'pre: payload.json says medium' "python3 -c 'import json; d=json.load(open(\"/run/luma-atlas/payload.json\")); assert d == {\"channel\": \"nightly\", \"source\": \"medium\", \"url\": \"file:///run/install/repo/luma/repo\", \"ref\": \"luma/1/x86_64/nightly\", \"preview_enrolled\": False}, d'"
expect 'pre: no --nogpg' "! grep -q nogpg /run/luma-atlas/payload.ks"

# 2. The medium carries another channel: download this one.
media_env stable
run_pre
expect 'pre: medium without the channel ref falls back to HTTPS' \
    "grep -qx 'ostreesetup --osname=luma --remote=luma --url=https://dl.simplyluma.com/os/repo --ref=luma/1/x86_64/stable' /run/luma-atlas/payload.ks"
expect 'pre: payload.json says online' "grep -q '\"source\": \"online\"' /run/luma-atlas/payload.json"

# 3. No medium repository at all.
rm -rf /run/install/repo
run_pre
expect 'pre: no medium repository means HTTPS' "grep -q 'url=https://dl.simplyluma.com/os/repo' /run/luma-atlas/payload.ks"

# 4. Test media pull the stand-in.
media_env nightly https://mirror.example/fedora fedora/44/x86_64/silverblue
run_pre
expect 'pre: test media pull the stand-in ref' \
    "grep -qx 'ostreesetup --osname=luma --remote=luma --url=https://mirror.example/fedora --ref=fedora/44/x86_64/silverblue' /run/luma-atlas/payload.ks"

# 5. A non-HTTPS URL is refused.
media_env nightly http://mirror.example/fedora fedora/44/x86_64/silverblue
if run_pre; then bad 'pre: plain HTTP refused'; else ok 'pre: plain HTTP refused'; fi

# The %post, against a fake deployment.
fake_target() {
    # $1 origin refspec, $2 an existing remote file body (optional)
    rm -rf /mnt/sysimage /mnt/sysroot
    mkdir -p /mnt/sysimage/ostree/deploy/luma/deploy /mnt/sysroot/etc/ostree/remotes.d
    ostree init --repo=/mnt/sysimage/ostree/repo --mode=bare >/dev/null
    printf '[origin]\nrefspec=%s\n' "$1" > /mnt/sysimage/ostree/deploy/luma/deploy/0123abcd.0.origin
    if [ -n "${2:-}" ]; then
        printf '%s' "$2" > /mnt/sysroot/etc/ostree/remotes.d/luma-os.conf
    fi
}
run_post() { ANA_INSTALL_PATH=/mnt/sysroot python3 "$t/post.py" > "$t/post.out" 2>&1; }

ml=/mnt/sysroot/etc/luma/update-mirrorlist
mirrorlist_ok() {
    # $1 expected URL
    [ "$(cat $ml)" = "$1" ] && [ "$(stat -c '%a %u %g' $ml)" = '600 0 0' ] &&
        [ "$(tail -c1 $ml | od -An -c | tr -d ' ')" = '\n' ] && [ ! -e $ml.atlas ]
}
no_url_in_remotes() { ! grep -rEq '^[[:space:]]*url[[:space:]]*=[[:space:]]*(https?|file)://' /mnt/sysroot/etc/ostree/remotes.d; }

# 6. No definition in the image: Anaconda recorded the medium URL.
media_env nightly
fake_target luma:luma/1/x86_64/nightly
printf '[remote "luma"]\nurl=file:///run/install/repo/luma/repo\n' > /mnt/sysroot/etc/ostree/remotes.d/luma.conf
if run_post; then ok 'post: Luma install succeeds'; else bad 'post: Luma install succeeds'; cat "$t/post.out"; fi
conf=/mnt/sysroot/etc/ostree/remotes.d/luma.conf
expect 'post: remote reads the mirror list' "grep -qx 'url=mirrorlist=file:///etc/luma/update-mirrorlist' $conf"
expect 'post: no URL in any remote file' no_url_in_remotes
expect 'post: mirror list holds the HTTPS URL, root:root 0600' "mirrorlist_ok https://dl.simplyluma.com/os/repo"
expect 'post: remote file stays world-readable' "[ \$(stat -c %a $conf) = 644 ]"
expect 'post: remote verifies commits' "grep -qx 'gpg-verify=true' $conf"
expect 'post: remote verifies summaries' "grep -qx 'gpg-verify-summary=true' $conf"
expect 'post: remote names the key' "grep -qx 'gpgkeypath=/etc/pki/ostree/luma-release.gpg' $conf"
expect 'post: remote has the collection id' "grep -qx 'collection-id=org.projectluma.OS' $conf"
expect 'post: medium URL is gone' "! grep -rq file:///run /mnt/sysroot/etc/ostree/remotes.d"
expect 'post: key installed 0644' "cmp -s $t/release.gpg /mnt/sysroot/etc/pki/ostree/luma-release.gpg && [ \$(stat -c %a /mnt/sysroot/etc/pki/ostree/luma-release.gpg) = 644 ]"
expect 'post: channel file' "[ \"\$(cat /mnt/sysroot/etc/luma/update-channel.conf)\" = channel=nightly ] && [ \$(stat -c %a /mnt/sysroot/etc/luma/update-channel.conf) = 644 ]"
expect 'post: origin untouched' "grep -qx 'refspec=luma:luma/1/x86_64/nightly' /mnt/sysimage/ostree/deploy/luma/deploy/0123abcd.0.origin"
expect 'post: exactly one luma remote definition' \
    "[ \$(grep -rlF '[remote \"luma\"]' /mnt/sysroot/etc/ostree/remotes.d | wc -l) = 1 ]"

# 7. The image (nightly .7 on) ships the mirror-list layout: its luma.conf is left byte for byte,
#    and the mirror list it ships is replaced with the medium's URL.
image_conf='# Luma operating system updates (ADR-030 section 5).
[remote "luma"]
url=mirrorlist=file:///etc/luma/update-mirrorlist
gpg-verify=true
gpg-verify-summary=true
gpgkeypath=/etc/pki/ostree/luma-release.gpg
collection-id=org.projectluma.OS
'
fake_target luma:luma/1/x86_64/nightly
printf '%s' "$image_conf" > $conf
cp -p $conf "$t/image.conf"
mkdir -p /mnt/sysroot/etc/luma && printf 'https://stale.example/os/repo\n' > $ml && chmod 0644 $ml
media_env nightly "" "" https://dl.simplyluma.com/os/repo
if run_post; then ok 'post: mirror-list image accepted'; else bad 'post: mirror-list image accepted'; cat "$t/post.out"; fi
expect 'post: image luma.conf left byte for byte' "cmp -s $conf \"$t/image.conf\" && [ \$(stat -c %Y $conf) = \$(stat -c %Y \"$t/image.conf\") ]"
expect 'post: image mirror list replaced, root:root 0600' "mirrorlist_ok https://dl.simplyluma.com/os/repo"
expect 'post: says it left the image remote alone' "grep -q 'left as it is' \"$t/post.out\""

# 7a. The media's URL is what goes in, not a constant.
fake_target luma:luma/1/x86_64/beta
printf '%s' "$image_conf" > $conf
media_env beta "" "" https://mirror.example/os/repo
run_post
expect 'post: mirror list carries the media URL' "mirrorlist_ok https://mirror.example/os/repo"

# 7b. An older image with the URL in its remote (in another file, beside another remote):
#     that file gets the mirror-list url, nothing is duplicated, other remotes are kept.
fake_target luma:luma/1/x86_64/stable "$(printf '# image comment\n[remote "flathub-like"]\nurl=https://example.org\n\n[remote "luma"]\nurl=https://dl.simplyluma.com/os/repo\ngpg-verify=false\n')"
media_env stable
if run_post; then ok 'post: URL-layout image accepted'; else bad 'post: URL-layout image accepted'; cat "$t/post.out"; fi
old=/mnt/sysroot/etc/ostree/remotes.d/luma-os.conf
expect 'post: no second luma.conf' "[ ! -e $conf ]"
expect 'post: other remotes and comments in that file kept' "grep -q 'flathub-like' $old && grep -q 'url=https://example.org' $old && grep -q '# image comment' $old"
expect 'post: URL-layout remote now reads the mirror list' "grep -qx 'url=mirrorlist=file:///etc/luma/update-mirrorlist' $old && ! grep -q 'url=https://dl.simplyluma' $old"
expect 'post: URL-layout remote verification restored' "! grep -q 'gpg-verify=false' $old && grep -qx 'gpg-verify-summary=true' $old"
expect 'post: URL-layout mirror list written' "mirrorlist_ok https://dl.simplyluma.com/os/repo"

# 7c. A mirror-list image that turns verification off is refused, not repaired silently.
fake_target luma:luma/1/x86_64/stable
printf '[remote "luma"]\nurl=mirrorlist=file:///etc/luma/update-mirrorlist\ngpg-verify=false\n' > $conf
media_env stable
if run_post; then bad 'post: image without verification fails the install'; else ok 'post: image without verification fails the install'; fi

# 7d. A mirror list path outside /etc/luma is refused.
fake_target luma:luma/1/x86_64/stable
sed -i 's#^mirrorlist=.*#mirrorlist=/etc/../root/list#' /usr/share/luma-installer-atlas/media.env
if run_post; then bad 'post: bad mirror list path refused'; else ok 'post: bad mirror list path refused'; fi

# 8. A Luma medium whose origin is wrong fails the install.
fake_target luma:luma/1/x86_64/beta
media_env stable
if run_post; then bad 'post: wrong origin fails a Luma install'; else ok 'post: wrong origin fails a Luma install'; fi

# 9. Test media rewrite the stand-in origin to the channel.
fake_target luma:fedora/44/x86_64/silverblue
media_env nightly https://mirror.example/fedora fedora/44/x86_64/silverblue
if run_post; then ok 'post: test media accepted'; else bad 'post: test media accepted'; cat "$t/post.out"; fi
expect 'post: test origin names the channel' "grep -qx 'refspec=luma:luma/1/x86_64/nightly' /mnt/sysimage/ostree/deploy/luma/deploy/0123abcd.0.origin"

# Staff media with the batch's preview credential (interim, until Hub enrolls
# devices one by one). The credential is never printed and lands only in the
# two root-only files.
cred=Zm9vYmFyLWJhdGNoLWNyZWRlbnRpYWwtdGVzdA_x-9
printf '%s' "$cred" > "$t/cred"
carry_credential() { (umask 077; printf '%s\n' "$1" > /usr/share/luma-installer-atlas/preview-credential); }
not_printed() { ! grep -qF -f "$t/cred" "$t/post.out" "$t/pre.out" 2>/dev/null; }
world_readable_clean() {
    ! find /mnt/sysroot /mnt/sysimage -type f -perm -o=r -print0 2>/dev/null | xargs -0 -r grep -lF -f "$t/cred" | grep -q .
}

# 9a. %pre records that the medium enrolls the computer, never the credential.
media_env nightly
carry_credential "$cred"
run_pre
expect 'credential: payload.json says enrolled' "python3 -c 'import json; assert json.load(open(\"/run/luma-atlas/payload.json\"))[\"preview_enrolled\"] is True'"
expect 'credential: payload.json and the %pre log do not hold it' "! grep -qF -f \"$t/cred\" /run/luma-atlas/payload.json /run/luma-atlas/payload.ks \"$t/pre.out\""
media_env nightly
run_pre
expect 'credential: payload.json says not enrolled without it' "python3 -c 'import json; assert json.load(open(\"/run/luma-atlas/payload.json\"))[\"preview_enrolled\"] is False'"

# 9b. A nightly medium with the credential.
media_env nightly
carry_credential "$cred"
fake_target luma:luma/1/x86_64/nightly
printf '%s' "$image_conf" > $conf
cp -p $conf "$t/image.conf"
if run_post; then ok 'credential: nightly install succeeds'; else bad 'credential: nightly install succeeds'; fi
record=/mnt/sysroot/etc/luma/update-preview-credential
expect 'credential: mirror list holds the preview URL, root:root 0600' "mirrorlist_ok https://dl.simplyluma.com/os/preview/$cred/repo"
expect 'credential: enrollment record root:root 0600' "[ \"\$(stat -c '%a %u %g' $record)\" = '600 0 0' ] && [ ! -e $record.atlas ]"
expect 'credential: enrollment record as EnrollPreview writes it' "python3 - $record \"$cred\" <<'PYCHECK'
import json, sys, time
record = json.load(open(sys.argv[1]))
assert record['credential'] == sys.argv[2], 'credential'
assert record['channel'] == 'nightly' and record['channels'] == ['nightly'], record
assert isinstance(record['issued_at'], int) and abs(record['issued_at'] - time.time()) < 300, record
assert record['source'] == 'staff-media', record
assert set(record) == {'credential', 'channel', 'channels', 'issued_at', 'source'}, record
PYCHECK"
expect 'credential: luma.conf still byte for byte' "cmp -s $conf \"$t/image.conf\""
expect 'credential: never printed' not_printed
expect 'credential: logged as installed' "grep -qx 'atlas: preview credential installed' \"$t/post.out\" && grep -q 'from https://dl.simplyluma.com/os/preview/<preview credential>/repo' \"$t/post.out\""
expect 'credential: in no world-readable file on the target' world_readable_clean

# 9c. The same credential on a stable medium fails, without printing it.
media_env stable
carry_credential "$cred"
fake_target luma:luma/1/x86_64/stable
printf '%s' "$image_conf" > $conf
if run_post; then bad 'credential: refused on a stable medium'; else ok 'credential: refused on a stable medium'; fi
expect 'credential: stable refusal does not print it' not_printed
expect 'credential: stable refusal writes no record' "[ ! -e /mnt/sysroot/etc/luma/update-preview-credential ]"

# 9d. An unusable credential fails, without printing it.
media_env nightly
carry_credential 'short!bad'
printf '%s' 'short!bad' > "$t/cred"
fake_target luma:luma/1/x86_64/nightly
printf '%s' "$image_conf" > $conf
if run_post; then bad 'credential: unusable credential refused'; else ok 'credential: unusable credential refused'; fi
expect 'credential: unusable credential not printed' not_printed
printf '%s' "$cred" > "$t/cred"

# 9e. Without the credential: the public URL and no record.
media_env nightly
fake_target luma:luma/1/x86_64/nightly
printf '%s' "$image_conf" > $conf
run_post
expect 'credential: without it, the public URL and no record' "mirrorlist_ok https://dl.simplyluma.com/os/repo && [ ! -e /mnt/sysroot/etc/luma/update-preview-credential ]"

# 9f. The first-boot log check: installed only with the credential, scrubs the
#     copied installation logs and kickstarts, keeps their modes, removes itself.
run_scrub_post() { ANA_INSTALL_PATH=/mnt/sysroot python3 "$t/scrub.py" > "$t/scrub.out" 2>&1; }
media_env nightly
fake_target luma:luma/1/x86_64/nightly
run_scrub_post
expect 'scrub: nothing installed without a credential' "[ ! -e /mnt/sysroot/etc/luma/atlas-scrub-install-logs ] && [ ! -e /mnt/sysroot/etc/systemd/system/luma-atlas-scrub-install-logs.service ]"
carry_credential "$cred"
if run_scrub_post; then ok 'scrub: installed with a credential'; else bad 'scrub: installed with a credential'; cat "$t/scrub.out"; fi
expect 'scrub: script 0700, unit and wants link in place' "[ \$(stat -c %a /mnt/sysroot/etc/luma/atlas-scrub-install-logs) = 700 ] && [ -f /mnt/sysroot/etc/systemd/system/luma-atlas-scrub-install-logs.service ] && [ -L /mnt/sysroot/etc/systemd/system/multi-user.target.wants/luma-atlas-scrub-install-logs.service ]"
expect 'scrub: the %post itself does not print or store it' "! grep -rqF -f \"$t/cred\" \"$t/scrub.out\" /mnt/sysroot/etc/luma/atlas-scrub-install-logs /mnt/sysroot/etc/systemd"
mkdir -p /mnt/sysroot/etc/luma /mnt/sysroot/var/log/anaconda /mnt/sysroot/root
printf '{"credential": "%s", "channel": "nightly", "channels": ["nightly"], "issued_at": 1, "source": "staff-media"}\n' "$cred" > /mnt/sysroot/etc/luma/update-preview-credential
printf 'line one\nmirror https://dl.simplyluma.com/os/preview/%s/repo\n' "$cred" > /mnt/sysroot/var/log/anaconda/journal.log
chmod 0600 /mnt/sysroot/var/log/anaconda/journal.log
printf 'clean log\n' > /mnt/sysroot/var/log/anaconda/program.log
printf '# kickstart %s\n' "$cred" > /mnt/sysroot/root/anaconda-ks.cfg
if LUMA_ATLAS_SCRUB_ROOT=/mnt/sysroot python3 /mnt/sysroot/etc/luma/atlas-scrub-install-logs > "$t/firstboot.out" 2>&1; then ok 'scrub: first boot run succeeds'; else bad 'scrub: first boot run succeeds'; cat "$t/firstboot.out"; fi
expect 'scrub: credential gone from the logs and kickstarts' "! grep -rqF -f \"$t/cred\" /mnt/sysroot/var/log/anaconda /mnt/sysroot/root"
expect 'scrub: marker left in its place' "grep -q '<preview credential removed>' /mnt/sysroot/var/log/anaconda/journal.log && grep -q 'line one' /mnt/sysroot/var/log/anaconda/journal.log"
expect 'scrub: file mode kept' "[ \$(stat -c %a /mnt/sysroot/var/log/anaconda/journal.log) = 600 ]"
expect 'scrub: enrollment record untouched' "grep -qF -f \"$t/cred\" /mnt/sysroot/etc/luma/update-preview-credential"
expect 'scrub: removed itself' "[ ! -e /mnt/sysroot/etc/luma/atlas-scrub-install-logs ] && [ ! -e /mnt/sysroot/etc/systemd/system/luma-atlas-scrub-install-logs.service ] && [ ! -L /mnt/sysroot/etc/systemd/system/multi-user.target.wants/luma-atlas-scrub-install-logs.service ]"
expect 'scrub: its output names files, not the credential' "! grep -qF -f \"$t/cred\" \"$t/firstboot.out\" && grep -q 'var/log/anaconda/journal.log' \"$t/firstboot.out\""
media_env nightly

# 9g. The build's leak check.
check=$(dirname "$0")/check-secret-absent.sh
mkdir -p "$t/leak/a/b"
printf 'nothing here\n' > "$t/leak/a/clean.txt"
printf '\000\001binary %s tail' "$cred" > "$t/leak/a/b/blob.bin"
cp "$t/cred" "$t/leak/a/the-one-place"
if bash "$check" --secret "$t/cred" --exclude "$t/leak/a/the-one-place" "$t/leak" > "$t/leak.out" 2>&1; then bad 'leak check: finds the secret in a binary file'; else ok 'leak check: finds the secret in a binary file'; fi
expect 'leak check: names the file, not the secret' "grep -q 'blob.bin' \"$t/leak.out\" && ! grep -qF -f \"$t/cred\" \"$t/leak.out\" && ! grep -q 'the-one-place' \"$t/leak.out\""
rm "$t/leak/a/b/blob.bin"
if bash "$check" --secret "$t/cred" --exclude "$t/leak/a/the-one-place" "$t/leak" > "$t/leak.out" 2>&1; then ok 'leak check: passes with only the excluded copy'; else bad 'leak check: passes with only the excluded copy'; cat "$t/leak.out"; fi
if bash "$check" --secret "$t/cred" "$t/leak" > /dev/null 2>&1; then bad 'leak check: the copy counts when not excluded'; else ok 'leak check: the copy counts when not excluded'; fi
printf '%s\n\n' "$cred" > "$t/two-lines"
if bash "$check" --secret "$t/two-lines" "$t/leak/a/clean.txt" > /dev/null 2>&1; then bad 'leak check: a secret file with an empty line is refused'; else ok 'leak check: a secret file with an empty line is refused'; fi
: > "$t/empty"
if bash "$check" --secret "$t/empty" "$t/leak/a/clean.txt" > /dev/null 2>&1; then bad 'leak check: an empty secret file is refused'; else ok 'leak check: an empty secret file is refused'; fi

# The fstab %post, against a fake deployment's /etc/fstab and boot entries.
anaconda_fstab() {
    cat > /mnt/sysroot/etc/fstab <<'EOF'
#
# /etc/fstab
# Created by anaconda
#
UUID=1111-root /                       btrfs   subvol=root,compress=zstd:1,ro 0 0
UUID=2222-boot /boot                   ext4    defaults        1 2
UUID=3333-EFI  /boot/efi               vfat    umask=0077,shortname=winnt 0 2
UUID=1111-root /home                   btrfs   subvol=home,compress=zstd:1 0 0
UUID=1111-root /var                    btrfs   subvol=var,compress=zstd:1 0 0
EOF
}
boot_entry() {
    # $1 options line
    mkdir -p /mnt/sysimage/boot/loader/entries
    rm -f /mnt/sysimage/boot/loader/entries/*.conf
    printf 'title Luma\nversion 1\nlinux /ostree/luma-abc/vmlinuz\ninitrd /ostree/luma-abc/initramfs.img\noptions %s\n' "$1" \
        > /mnt/sysimage/boot/loader/entries/ostree-1.conf
}
run_fstab() { ANA_INSTALL_PATH=/mnt/sysroot python3 "$t/fstab.py" > "$t/fstab.out" 2>&1; }
fstab=/mnt/sysroot/etc/fstab

# 10. The / entry is commented out, with the reason, and nothing else changes.
fake_target luma:luma/1/x86_64/nightly
anaconda_fstab
cp $fstab "$t/fstab.before"
boot_entry 'root=UUID=1111-root rootflags=subvol=root rw ostree=/ostree/boot.1/luma/abc/0 rhgb quiet'
if run_fstab; then ok 'fstab: script succeeds'; else bad 'fstab: script succeeds'; cat "$t/fstab.out"; fi
expect 'fstab: no active / entry' "! awk '\$1 !~ /^#/ && \$2 == \"/\"' $fstab | grep -q ."
expect 'fstab: / entry kept as a comment' "grep -qx '# UUID=1111-root /                       btrfs   subvol=root,compress=zstd:1,ro 0 0' $fstab"
expect 'fstab: the reason precedes it' \
    "grep -B1 '^# UUID=1111-root /  ' $fstab | head -n1 | grep -qx '# Retired by the Luma installer: / is composefs and is mounted from the kernel command line.'"
expect 'fstab: /boot, /boot/efi, /home and /var untouched' \
    "( for m in /boot /boot/efi /home /var; do grep -E \"^UUID=[^ ]+ +\$m +\" \"$t/fstab.before\" | grep -qxFf - $fstab || exit 1; done )"
expect 'fstab: only those two lines differ' "[ \$(diff \"$t/fstab.before\" $fstab | grep -c '^[<>]') = 3 ]"

# 11. Running it again changes nothing.
cp $fstab "$t/fstab.once"
run_fstab
expect 'fstab: second run is a no-op' "cmp -s \"$t/fstab.once\" $fstab"

# 12. rootflags carrying other options still counts.
anaconda_fstab
boot_entry 'root=UUID=1111-root rootflags=compress=zstd:1,subvol=root rw'
run_fstab
expect 'fstab: subvol among other rootflags is accepted' "grep -q '^# Retired by the Luma installer' $fstab"

# 13. Boot entries that do not mount the subvolume: the entry stays.
anaconda_fstab
boot_entry 'root=UUID=1111-root rw quiet'
if run_fstab; then ok 'fstab: missing rootflags does not fail the install'; else bad 'fstab: missing rootflags does not fail the install'; fi
expect 'fstab: missing rootflags keeps the / entry' "grep -q '^UUID=1111-root /  ' $fstab && grep -q 'keeping the / entry' \"$t/fstab.out\""

# 14. No root= at all: the entry stays.
anaconda_fstab
boot_entry 'rootflags=subvol=root rw'
run_fstab
expect 'fstab: missing root= keeps the / entry' "grep -q '^UUID=1111-root /  ' $fstab"

# 15. No boot entries: the entry stays.
anaconda_fstab
rm -f /mnt/sysimage/boot/loader/entries/*.conf
run_fstab
expect 'fstab: no boot entries keeps the / entry' "grep -q '^UUID=1111-root /  ' $fstab"

# 16. No / entry: nothing to do.
printf 'UUID=2222-boot /boot ext4 defaults 1 2\n' > $fstab
boot_entry 'root=UUID=1111-root rootflags=subvol=root rw'
if run_fstab; then ok 'fstab: no / entry is fine'; else bad 'fstab: no / entry is fine'; fi
expect 'fstab: no / entry leaves the file alone' "[ \"\$(cat $fstab)\" = 'UUID=2222-boot /boot ext4 defaults 1 2' ]"

# The kargs.d %post, against a fake deployment, boot entry and ostree.
# The fake ostree does what `admin instutil set-kargs --merge --append=K` does
# to the single boot entry, and logs each call; ATLAS_FAKE_OSTREE=noop makes
# it change nothing.
mkdir -p "$t/bin"
cat > "$t/bin/ostree" <<'EOF'
#!/usr/bin/python3
import glob, os, sys
args = sys.argv[1:]
with open(os.environ["ATLAS_FAKE_OSTREE_LOG"], "a") as log:
    log.write(" ".join(args) + "\n")
assert args[:3] == ["admin", "instutil", "set-kargs"], args
assert "--merge" in args and any(a.startswith("--sysroot=") for a in args), args
if os.environ.get("ATLAS_FAKE_OSTREE") == "noop":
    raise SystemExit(0)
sysroot = next(a.split("=", 1)[1] for a in args if a.startswith("--sysroot="))
(entry,) = glob.glob(os.path.join(sysroot, "boot/loader/entries/*.conf"))
lines = open(entry).read().splitlines()
for arg in args:
    if arg.startswith("--append="):
        lines = [line + " " + arg.split("=", 1)[1] if line.startswith("options ") else line for line in lines]
open(entry, "w").write("\n".join(lines) + "\n")
EOF
chmod +x "$t/bin/ostree"
kd=/mnt/sysroot/usr/lib/bootc/kargs.d
other_arch=$([ "$(uname -m)" = aarch64 ] && echo x86_64 || echo aarch64)
kargs_target() {
    rm -rf /mnt/sysimage /mnt/sysroot "$t/ostree.log"
    mkdir -p /mnt/sysroot/usr/lib /mnt/sysimage/boot/loader/entries
    printf 'title Luma\noptions ostree=/ostree/boot.0/luma/abc/0 root=UUID=1111-root vconsole.keymap=us rootflags=subvol=root rhgb quiet rw\n' \
        > /mnt/sysimage/boot/loader/entries/ostree-1.conf
    : > "$t/ostree.log"
}
run_kargs() { ATLAS_FAKE_OSTREE_LOG="$t/ostree.log" PATH="$t/bin:$PATH" ANA_INSTALL_PATH=/mnt/sysroot python3 "$t/kargs.py" > "$t/kargs.out" 2>&1; }
options() { sed -n 's/^options //p' /mnt/sysimage/boot/loader/entries/ostree-1.conf; }
count() { options | tr ' ' '\n' | grep -cx -- "$1"; }

# 17. No kargs.d directory: nothing to do.
kargs_target
if run_kargs; then ok 'kargs: no kargs.d directory is fine'; else bad 'kargs: no kargs.d directory is fine'; cat "$t/kargs.out"; fi
expect 'kargs: no directory, no set-kargs call' "[ ! -s \"$t/ostree.log\" ] && grep -q 'no /usr/lib/bootc/kargs.d' \"$t/kargs.out\""

# 18. An empty kargs.d directory: nothing to do.
kargs_target; mkdir -p $kd
if run_kargs; then ok 'kargs: empty kargs.d is fine'; else bad 'kargs: empty kargs.d is fine'; fi
expect 'kargs: empty directory, no set-kargs call' "[ ! -s \"$t/ostree.log\" ] && grep -q 'declares no kernel arguments' \"$t/kargs.out\""

# 19. The release's files: one for every architecture, one for another architecture,
#     one for this architecture that repeats an argument Anaconda already set.
kargs_target; mkdir -p $kd
printf '# luma-vitals-crash-evidence\nkargs = ["efi_pstore.pstore_disable=0"]\n' > $kd/10-luma-crash-evidence.toml
printf 'kargs = ["only-on-%s=1"]\nmatch-architectures = ["%s"]\n' "$other_arch" "$other_arch" > $kd/20-other-arch.toml
printf 'kargs = ["this-arch=1", "quiet"]\nmatch-architectures = ["%s", "riscv64"]\n' "$(uname -m)" > $kd/30-this-arch.toml
printf 'not = "a kargs file"\n' > $kd/README.txt
if run_kargs; then ok 'kargs: release files accepted'; else bad 'kargs: release files accepted'; cat "$t/kargs.out"; fi
expect 'kargs: efi_pstore.pstore_disable=0 applied' "[ \$(count efi_pstore.pstore_disable=0) = 1 ]"
expect 'kargs: matching architecture applied' "[ \$(count this-arch=1) = 1 ]"
expect 'kargs: other architecture skipped' "[ \$(count only-on-$other_arch=1) = 0 ] && grep -q 'skipped' \"$t/kargs.out\""
expect 'kargs: rhgb quiet kept, not duplicated' "[ \$(count rhgb) = 1 ] && [ \$(count quiet) = 1 ]"
expect 'kargs: Anaconda arguments kept' "[ \$(count root=UUID=1111-root) = 1 ] && [ \$(count rootflags=subvol=root) = 1 ] && [ \$(count rw) = 1 ]"
expect 'kargs: one set-kargs call per missing argument, merged' \
    "[ \$(wc -l < \"$t/ostree.log\") = 2 ] && grep -qx 'admin instutil set-kargs --sysroot=/mnt/sysimage --merge --append=efi_pstore.pstore_disable=0' \"$t/ostree.log\" && grep -qx 'admin instutil set-kargs --sysroot=/mnt/sysimage --merge --append=this-arch=1' \"$t/ostree.log\""
expect 'kargs: non-toml files ignored' "! grep -q 'kargs file' \"$t/ostree.log\""
expect 'kargs: what was applied is logged' "grep -q 'kernel arguments from kargs.d: efi_pstore.pstore_disable=0 this-arch=1 quiet' \"$t/kargs.out\" && grep -q 'appended efi_pstore.pstore_disable=0 this-arch=1' \"$t/kargs.out\" && grep -q 'boot entry options: ' \"$t/kargs.out\""

# 20. Running again adds nothing.
: > "$t/ostree.log"
run_kargs
expect 'kargs: second run is a no-op' "[ ! -s \"$t/ostree.log\" ] && [ \$(count efi_pstore.pstore_disable=0) = 1 ] && grep -q 'appended nothing' \"$t/kargs.out\""

# 21. Only files for another architecture.
kargs_target; mkdir -p $kd
printf 'kargs = ["x=1"]\nmatch-architectures = ["%s"]\n' "$other_arch" > $kd/10-other.toml
run_kargs
expect 'kargs: nothing for this architecture, no call' "[ ! -s \"$t/ostree.log\" ] && grep -q 'declares no kernel arguments for' \"$t/kargs.out\""

# 22. Files bootc would refuse fail the install, with the reason.
for body in 'kargs = [' 'kargs = "efi_pstore.pstore_disable=0"' 'kargs = ["two words"]' 'kargs = ["a=1"]\nmatch-architectures = "x86_64"'; do
    kargs_target; mkdir -p $kd
    printf "$body\n" > $kd/10-bad.toml
    if run_kargs; then bad "kargs: refused: $body"; else ok "kargs: refused: $body"; fi
done
expect 'kargs: refusal names the file' "grep -q 'kargs.d/10-bad.toml' \"$t/kargs.out\""

# 23. set-kargs that changes nothing fails the install rather than booting without the argument.
kargs_target; mkdir -p $kd
printf 'kargs = ["efi_pstore.pstore_disable=0"]\n' > $kd/10-crash.toml
if ATLAS_FAKE_OSTREE=noop run_kargs; then bad 'kargs: an argument that did not stick fails'; else ok 'kargs: an argument that did not stick fails'; fi
expect 'kargs: says which argument is missing' "grep -q 'still missing after set-kargs: efi_pstore.pstore_disable=0' \"$t/kargs.out\""

# 24. The payload check the ISO build runs, on the signed repository.
media_env nightly
medium_repo luma/1/x86_64/nightly
rm -rf "$t/verify" && ostree init --repo="$t/verify" --mode=archive && ostree config --repo="$t/verify" set core.min-free-space-percent 0
ostree remote add --repo="$t/verify" --gpg-import="$t/release.gpg" --set=gpg-verify=true --set=gpg-verify-summary=true \
    --collection-id=org.projectluma.OS check file:///run/install/repo/luma/repo
expect 'build check: signed payload verifies' "ostree pull --repo=$t/verify --commit-metadata-only check luma/1/x86_64/nightly >/dev/null 2>&1"
export GNUPGHOME="$t/other" && install -d -m 0700 "$GNUPGHOME"
gpg --batch --passphrase '' --quick-gen-key 'Another key' ed25519 sign 1d >/dev/null 2>&1
gpg --export > "$t/other.gpg"
rm -rf "$t/verify" && ostree init --repo="$t/verify" --mode=archive && ostree config --repo="$t/verify" set core.min-free-space-percent 0
ostree remote add --repo="$t/verify" --gpg-import="$t/other.gpg" --set=gpg-verify=true --set=gpg-verify-summary=true \
    --collection-id=org.projectluma.OS check file:///run/install/repo/luma/repo
expect 'build check: payload signed by another key is refused' "! ostree pull --repo=$t/verify --commit-metadata-only check luma/1/x86_64/nightly >/dev/null 2>&1"

# 25. The new system's name (ADR-040, device names): the installed system's
# luma-device-name helper names it in install mode; without the helper the
# name is left unset (placeholders removed); a name the install was given is
# kept either way.
awk '/^%post --nochroot --erroronfail --interpreter=\/usr\/bin\/bash/{on=1; next} on && /^%end/{exit} on' "$ks" > "$t/hostname.sh"
grep -q 'luma-device-name' "$t/hostname.sh" || { echo 'could not find the hostname %post' >&2; exit 1; }
! grep -q 'DEFAULT_HOSTNAME' "$t/hostname.sh" || bad 'hostname: the %post no longer names machines after DEFAULT_HOSTNAME'
hostname_target() {
    # $1 /etc/hostname content ("-" for no file), $2 helper: none | fake | real
    rm -rf /mnt/sysroot && mkdir -p /mnt/sysroot/etc /mnt/sysroot/usr/lib /mnt/sysroot/usr/libexec/luma-os
    [ "$1" = - ] || printf '%s\n' "$1" > /mnt/sysroot/etc/hostname
    echo 'DEFAULT_HOSTNAME=luma' > /mnt/sysroot/usr/lib/os-release
    printf 'root:x:0:0:root:/root:/bin/bash\nnick:x:1000:1000:Nick McMillan:/home/nick:/bin/bash\n' > /mnt/sysroot/etc/passwd
    case "$2" in
        fake)
            cat > /mnt/sysroot/usr/libexec/luma-os/luma-device-name <<'FAKE'
import sys
open("/mnt/sysroot/helper-args", "w").write(" ".join(sys.argv[1:]))
print("luma-device-name: fake helper ran", file=sys.stderr)
raise SystemExit(3)
FAKE
            ;;
        real) install -m 0755 "$real_helper" /mnt/sysroot/usr/libexec/luma-os/luma-device-name ;;
    esac
}
run_hostname() { ANA_INSTALL_PATH=/mnt/sysroot bash "$t/hostname.sh" > "$t/hostname.out" 2>&1; }
no_hostname() { [ ! -e /mnt/sysroot/etc/hostname ]; }
hostname_is() { [ "$(cat /mnt/sysroot/etc/hostname)" = "$1" ] && [ "$(stat -c %a /mnt/sysroot/etc/hostname)" = 644 ]; }

hostname_target - fake
expect 'hostname: the target helper runs in install mode against the deployment root' \
    "run_hostname && [ \"\$(cat /mnt/sysroot/helper-args)\" = 'install --root /mnt/sysroot' ] && grep -qx 'atlas: fake helper ran' $t/hostname.out"
hostname_target - none
expect 'hostname: without the helper no name is set' "run_hostname && no_hostname"
for placeholder in localhost localhost.localdomain localhost-live fedora fedora.localdomain luma; do
    hostname_target "$placeholder" none
    expect "hostname: without the helper the placeholder $placeholder is removed" "run_hostname && no_hostname"
done
hostname_target nicks-thinkpad none
expect 'hostname: without the helper a name the install was given is kept' "run_hostname && hostname_is nicks-thinkpad"

# With the image's real helper, when this checkout carries it.
real_helper="$src/image/luma-desktop/rootfs/usr/libexec/luma-os/luma-device-name"
if [ -f "$real_helper" ]; then
    hostname_target - real
    expect 'hostname: the helper names the install after its person' \
        "run_hostname && grep -Eq '^nicks-(computer|desktop|laptop|tablet|[a-z-]+)(-[0-9]+)?\$' /mnt/sysroot/etc/hostname && grep -q \"^PRETTY_HOSTNAME=\\\"Nick’s \" /mnt/sysroot/etc/machine-info"
    hostname_target lab-7 real
    expect 'hostname: the helper keeps a name the install was given' "run_hostname && hostname_is lab-7 && [ ! -e /mnt/sysroot/etc/machine-info ]"
    hostname_target localhost-live real
    sed -i '/^nick:/d' /mnt/sysroot/etc/passwd
    expect 'hostname: the helper leaves the name unset with no account' "run_hostname && no_hostname"
else
    printf 'skip hostname: the image helper is not in this checkout\n'
fi

rm -rf "$t"
printf '\n%d passed, %d failed\n' "$passed" "$failed"
[ "$failed" = 0 ]
