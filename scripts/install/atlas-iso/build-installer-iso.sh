#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Compose a Luma installer ISO whose interface is Atlas.
#
# Fedora 44's Silverblue ISO runs Anaconda's GTK interface and carries no web
# UI, Cockpit or browser, so Atlas cannot be dropped onto it. Instead this
# builds the installer runtime the way Fedora builds its own boot.iso: lorax
# (the same 44.6 release) from Fedora 44's release repository, plus a local
# repository holding luma-installer-atlas and the pinned runtime kernel
# (config/install/atlas/runtime-kernel.env, since the release repository is
# frozen at the GA kernel). Anaconda 44.30 switches to its web
# UI whenever /usr/share/cockpit/anaconda-webui exists, and Atlas provides it.
# mkksiso then copies stage 2 into memory at boot (rd.live.ram=1, so the
# medium can be ejected before "Luma is ready.") and adds the payload
# repository to the ISO when one is carried.
#
# Channels and payload (ADR-030, sections 4, 5 and 9; constants in
# config/install/atlas/luma-os-channels.env):
#   - The medium installs luma/1/x86_64/<channel>: from the OSTree repository
#     on the medium when it carries that ref, otherwise from Luma's HTTPS
#     repository. The installer kickstart decides at boot.
#   - GPG verification is always on. The Luma OS Release public key is the
#     only key in the installer runtime's OSTree keyring; there is no option
#     to turn verification off.
#   - The installed system's origin is luma:luma/1/x86_64/<channel> on the
#     luma remote, whose url is mirrorlist=file:///etc/luma/update-mirrorlist;
#     that root-only list holds the HTTPS repository URL, and
#     /etc/luma/update-channel.conf records the channel.
#   - beta and nightly are served only to enrolled devices, so media for them
#     must carry the payload. Until Hub enrolls devices one by one, staff media
#     for those channels may carry one per-batch preview credential
#     (--preview-credential-file): root-only in the installer, written to the
#     installed system's root-only mirror list and enrollment record, and
#     checked to appear nowhere else in the medium, its sidecars or the logs.
#     Without the option no credential is on a medium.
#
# Usage (as root, on the Linux/x86_64 build host):
#   build-installer-iso.sh --rpms DIR --boot-rpms DIR --boot-manifest FILE --work DIR --output ISO
#       [--channel stable|beta|nightly]   (default nightly: staff media)
#       [--payload-repo DIR]              OSTree repository to carry on the medium
#       [--release-key PATH]              default /etc/pki/ostree/luma-release.gpg
#       [--preview-credential-file FILE]  beta or nightly staff media only: one line,
#                                         the batch's base64url preview credential
#       [--display-name NAME]             the release's os-release PRETTY_NAME, e.g.
#                                         "Luma (Prairie, Beta 0, Nightly 20260916)": names the
#                                         boot menu entries ("Install NAME") and, without
#                                         --volid, the volume label (Luma-Prairie-Beta-0-20260916)
#       [--volid LABEL] [--product NAME] [--version VERSION] [--release-repo URL]
#
#   Test media only (a stand-in payload, never a Luma release):
#       --test-payload-url URL --test-payload-ref REF --test-payload-key PATH
#   The stand-in is pulled with GPG verification against its own key; the
#   installed system is still given the Luma remote, key, origin and channel,
#   so everything after the pull is what a Luma install gets.
#
# Examples:
#   Staff media carrying tonight's nightly:
#     --channel nightly --payload-repo /srv/luma-os/repo
#   Public media that download stable:
#     --channel stable --display-name "Luma (Version 1, Prairie)"
#   VM test media (Fedora Silverblue stand-in, verified with Fedora's key):
#     --test-payload-url https://ostree.example/fedora --test-payload-ref fedora/44/x86_64/silverblue \
#     --test-payload-key /etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-44-primary --release-key TEST_KEY

set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$here/../../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/install/atlas/luma-os-channels.env"

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

rpms="" work="" output="" boot_rpms="" boot_manifest=""
channel=$LUMA_OS_DEFAULT_CHANNEL
payload_repo=""
release_key=$LUMA_OS_RELEASE_KEY
test_url="" test_ref="" test_key=""
credential_file=""
# The installer runtime's product version is the OS major version the medium
# installs (luma/<major>/x86_64); release names come from --display-name.
product="Luma" version=$(printf '%s' "$LUMA_OS_REF_PREFIX" | cut -d/ -f2)
volid="" display_name=""
release_repo="https://dl.fedoraproject.org/pub/fedora/linux/releases/44/Everything/x86_64/os/"
tools_image="localhost/luma-atlas-iso-tools:44"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --rpms) rpms=$2; shift 2 ;;
    --boot-rpms) boot_rpms=$2; shift 2 ;;
    --boot-manifest) boot_manifest=$2; shift 2 ;;
    --work) work=$2; shift 2 ;;
    --output) output=$2; shift 2 ;;
    --channel) channel=$2; shift 2 ;;
    --payload-repo) payload_repo=$2; shift 2 ;;
    --release-key) release_key=$2; shift 2 ;;
    --test-payload-url) test_url=$2; shift 2 ;;
    --test-payload-ref) test_ref=$2; shift 2 ;;
    --test-payload-key) test_key=$2; shift 2 ;;
    --preview-credential-file) credential_file=$2; shift 2 ;;
    --volid) volid=$2; shift 2 ;;
    --display-name) display_name=$2; shift 2 ;;
    --product) product=$2; shift 2 ;;
    --version) version=$2; shift 2 ;;
    --release-repo) release_repo=$2; shift 2 ;;
    --nogpg|--no-gpg|--gpg-verify)
      fail "$1 is not an option: Luma media always verify the payload with GPG" ;;
    *) fail "unknown argument $1" ;;
  esac
done

for value in rpms work output boot_rpms boot_manifest; do
  [ -n "${!value}" ] || fail "--$value is required"
done
[ "$(id -u)" = 0 ] || fail 'lorax needs root; run as root'
case "$version" in ''|*[!0-9]*) fail "cannot read the OS major version from LUMA_OS_REF_PREFIX=$LUMA_OS_REF_PREFIX" ;; esac
if [ -n "$display_name" ]; then
  # Shown in GRUB and isolinux menu titles: one line, no quoting characters.
  case "$display_name" in
    *[\'\"\\\`\$]*|*$'\n'*|*$'\t'*) fail "--display-name may not contain quotes, backslashes, \$ or control characters" ;;
    "$product"|"$product "*) ;;
    *) fail "--display-name must start with the product name $product: $display_name" ;;
  esac
  [ "${#display_name}" -le 80 ] || fail "--display-name is longer than 80 characters"
fi
if [ -z "$volid" ] && [ -n "$display_name" ]; then
  # Luma (Prairie, Beta 0, Nightly 20260916) -> Luma-Prairie-Beta-0-20260916
  # Luma (Version 1, Prairie)                -> Luma-1-Prairie-x86_64
  volid=$(printf '%s\n' "$display_name" | tr -cs 'A-Za-z0-9' '\n' |
    grep -vx -e '' -e Nightly -e Version | paste -sd- -)
  [ "${#volid}" -le 25 ] && volid="$volid-x86_64"
  while [ "${#volid}" -gt 32 ]; do volid=${volid%-*}; done
fi
[ -n "$volid" ] || volid="$product-$version-Install-x86_64"
[ "${#volid}" -le 32 ] || fail "volume label $volid is longer than 32 characters"
case "$volid" in *[!A-Za-z0-9_-]*) fail "volume label $volid may only use letters, digits, _ and -" ;; esac
ls "$rpms"/luma-installer-atlas-*.noarch.rpm >/dev/null 2>&1 || fail "no luma-installer-atlas RPM in $rpms"

case " $LUMA_OS_CHANNELS " in *" $channel "*) ;; *) fail "--channel must be one of: $LUMA_OS_CHANNELS" ;; esac
ref="$LUMA_OS_REF_PREFIX/$channel"

[ -s "$release_key" ] || fail "the Luma OS Release public key is missing: $release_key (pass --release-key)"
case "$LUMA_OS_URL" in *[[:space:]]*) fail "LUMA_OS_URL contains whitespace" ;; https://?*) ;; *) fail "LUMA_OS_URL must be an HTTPS URL: $LUMA_OS_URL" ;; esac
case "$LUMA_OS_MIRRORLIST" in
  /etc/luma/*/*|/etc/luma/) fail "LUMA_OS_MIRRORLIST must be a file directly in /etc/luma: $LUMA_OS_MIRRORLIST" ;;
  /etc/luma/[A-Za-z0-9._-]*) ;;
  *) fail "LUMA_OS_MIRRORLIST must be a file directly in /etc/luma: $LUMA_OS_MIRRORLIST" ;;
esac

test_media=0
if [ -n "$test_url$test_ref$test_key" ]; then
  [ -n "$test_url" ] && [ -n "$test_ref" ] && [ -n "$test_key" ] ||
    fail '--test-payload-url, --test-payload-ref and --test-payload-key go together'
  [ -z "$payload_repo" ] || fail '--payload-repo and a test payload cannot be combined'
  [ -s "$test_key" ] || fail "test payload key is missing: $test_key"
  case "$test_url" in https://*) ;; *) fail 'the test payload URL must use HTTPS' ;; esac
  test_media=1
fi

case " $LUMA_OS_PREVIEW_CHANNELS " in
  *" $channel "*)
    if [ -z "$payload_repo" ] && [ "$test_media" = 0 ]; then
      fail "$channel is a preview channel: its repository needs a per-device credential, so the medium must carry the payload (--payload-repo)"
    fi ;;
esac

if [ -n "$credential_file" ]; then
  case " $LUMA_OS_PREVIEW_CHANNELS " in
    *" $channel "*) ;;
    *) fail "--preview-credential-file is only for the preview channels ($LUMA_OS_PREVIEW_CHANNELS), not $channel" ;;
  esac
  [ -f "$credential_file" ] && [ -r "$credential_file" ] || fail "cannot read the preview credential file $credential_file"
  # One line of 16-256 base64url characters (luma-update's credential format),
  # checked without printing it.
  [ "$(wc -l < "$credential_file" | tr -d ' ')" -le 1 ] &&
    tr -d '\n' < "$credential_file" | grep -Eqx '[A-Za-z0-9_-]{16,256}' ||
    fail "the preview credential file $credential_file does not hold one base64url credential of 16 to 256 characters"
fi

mkdir -p "$work"
work=$(realpath "$work")
rm -rf "$work/lorax-out" "$work/tmp" "$work/luma-rpms" "$work/templates" "$work/keys" "$work/secret" "$work/lorax-templates"
rm -f "$work/logs/container.log"
mkdir -p "$work/tmp" "$work/luma-rpms" "$work/templates" "$work/logs" "$work/keys"

# The preview credential: normalized to one line without a newline, root-only,
# and removed from the work tree however the build ends.
credential_var=none
if [ -n "$credential_file" ]; then
  install -d -m 0700 "$work/secret"
  (umask 077; tr -d '\n' < "$credential_file" > "$work/secret/preview-credential")
  credential_var=/w/secret/preview-credential
  trap 'rm -rf "$work/secret"' EXIT
fi

podman build --tag "$tools_image" --file "$here/Containerfile" "$here"

cp "$rpms"/luma-installer-atlas-*.noarch.rpm "$work/luma-rpms/"
python3 "$here/prepare-boot-rpms.py" --inputs "$repo_root/config/desktop/inputs.env" \
  --manifest "$boot_manifest" --pool "$boot_rpms" --output "$work/luma-rpms"

# The installer runtime's kernel: the one the installed system runs, not
# Fedora 44's frozen GA kernel (see config/install/atlas/runtime-kernel.env).
# shellcheck disable=SC1091
. "$repo_root/config/install/atlas/runtime-kernel.env"
kernel_cache=${ATLAS_RUNTIME_KERNEL_CACHE:-$work/kernel-cache}
mkdir -p "$kernel_cache"
for package in $ATLAS_RUNTIME_KERNEL_PACKAGES; do
  kernel_rpm="$package-$ATLAS_RUNTIME_KERNEL_NVR.$ATLAS_RUNTIME_KERNEL_ARCH.rpm"
  sum_name="ATLAS_RUNTIME_KERNEL_SHA256_${package//-/_}"
  sum=${!sum_name:-}
  [ -n "$sum" ] || fail "runtime-kernel.env has no sha256 for $package"
  if [ ! -s "$kernel_cache/$kernel_rpm" ] ||
     [ "$(sha256sum <"$kernel_cache/$kernel_rpm" | cut -d' ' -f1)" != "$sum" ]; then
    curl -fsSL --retry 3 --output "$kernel_cache/$kernel_rpm.part" \
        "$ATLAS_RUNTIME_KERNEL_BASE_URL/$kernel_rpm" ||
      fail "could not fetch $ATLAS_RUNTIME_KERNEL_BASE_URL/$kernel_rpm"
    mv "$kernel_cache/$kernel_rpm.part" "$kernel_cache/$kernel_rpm"
  fi
  [ "$(sha256sum <"$kernel_cache/$kernel_rpm" | cut -d' ' -f1)" = "$sum" ] ||
    fail "$kernel_rpm does not match its sha256 pin"
  cp "$kernel_cache/$kernel_rpm" "$work/luma-rpms/"
done
printf 'installer runtime kernel %s.%s (pinned)\n' \
    "$ATLAS_RUNTIME_KERNEL_NVR" "$ATLAS_RUNTIME_KERNEL_ARCH"
cp "$here/luma-atlas-runtime.tmpl" "$work/templates/"
cp "$repo_root/scripts/boot/check-grub-fonts.py" "$work/templates/"
cp "$repo_root/config/install/atlas/interactive-defaults.ks" "$work/templates/interactive-defaults.ks"
cp "$release_key" "$work/keys/release.key"
# The key the installer's pull trusts: the release key, or the stand-in's key
# on test media.
if [ "$test_media" = 1 ]; then
  cp "$test_key" "$work/keys/pull.key"
else
  cp "$release_key" "$work/keys/pull.key"
fi

# PID 1's native clock epoch keeps an offline installer with a stale RTC from
# asking GnuPG to verify keys/signatures that appear to come from the future.
# The epoch is carried as file metadata in both stage 2 and the initramfs.
clock_epoch=$(date -u +%s)
{
  printf '# Written by build-installer-iso.sh; read by the installer kickstart.\n'
  printf 'clock_epoch=%s\n' "$clock_epoch"
  printf 'channel=%s\n' "$channel"
  printf 'channels=%s\n' "$LUMA_OS_CHANNELS"
  printf 'remote=%s\n' "$LUMA_OS_REMOTE"
  printf 'https_url=%s\n' "$LUMA_OS_URL"
  printf 'mirrorlist=%s\n' "$LUMA_OS_MIRRORLIST"
  printf 'preview_channels=%s\n' "$LUMA_OS_PREVIEW_CHANNELS"
  printf 'preview_url_prefix=%s\n' "$LUMA_OS_PREVIEW_URL_PREFIX"
  printf 'preview_record=%s\n' "$LUMA_OS_PREVIEW_RECORD"
  printf 'collection_id=%s\n' "$LUMA_OS_COLLECTION_ID"
  printf 'ref=%s\n' "$ref"
  printf 'stateroot=%s\n' "$LUMA_OS_STATEROOT"
  printf 'medium_repo=%s\n' "$LUMA_OS_MEDIUM_REPO"
  printf 'target_key=%s\n' "$LUMA_OS_TARGET_KEY"
  printf 'volid=%s\n' "$volid"
  printf 'test_payload_url=%s\n' "$test_url"
  printf 'test_payload_ref=%s\n' "$test_ref"
} > "$work/templates/media.env"

# The boot menus say "Install <display name>": lorax writes "<product>
# <version>" into every menu title (Install, Test this media & install, basic
# graphics, Troubleshooting) and nowhere else, and mkksiso replaces it.
menu_replace=""
if [ -n "$display_name" ]; then
  menu_replace="-R '$product $version' '$display_name'"
fi

payload_mount=() payload_add=""
if [ -n "$payload_repo" ]; then
  payload_repo=$(realpath "$payload_repo")
  [ -f "$payload_repo/config" ] && [ -d "$payload_repo/objects" ] || fail "$payload_repo is not an OSTree repository"
  # mkksiso adds the directory by its base name, so mount the repository
  # at /add/<first component of LUMA_OS_MEDIUM_REPO>/<rest>.
  payload_mount=(--volume "$payload_repo:/add/$LUMA_OS_MEDIUM_REPO:ro")
  payload_add="-a /add/${LUMA_OS_MEDIUM_REPO%%/*}"
fi

# DRACUT_NO_XATTR: inside a container the kernel refuses copying SELinux
# xattrs into dracut's staging tree, which silently drops files from the
# initramfs (the first build lacked systemd-sysroot-fstab-check and could not
# mount its root). /dev is shared so lorax sees loop devices it creates.
podman run --rm --privileged --network host --env DRACUT_NO_XATTR=1 \
    --volume /dev:/dev --volume "$work:/w" --volume "$here:/s:ro" "${payload_mount[@]}" "$tools_image" bash -euo pipefail -c "
  trap 'echo \"error: build step failed at line \$LINENO of the container script\" >&2' ERR
  # Dracut normalizes mtimes when SOURCE_DATE_EPOCH is set. Use this medium's
  # epoch so normalization cannot erase the native clock floor.
  export SOURCE_DATE_EPOCH='$clock_epoch'
  # Keys: one OpenPGP public key each, stored binary (dearmored).
  for name in release pull; do
    install -d -m 0700 /w/tmp/gnupg-\$name
    gpg --batch --quiet --homedir /w/tmp/gnupg-\$name --import /w/keys/\$name.key 2>/dev/null ||
      { echo \"error: the \$name key is not an OpenPGP public key\" >&2; exit 1; }
    count=\$(gpg --batch --homedir /w/tmp/gnupg-\$name --with-colons --list-keys | grep -c '^pub:')
    [ \"\$count\" = 1 ] || { echo \"error: the \$name key file holds \$count keys, not one\" >&2; exit 1; }
    gpg --batch --homedir /w/tmp/gnupg-\$name --export > /w/keys/\$name.gpg
    gpg --batch --homedir /w/tmp/gnupg-\$name --with-colons --fingerprint | awk -F: '/^fpr:/ { print \$10; exit }' > /w/keys/\$name.fingerprint
  done
  echo \"release key \$(cat /w/keys/release.fingerprint)\"
  echo \"pull key    \$(cat /w/keys/pull.fingerprint)\"

  if [ -n '$payload_repo' ]; then
    # The carried payload must be exactly what an online install would get:
    # the channel's ref, a signed summary and a signed commit, the Luma
    # collection id.
    repo=/add/$LUMA_OS_MEDIUM_REPO
    collection=\$(ostree --repo=\$repo config get core.collection-id 2>/dev/null || true)
    [ \"\$collection\" = '$LUMA_OS_COLLECTION_ID' ] ||
      { echo \"error: payload collection id is '\$collection', not $LUMA_OS_COLLECTION_ID\" >&2; exit 1; }
    ostree --repo=\$repo rev-parse '$ref' >/dev/null ||
      { echo 'error: the payload repository has no $ref' >&2; exit 1; }
    rm -rf /w/tmp/verify && ostree init --repo=/w/tmp/verify --mode=archive
    # Commit metadata only: kilobytes, whatever the build disk's free space.
    ostree config --repo=/w/tmp/verify set core.min-free-space-percent 0
    ostree remote add --repo=/w/tmp/verify --gpg-import=/w/keys/release.gpg \
        --set=gpg-verify=true --set=gpg-verify-summary=true \
        --collection-id='$LUMA_OS_COLLECTION_ID' check file://\$repo
    ostree pull --repo=/w/tmp/verify --commit-metadata-only check '$ref'
    echo \"payload $ref \$(ostree --repo=\$repo rev-parse '$ref') verified\"
    rm -rf /w/tmp/verify
  fi

  while read -r nevra digest header; do
    actual=\$(rpm -qp --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH} %{SHA256HEADER}' /w/luma-rpms/\$nevra.rpm)
    [ \"\$actual\" = \"\$nevra \$header\" ] || { echo \"error: boot RPM header mismatch: \$nevra\" >&2; exit 1; }
  done < /w/boot-packages.manifest
  python3 /s/prepare-lorax-boot.py /usr/share/lorax /w/lorax-templates
  createrepo_c --quiet /w/luma-rpms
  boot_install_args=()
  while read -r nevra digest header; do
    boot_install_args+=(--installpkgs \"\$nevra\")
  done < /w/boot-packages.manifest
  lorax --product '$product' --version '$version' --release 44 --isfinal \
        --volid '$volid' --nomacboot --noupgrade \
        --source '$release_repo' --source file:///w/luma-rpms \
        --installpkgs luma-installer-atlas --installpkgs slitherer \
        --installpkgs cockpit-ws --installpkgs cockpit-bridge \
        \"\${boot_install_args[@]}\" \
        --sharedir /w/lorax-templates \
        --add-template /w/templates/luma-atlas-runtime.tmpl \
        --add-template-var luma_atlas_ks=/w/templates/interactive-defaults.ks \
        --add-template-var luma_atlas_media=/w/templates/media.env \
        --add-template-var luma_atlas_release_key=/w/keys/release.gpg \
        --add-template-var luma_atlas_pull_key=/w/keys/pull.gpg \
        --add-template-var luma_atlas_clock_epoch=$clock_epoch \
        --add-template-var luma_atlas_preview_credential=$credential_var \
        --tmp /w/tmp --logfile /w/logs/lorax.log \
        /w/lorax-out
  if grep -q 'dracut-install: ERROR' /w/logs/program.log; then
    echo 'error: dracut could not install files into the initramfs; see logs/program.log' >&2
    exit 1
  fi
  lsinitrd /w/lorax-out/images/pxeboot/initrd.img > /w/logs/initrd-contents.txt
  # The runtime boots the pinned kernel, not the release repository's GA one:
  # the modules in its initramfs name the kernel they were built for.
  # grep -m1 stops reading by itself: a sed | head pipe fails under pipefail
  # once the listing outgrows the pipe buffer (SIGPIPE to sed).
  runtime_kernel=\$(grep -o -m1 'usr/lib/modules/[0-9][^/]*' /w/logs/initrd-contents.txt | cut -d/ -f4)
  echo \"runtime kernel \$runtime_kernel\"
  printf '%s\n' \"\$runtime_kernel\" > /w/logs/runtime-kernel.txt
  [ \"\$runtime_kernel\" = '$ATLAS_RUNTIME_KERNEL_NVR.$ATLAS_RUNTIME_KERNEL_ARCH' ] || {
    echo \"error: the installer runtime kernel is '\$runtime_kernel', not the pinned $ATLAS_RUNTIME_KERNEL_NVR.$ATLAS_RUNTIME_KERNEL_ARCH\" >&2
    exit 1
  }
  grep -q 'usr/lib/systemd/systemd-sysroot-fstab-check' /w/logs/initrd-contents.txt || {
    echo 'error: the installer initramfs is incomplete' >&2
    exit 1
  }
  # Presence alone is insufficient: systemd reads this file's mtime.
  mkdir -p /w/tmp/clock-initrd
  # Lorax uses --xz --no-early-microcode. lsinitrd --unpack does not preserve
  # mtimes, so explicitly restore the archive metadata with cpio -m.
  (cd /w/tmp/clock-initrd && xz -dc /w/lorax-out/images/pxeboot/initrd.img |
      cpio --extract --make-directories --preserve-modification-time --no-absolute-filenames --quiet usr/lib/clock-epoch)
  test \"\$(stat -c %Y /w/tmp/clock-initrd/usr/lib/clock-epoch)\" = '$clock_epoch'
  for file in luma-loading.plymouth luma-loading.script luma-wordmark.png luma-loading-dot.png; do
    grep -Fq \"usr/share/plymouth/themes/luma-loading/\$file\" /w/logs/initrd-contents.txt || {
      echo \"error: installer initramfs lacks Luma loader file: \$file\" >&2; exit 1;
    }
  done
  python3 /w/templates/check-grub-fonts.py /w/lorax-out/boot/grub2/themes/luma
  # The runtime must hold exactly the files this medium was built with.
  rm -rf /w/tmp/stage2 && mkdir -p /w/tmp/stage2
  unsquashfs -no-progress -d /w/tmp/stage2 /w/lorax-out/images/install.img \
      usr/share/anaconda/interactive-defaults.ks usr/share/luma-installer-atlas usr/share/ostree usr/lib/clock-epoch \
      usr/share/plymouth/plymouthd.defaults usr/share/plymouth/themes/luma-loading \
      usr/lib/systemd/system/plymouth-reboot.service usr/lib/systemd/system/reboot.target.wants/plymouth-reboot.service \
      usr/lib/systemd/system/plymouth-quit.service.d usr/lib/systemd/system/plymouth-quit-wait.service.d \
      usr/lib64/python3.14/site-packages/pyanaconda/display.py \
      usr/lib64/python3.14/site-packages/pyanaconda/timezone.py \
      usr/lib64/python3.14/site-packages/pyanaconda/modules/payloads/payload/rpm_ostree/installation.py \
      usr/share/anaconda/window-manager/glib-2.0/schemas usr/share/dconf/profile/gnomekiosk >/dev/null
  grep -Fxq 'Theme=luma-loading' /w/tmp/stage2/usr/share/plymouth/plymouthd.defaults
  test -s /w/tmp/stage2/usr/share/plymouth/themes/luma-loading/luma-loading.script
  grep -Fxq first-frame /w/tmp/stage2/usr/share/luma-installer-atlas/plymouth-handoff-policy
  test -s /w/tmp/stage2/usr/share/luma-installer-atlas/loader-background.svg
  test -s /w/tmp/stage2/usr/share/luma-installer-atlas/kiosk-background.dconf
  grep -Fxq 'file-db:/usr/share/luma-installer-atlas/kiosk-background.dconf' /w/tmp/stage2/usr/share/dconf/profile/gnomekiosk
  grep -Fxq 'file-db:/usr/share/gnome-kiosk/gnomekiosk.dconf.compiled' /w/tmp/stage2/usr/share/dconf/profile/gnomekiosk
  for owner in plymouth-quit plymouth-quit-wait; do
    grep -Fxq 'ConditionKernelCommandLine=!rhgb' /w/tmp/stage2/usr/lib/systemd/system/\$owner.service.d/50-luma-atlas-handoff.conf
  done
  grep -Fq '_luma_release_boot_splash()' /w/tmp/stage2/usr/lib64/python3.14/site-packages/pyanaconda/display.py
  python3 /s/prepare-runtime-clock.py --verify /w/tmp/stage2
  test -s /w/tmp/stage2/usr/share/anaconda/window-manager/glib-2.0/schemas/gschemas.compiled
  grep -Fq 'plymouthd --mode=reboot' /w/tmp/stage2/usr/lib/systemd/system/plymouth-reboot.service
  test \"\$(readlink /w/tmp/stage2/usr/lib/systemd/system/reboot.target.wants/plymouth-reboot.service)\" = ../plymouth-reboot.service
  cmp /w/templates/interactive-defaults.ks /w/tmp/stage2/usr/share/anaconda/interactive-defaults.ks
  cmp /w/templates/media.env /w/tmp/stage2/usr/share/luma-installer-atlas/media.env
  grep -Fxq 'mirrorlist=$LUMA_OS_MIRRORLIST' /w/tmp/stage2/usr/share/luma-installer-atlas/media.env
  grep -Fxq 'https_url=$LUMA_OS_URL' /w/tmp/stage2/usr/share/luma-installer-atlas/media.env
  # The kickstart writes the mirror list and never a URL into the remote.
  grep -Fq 'mirrorlist_url = \"mirrorlist=file://\" + mirrorlist' /w/tmp/stage2/usr/share/anaconda/interactive-defaults.ks
  grep -Fq 'Retired by the Luma installer' /w/tmp/stage2/usr/share/anaconda/interactive-defaults.ks
  grep -Fq 'usr/lib/bootc/kargs.d' /w/tmp/stage2/usr/share/anaconda/interactive-defaults.ks
  cmp /w/keys/release.gpg /w/tmp/stage2/usr/share/luma-installer-atlas/luma-release.gpg
  [ \"\$(ls /w/tmp/stage2/usr/share/ostree/trusted.gpg.d)\" = luma-release.gpg ] ||
    { echo 'error: the runtime OSTree keyring holds more than the pull key' >&2; exit 1; }
  cmp /w/keys/pull.gpg /w/tmp/stage2/usr/share/ostree/trusted.gpg.d/luma-release.gpg
  test \"\$(stat -c %Y /w/tmp/stage2/usr/lib/clock-epoch)\" = '$clock_epoch'
  rm -rf /w/tmp/stage2
  if [ '$credential_var' != none ]; then
    # The credential is root-only in the installer and nowhere else in it.
    rm -rf /w/tmp/stage2-all
    unsquashfs -no-progress -no-xattrs -d /w/tmp/stage2-all /w/lorax-out/images/install.img >/dev/null
    carried=/w/tmp/stage2-all/usr/share/luma-installer-atlas/preview-credential
    [ \"\$(stat -c '%a %u %g' \$carried)\" = '600 0 0' ] ||
      { echo 'error: the installer copy of the preview credential is not root:root 0600' >&2; exit 1; }
    cmp -s /w/secret/preview-credential \$carried ||
      { echo 'error: the installer copy of the preview credential differs from the file given' >&2; exit 1; }
    /s/check-secret-absent.sh --secret /w/secret/preview-credential --exclude \$carried \
        --label 'elsewhere in the installer image' /w/tmp/stage2-all
    rm -rf /w/tmp/stage2-all
  else
    unsquashfs -no-progress -d /w/tmp/stage2 /w/lorax-out/images/install.img \
        usr/share/luma-installer-atlas/preview-credential >/dev/null 2>&1 || :
    [ ! -e /w/tmp/stage2/usr/share/luma-installer-atlas/preview-credential ] ||
      { echo 'error: this medium was not given a preview credential but its installer holds one' >&2; exit 1; }
    rm -rf /w/tmp/stage2
  fi
  rm -f /w/output.iso.partial
  mkksiso -V '$volid' -c 'rd.live.ram=1' \
          -R 'set default=\"1\"' 'set default=\"0\"' \
          $menu_replace \
          $payload_add \
          /w/lorax-out/images/boot.iso /w/output.iso.partial
  mkdir -p /w/tmp/iso-boot-proof
  xorriso -osirrox on -indev /w/output.iso.partial \
    -extract /boot/grub2 /w/tmp/iso-boot-proof/grub2 >/dev/null 2>&1
  xorriso -osirrox on -indev /w/output.iso.partial \
    -extract /EFI/BOOT/grub.cfg /w/tmp/iso-boot-proof/efi-grub.cfg >/dev/null 2>&1
  for cfg in /w/tmp/iso-boot-proof/grub2/grub.cfg /w/tmp/iso-boot-proof/efi-grub.cfg; do
    grep -Fq 'set theme=/boot/grub2/themes/luma/theme.txt' \"\$cfg\"
    if grep -Fq '@ROOT@' \"\$cfg\"; then
      echo 'error: unresolved GRUB boot root' >&2; exit 1
    fi
  done
  python3 /w/templates/check-grub-fonts.py /w/tmp/iso-boot-proof/grub2/themes/luma
  cmp /w/lorax-out/boot/grub2/themes/luma/theme.txt /w/tmp/iso-boot-proof/grub2/themes/luma/theme.txt
  if [ '$credential_var' != none ]; then
    # Every file on the medium other than the compressed installer image, and
    # the image itself, as bytes.
    mkdir -p /w/tmp/iso && mount -o ro,loop /w/output.iso.partial /w/tmp/iso
    status=0
    /s/check-secret-absent.sh --secret /w/secret/preview-credential --label 'in the files on the medium' /w/tmp/iso || status=\$?
    umount /w/tmp/iso
    [ \$status = 0 ]
  fi
" 2>&1 | tee "$work/logs/container.log"

mv "$work/output.iso.partial" "$output"
{
  printf 'channel=%s\nref=%s\nremote=%s\nurl=%s\nremote_url=mirrorlist=file://%s\nmirrorlist=%s\n' \
    "$channel" "$ref" "$LUMA_OS_REMOTE" "$LUMA_OS_URL" "$LUMA_OS_MIRRORLIST" "$LUMA_OS_MIRRORLIST"
  printf 'payload=%s\n' "$( [ -n "$payload_repo" ] && echo medium || { [ "$test_media" = 1 ] && echo "test $test_ref"; } || echo online)"
  printf 'release_key_fingerprint=%s\n' "$(cat "$work/keys/release.fingerprint")"
  printf 'pull_key_fingerprint=%s\n' "$(cat "$work/keys/pull.fingerprint")"
  printf 'preview_credential=%s\n' "$( [ -n "$credential_file" ] && echo carried || echo none)"
  printf 'runtime_kernel=%s\n' "$(cat "$work/logs/runtime-kernel.txt")"
  printf 'clock_epoch=%s\n' "$clock_epoch"
} > "$output.media"
sha256sum "$output" | tee "$output.sha256"
cat "$output.media"
if [ -n "$credential_file" ]; then
  # Nothing written beside the medium or into the build logs holds it.
  "$here/check-secret-absent.sh" --secret "$work/secret/preview-credential" \
      --label 'in the sidecars, templates or build logs' \
      "$output.media" "$output.sha256" "$work/templates" "$work/logs"
  rm -rf "$work/secret"
fi
rm -rf "$work/tmp" "$work/lorax-out"
