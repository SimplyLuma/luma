#!/usr/bin/env bash

set -Eeuo pipefail

if (( EUID != 0 )); then
  echo "run as root" >&2
  exit 1
fi

stamp="${1:-$(date -u +%Y%m%dT%H%M%SZ)}"
domain="luma-design"
backup_root="/var/lib/luma-backups/${stamp}"
metadata_dir="${backup_root}/metadata"
chunks_dir="${backup_root}/release-assets"
source_tree="/home/nick/src/project-luma"
vm_archive="${backup_root}/project-luma-vm-state-${stamp}.tar.zst"
evidence_archive="${backup_root}/project-luma-build-evidence-${stamp}.tar.zst"
worktree_archive="${backup_root}/project-luma-dell-worktree-${stamp}.tar.zst"

mkdir -p "${metadata_dir}" "${chunks_dir}"
chmod 0700 "${backup_root}"

domain_was_running=false
if [[ "$(virsh --connect qemu:///system domstate "${domain}")" == "running" ]]; then
  domain_was_running=true
fi

restart_domain() {
  if [[ "${domain_was_running}" == true ]] &&
     [[ "$(virsh --connect qemu:///system domstate "${domain}" 2>/dev/null || true)" != "running" ]]; then
    virsh --connect qemu:///system start "${domain}" >/dev/null || true
  fi
}
trap restart_domain EXIT

if [[ "${domain_was_running}" == true ]]; then
  virsh --connect qemu:///system shutdown "${domain}" >/dev/null
  for _ in {1..60}; do
    [[ "$(virsh --connect qemu:///system domstate "${domain}")" == "shut off" ]] && break
    sleep 2
  done
fi

if [[ "$(virsh --connect qemu:///system domstate "${domain}")" != "shut off" ]]; then
  echo "${domain} did not shut down cleanly; refusing to capture a crash-consistent image" >&2
  exit 1
fi

virsh --connect qemu:///system dumpxml --inactive "${domain}" >"${metadata_dir}/luma-design.xml"
virsh --connect qemu:///system net-dumpxml default >"${metadata_dir}/network-default.xml"
virsh --connect qemu:///system pool-dumpxml luma >"${metadata_dir}/pool-luma.xml"
virsh --connect qemu:///system pool-dumpxml luma-desktop >"${metadata_dir}/pool-luma-desktop.xml"
qemu-img info --backing-chain --output=json /var/lib/libvirt/images/luma/design.qcow2 \
  >"${metadata_dir}/design-backing-chain.json"

for image in baseline.qcow2 design.qcow2 acceptance-base.qcow2; do
  qemu-img check "/var/lib/libvirt/images/luma/${image}" \
    >"${metadata_dir}/${image}.check.txt"
done

if command -v rpm-ostree >/dev/null && rpm-ostree status --json \
  >"${metadata_dir}/rpm-ostree-status.json" \
  2>"${metadata_dir}/rpm-ostree-status.stderr.txt"; then
  :
else
  echo "host is not managed by rpm-ostree" >"${metadata_dir}/rpm-ostree-status.unavailable.txt"
fi
rpm -qa --qf '%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\t%{ARCH}\n' | sort \
  >"${metadata_dir}/installed-packages.tsv"
hostnamectl >"${metadata_dir}/hostnamectl.txt"
uname -a >"${metadata_dir}/uname.txt"
semodule -lfull >"${metadata_dir}/selinux-modules.txt"
semanage export >"${metadata_dir}/selinux-local-customizations.txt"
virsh --connect qemu:///system list --all >"${metadata_dir}/domains.txt"

sha256sum \
  /var/lib/libvirt/images/luma/baseline.qcow2 \
  /var/lib/libvirt/images/luma/design.qcow2 \
  /var/lib/libvirt/images/luma/acceptance-base.qcow2 \
  /var/lib/libvirt/images/luma/test-provisioning.ign \
  /var/lib/libvirt/qemu/nvram/luma-design_VARS.qcow2 \
  >"${metadata_dir}/source-files.sha256"

tar --numeric-owner --xattrs --acls --selinux --sparse \
  -I 'zstd -T0 -10' -C / -cf "${vm_archive}" \
  var/lib/libvirt/images/luma \
  var/lib/libvirt/qemu/nvram/luma-design_VARS.qcow2 \
  var/lib/libvirt/swtpm \
  etc/libvirt \
  etc/greetd \
  etc/polkit-1/rules.d \
  etc/systemd/system \
  etc/systemd/logind.conf.d \
  etc/tmpfiles.d \
  etc/ssh/sshd_config.d \
  "var/lib/luma-backups/${stamp}/metadata"

if [[ -d "${source_tree}/build" ]]; then
  (
    cd "${source_tree}"
    find build -type f \
      \( -name 'SHA256SUMS' -o -name 'build-report.txt' -o -name '*.buildlog' \
         -o -name '*.log' -o -name '*.json' -o -name '*.rpm' -o -name '*.ign' \
         -o -name '*.toml' -o -name '*.txt' -o -name '*.ppm' \) \
      -print0 | sort -z >"${metadata_dir}/build-evidence-files.nul"
    tar --null -I 'zstd -T0 -10' -cf "${evidence_archive}" \
      --files-from="${metadata_dir}/build-evidence-files.nul"
  )
fi

if [[ -d "${source_tree}" ]]; then
  tar -I 'zstd -T0 -10' -C "${source_tree}" \
    --exclude=.git --exclude=build --exclude=wiki/node_modules \
    -cf "${worktree_archive}" .
fi

for archive in "${vm_archive}" "${evidence_archive}" "${worktree_archive}"; do
  [[ -f "${archive}" ]] || continue
  archive_name="$(basename "${archive}")"
  split --bytes=1900M --numeric-suffixes=0 --suffix-length=2 \
    "${archive}" "${chunks_dir}/${archive_name}.part-"
done

(
  cd "${chunks_dir}"
  sha256sum ./* >SHA256SUMS
  wc -c ./* >SIZES.txt
)

cat >"${backup_root}/README.txt" <<EOF
Project Luma migration backup ${stamp}

The release-assets directory contains chunks smaller than GitHub's 2 GiB
release-asset limit. Verify them with SHA256SUMS, concatenate parts in lexical
order, then extract the resulting .tar.zst archive as root on a Fedora lab host.

The VM archive contains the complete qcow2 backing chain, UEFI NVRAM, TPM state,
libvirt definitions, and relevant host configuration. The design VM was shut
down cleanly before capture and restarted after packaging.
EOF

cp "${backup_root}/README.txt" "${chunks_dir}/README.txt"
(
  cd "${backup_root}"
  sha256sum ./*.tar.zst >ARCHIVES.sha256
)
cp "${backup_root}/ARCHIVES.sha256" "${chunks_dir}/ARCHIVES.sha256"

echo "backup_root=${backup_root}"
du -h "${backup_root}"/*.tar.zst
cat "${chunks_dir}/SIZES.txt"
