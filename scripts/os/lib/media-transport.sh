# SPDX-License-Identifier: Apache-2.0
# Transport aliases retain the original ISO identity without duplicating bytes.
# The origin serves .iso.download reliably; metadata keeps the saved .iso name.
luma_media_transport_link() {
  local iso=$1 alias=$2 temporary
  [ -s "$iso" ] && [[ "$alias" = *.iso.download ]] || return 1
  temporary="$alias.new"
  ln -f -- "$iso" "$temporary" && mv -f -- "$temporary" "$alias"
}
luma_media_transport_retire() {
  local iso=$1
  [[ "$iso" = *.iso ]] || return 1
  rm -f -- "$iso" "$iso.download"
}
