# SPDX-License-Identifier: Apache-2.0
# shellcheck shell=sh
#
# Choose the OSTree deployment to boot, for initramfs on devices whose
# bootloader does not read ostree's Boot Loader Specification entries (Android
# bootloaders, U-Boot without BLS). Sourced by busybox ash; installed in the
# initramfs as /usr/lib/luma/ostree-boot.sh.
#
# Default: the entry in /boot/loader/entries with the highest "version". ostree
# writes n - index there, so this is the first deployment in `ostree admin
# status`. `rpm-ostree rollback` and finalized upgrades reorder the entries;
# nothing else is needed for them to take effect.
#
# Fallback: greenboot's boot counting in /boot/grub2/grubenv, the same rule as
# greenboot's GRUB snippet 08_greenboot.cfg. With boot_counter set and
# boot_success=0 the counter goes down by one each boot; at 0 (or -1) the
# second entry boots and the counter stays -1 until greenboot's health check
# makes the fallback permanent (rpm-ostree rollback). boot_success is reset to
# 0 on every boot. greenboot arms the counter when it finalizes a staged
# deployment and clears it once the new deployment is healthy.
#
# A deployment whose tree is missing, or lacks systemd or /etc, is skipped in
# favour of the next entry.
#
# luma_ostree_resolve ROOT
#   ROOT is the mounted physical root (the filesystem holding /ostree and
#   /boot). Sets LUMA_OSTREE_TARGET (the ostree= value of the chosen entry),
#   LUMA_OSTREE_DEPLOY (deploy/STATEROOT/deploy/CHECKSUM.SERIAL, relative to
#   /ostree), LUMA_OSTREE_INDEX (0 = default) and LUMA_OSTREE_REASON. Returns 1
#   when there is no usable entry; the caller then keeps its old behaviour.
#   LUMA_OSTREE_REQUIRE adds paths (relative to the deployment) that must exist.
#   The result is also written to LUMA_OSTREE_STATE (/run/luma/ostree-boot),
#   which outlives the switch to the real root.
#
# luma_ostree_apply ROOT CMDLINE
#   Points the aboot slot link that ostree-prepare-root and
#   ostree-system-generator follow (/ostree/root.a or root.b, chosen from
#   androidboot.* exactly as libostree does) at the chosen deployment, and sets
#   LUMA_OSTREE_CMDLINE to CMDLINE with ostree= replaced by the chosen entry's.

LUMA_OSTREE_LOG="${LUMA_OSTREE_LOG:-/dev/kmsg}"
LUMA_OSTREE_STATE="${LUMA_OSTREE_STATE:-/run/luma/ostree-boot}"

luma_ostree_log() {
	echo "luma-ostree: $*" >>"$LUMA_OSTREE_LOG" 2>/dev/null || echo "luma-ostree: $*" >&2
}

# Print "VERSION<TAB>TARGET" for every entry of the current boot version,
# default first.
luma_ostree_entries() {
	local root loader dir f version target tab
	root="$1"
	tab="$(printf '\t')"
	if [ -L "$root/boot/loader" ]; then
		loader="$(readlink "$root/boot/loader")" || return 1
	else
		loader=loader
	fi
	case "$loader" in
		loader|loader.0|loader.1) ;;
		*) return 1 ;;
	esac
	dir="$root/boot/$loader/entries"
	[ -d "$dir" ] || return 1
	for f in "$dir"/*.conf; do
		[ -f "$f" ] || continue
		version="$(sed -n 's/^version[[:space:]][[:space:]]*\([^[:space:]]*\).*/\1/p' "$f" | head -n 1)"
		target="$(sed -n 's/^options[[:space:]]//p' "$f" | tr '\t' ' ' | tr ' ' '\n' | sed -n 's/^ostree=//p' | tail -n 1)"
		case "$version" in ''|*[!0-9]*) continue ;; esac
		case "$target" in
			/ostree/boot.[01]/*/*/[0-9]*) ;;
			*) continue ;;
		esac
		case "$target" in *..*|*"$tab"*) continue ;; esac
		printf '%s\t%s\n' "$version" "$target"
	done | sort -t "$tab" -k1,1nr
}

# Print the deployment a boot link resolves to, relative to /ostree.
luma_ostree_deploy_of() {
	local root target link
	root="$1"; target="$2"
	link="$(readlink "$root$target" 2>/dev/null)" || return 1
	link="${link#../../../}"
	case "$link" in
		deploy/*/deploy/*.[0-9]*) ;;
		*) return 1 ;;
	esac
	case "$link" in *..*) return 1 ;; esac
	printf '%s\n' "$link"
}

luma_ostree_usable() {
	local dir path
	dir="$1"
	[ -d "$dir" ] && [ -d "$dir/etc" ] && [ -x "$dir/usr/lib/systemd/systemd" ] || return 1
	for path in ${LUMA_OSTREE_REQUIRE:-}; do
		[ -e "$dir/$path" ] || return 1
	done
}

luma_grubenv_get() {
	sed -n "s/^$2=//p" "$1" 2>/dev/null | head -n 1
}

# luma_grubenv_set FILE KEY=VALUE...: rewrite a GRUB environment block (1024
# bytes, '#' padding) with the given keys replaced.
luma_grubenv_set() {
	local file body kv content pad tmp
	file="$1"; shift
	body="$(sed -e '/^#/d' -e '/^$/d' "$file" 2>/dev/null)"
	for kv; do
		body="$(printf '%s\n' "$body" | grep -v "^${kv%%=*}=")"
		body="$body
$kv"
	done
	content="# GRUB Environment Block
$(printf '%s\n' "$body" | sed '/^$/d')
"
	pad=$((1024 - ${#content}))
	[ "$pad" -ge 0 ] || return 1
	tmp="$file.luma-new"
	{ printf '%s' "$content"; head -c "$pad" /dev/zero | tr '\0' '#'; } >"$tmp" || return 1
	if [ -L "$file" ]; then
		cat "$tmp" >"$file" && rm -f "$tmp"
	else
		mv -f "$tmp" "$file"
	fi
}

# Apply greenboot's boot counting. Sets LUMA_OSTREE_START to the entry index
# to try first.
luma_ostree_count() {
	local env counter success
	env="$1/boot/grub2/grubenv"
	LUMA_OSTREE_START=0
	[ -f "$env" ] || return 0
	counter="$(luma_grubenv_get "$env" boot_counter)"
	success="$(luma_grubenv_get "$env" boot_success)"
	if [ -n "$counter" ] && [ "$success" = 0 ]; then
		case "$counter" in
			0|-1) LUMA_OSTREE_START=1; counter=-1 ;;
			*[!0-9]*) ;;
			*) counter=$((counter - 1)) ;;
		esac
		luma_grubenv_set "$env" "boot_counter=$counter" boot_success=0 ||
			luma_ostree_log "cannot update $env"
	elif [ "$success" != 0 ]; then
		luma_grubenv_set "$env" boot_success=0 || luma_ostree_log "cannot update $env"
	fi
}

luma_ostree_resolve() {
	local root entries count order index line target deploy skipped tab
	root="$1"
	tab="$(printf '\t')"
	LUMA_OSTREE_TARGET=; LUMA_OSTREE_DEPLOY=; LUMA_OSTREE_INDEX=; LUMA_OSTREE_REASON=
	entries="$(luma_ostree_entries "$root")" || return 1
	[ -n "$entries" ] || return 1
	count="$(printf '%s\n' "$entries" | grep -c .)"
	luma_ostree_count "$root"
	# greenboot fallback: the second entry first, then the default, then the rest.
	if [ "$LUMA_OSTREE_START" = 1 ] && [ "$count" -gt 1 ]; then
		order="1 0"
	else
		order="0 1"
	fi
	index=2
	while [ "$index" -lt "$count" ]; do
		order="$order $index"
		index=$((index + 1))
	done
	[ "$count" -gt 1 ] || order=0
	skipped=
	for index in $order; do
		line="$(printf '%s\n' "$entries" | sed -n "$((index + 1))p")"
		target="${line#*"$tab"}"
		if deploy="$(luma_ostree_deploy_of "$root" "$target")" && luma_ostree_usable "$root/ostree/$deploy"; then
			LUMA_OSTREE_TARGET="$target"
			LUMA_OSTREE_DEPLOY="$deploy"
			LUMA_OSTREE_INDEX="$index"
			if [ -n "$skipped" ]; then
				LUMA_OSTREE_REASON="skipped unusable:$skipped"
			elif [ "$LUMA_OSTREE_START" = 1 ] && [ "$index" = 1 ]; then
				LUMA_OSTREE_REASON="greenboot fallback"
			else
				LUMA_OSTREE_REASON=default
			fi
			luma_ostree_log "booting entry $index of $count, $target -> $deploy ($LUMA_OSTREE_REASON)"
			mkdir -p "${LUMA_OSTREE_STATE%/*}" 2>/dev/null &&
				printf 'index=%s\nentries=%s\ntarget=%s\ndeploy=%s\nreason=%s\n' "$index" "$count" \
					"$target" "$deploy" "$LUMA_OSTREE_REASON" >"$LUMA_OSTREE_STATE" 2>/dev/null
			return 0
		fi
		skipped="$skipped $target"
		luma_ostree_log "entry $index ($target) is not a usable deployment"
	done
	luma_ostree_log "no usable deployment among $count entries"
	return 1
}

luma_ostree_apply() {
	local root cmdline word slot android link current out
	root="$1"; cmdline="$2"
	[ -n "$LUMA_OSTREE_DEPLOY" ] && [ -n "$LUMA_OSTREE_TARGET" ] || return 1
	slot=; android=; out=
	set -f
	for word in $cmdline; do
		case "$word" in
			androidboot.slot_suffix=_a) slot=a ;;
			androidboot.slot_suffix=_b) slot=b ;;
			androidboot.slot_suffix=*) set +f; return 1 ;;
			androidboot.*) android=1 ;;
		esac
		case "$word" in
			ostree=*) ;;
			*) out="${out:+$out }$word" ;;
		esac
	done
	set +f
	[ -n "$slot" ] || [ -z "$android" ] || slot=a
	LUMA_OSTREE_CMDLINE="${out:+$out }ostree=$LUMA_OSTREE_TARGET"
	[ -n "$slot" ] || return 0
	link="$root/ostree/root.$slot"
	current="$(readlink "$link" 2>/dev/null)" || current=
	[ "$current" != "$LUMA_OSTREE_DEPLOY" ] || return 0
	# Not atomic, and need not be: the next boot rewrites the link from the
	# boot entries whether or not it exists.
	rm -f "$link" && ln -s "$LUMA_OSTREE_DEPLOY" "$link" || return 1
	sync
	luma_ostree_log "root.$slot -> $LUMA_OSTREE_DEPLOY (was ${current:-unset})"
}
