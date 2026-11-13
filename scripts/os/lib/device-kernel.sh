# SPDX-License-Identifier: Apache-2.0
# Used only inside a disposable device image build, never on the build host.
luma_replace_device_kernel() {
  local replacement=${1:?exact admitted kernel RPM required} inventory name nevra
  local -a remove=()
  inventory=$(rpm -qa --qf '%{NAME}\t%{NEVRA}\n') || return
  while IFS=$'\t' read -r name nevra; do
    case "$name" in
      kernel|kernel-core|kernel-modules|kernel-modules-core|kernel-modules-extra|kernel-modules-internal|kernel-uki-dtbloader|kernel-uki-virt)
        [ -n "$nevra" ] || return 1
        remove+=("$nevra") ;;
    esac
  done <<< "$inventory"
  [ "${#remove[@]}" -gt 0 ] || { echo 'No base kernel packages found' >&2; return 1; }
  # Removing only kernel-core allows a Fedora UKI to satisfy the remaining
  # stock kernel packages. Explicitly replace the complete installed family
  # in ONE dependency-checked transaction. Keep userspace headers/tools and
  # unrelated dependencies. The admitted local RPM needs no network solver.
  dnf5 -y --disablerepo='*' --setopt=clean_requirements_on_remove=False do \
    --type=package --action=remove "${remove[@]}" --type=auto --action=install "$replacement"
}
