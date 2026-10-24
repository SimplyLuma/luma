# SPDX-License-Identifier: Apache-2.0
# Phone hardware target lookups over lib/devices.tsv. Sourced by
# lib/common.sh, never executed. An unlisted target is refused; the empty
# target is the generic desktop and is never looked up here.

luma_os_devices_table="$(dirname -- "${BASH_SOURCE[0]}")/devices.tsv"

# luma_os_device_field TARGET FIELD
# FIELD is one of slug, arch, import, base_kind, required_labels, image_id.
luma_os_device_field() {
  local target=$1 field=$2 column value
  case "$field" in
    slug) column=2 ;;
    arch) column=3 ;;
    import) column=4 ;;
    base_kind) column=5 ;;
    required_labels) column=6 ;;
    image_id) column=7 ;;
    *) luma_os_die "unknown device field: $field" ;;
  esac
  [ -n "$target" ] || luma_os_die 'no hardware target given'
  value=$(awk -F '\t' -v target="$target" -v column="$column" \
    '$1 !~ /^#/ && $1 == target { print $column; found = 1; exit } END { exit !found }' \
    "$luma_os_devices_table") || luma_os_die "unsupported hardware target: $target"
  printf '%s\n' "$value"
}

# The target's channel ref: luma/<major>/<arch>/<slug>/<channel>.
luma_os_device_ref() {
  local target=$1 channel=$2 slug arch
  # A refused target stops the caller, as luma_os_die does.
  slug=$(luma_os_device_field "$target" slug) || exit 1
  arch=$(luma_os_device_field "$target" arch) || exit 1
  case " $LUMA_OS_CHANNELS " in
    *" $channel "*) ;;
    *) luma_os_die "unknown channel: $channel" ;;
  esac
  printf 'luma/%s/%s/%s/%s\n' "$LUMA_OS_MAJOR" "$arch" "$slug" "$channel"
}

# The target's release metadata namespace below <repo>/luma/.
luma_os_device_namespace() {
  local target=$1 slug arch
  # A refused target stops the caller, as luma_os_die does.
  slug=$(luma_os_device_field "$target" slug) || exit 1
  arch=$(luma_os_device_field "$target" arch) || exit 1
  printf 'targets/%s/%s\n' "$arch" "$slug"
}
