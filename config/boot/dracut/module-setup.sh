#!/usr/bin/bash
# SPDX-License-Identifier: MPL-2.0
#
# luma-boot-splash: build the initramfs with the splash setting this machine
# is about to have.
#
# luma-boot-splash-migrate moves an /etc/plymouth/plymouthd.conf that is still
# an earlier Luma default to the current one, early in the first boot of a
# deployment that ships a new default. On machines that regenerate their
# initramfs locally (rpm-ostree initramfs --enable), that initramfs was built
# before the helper ran, from the old /etc, so the first boot -- and every boot
# until the next update -- would still draw the old splash. This module asks
# the helper the same question at build time and, where it would replace the
# file, gives the initramfs the current default and its theme. It never
# touches /etc, and it does nothing for a file that is current, an
# administrator's own, or already reconciled once.

# called by dracut
check() {
    [[ -x ${dracutsysrootdir-}/usr/libexec/luma-boot-splash-migrate ]] || return 1
    return 0
}

# called by dracut
depends() {
    echo plymouth
}

pkglib_dir() {
    local _dir
    for _dir in /usr/libexec/plymouth /usr/lib/plymouth; do
        if [[ -x ${dracutsysrootdir-}$_dir/plymouth-populate-initrd ]]; then
            echo "$_dir"
            return
        fi
    done
}

# called by dracut
install() {
    local root=${dracutsysrootdir-} default theme pkglib
    default=$root/usr/share/luma/boot/plymouthd.conf

    LUMA_PLYMOUTHD_CONF=$root/etc/plymouth/plymouthd.conf \
        LUMA_PLYMOUTHD_DEFAULT=$default \
        LUMA_PLYMOUTHD_PREVIOUS=$root/usr/share/luma/boot/plymouthd.conf.previous \
        bash "$root/usr/libexec/luma-boot-splash-migrate" --check || return 0

    # The current default sets no theme of its own; the splash is then the one
    # Luma's plymouth selects in plymouthd.defaults.
    theme=$(sed -n 's/^[[:space:]]*Theme[[:space:]]*=[[:space:]]*//p' "$default" | tail -n 1)
    [[ -n $theme ]] || theme=$(sed -n 's/^[[:space:]]*Theme[[:space:]]*=[[:space:]]*//p' \
        "$root/usr/share/plymouth/plymouthd.defaults" 2>/dev/null | tail -n 1)
    pkglib=$(pkglib_dir)
    if [[ -z $theme || -z $pkglib || ! -f $root/usr/share/plymouth/themes/$theme/$theme.plymouth ]]; then
        dwarn "luma-boot-splash: cannot carry the current Luma splash ($theme); keeping the configured one"
        return 0
    fi

    dinfo "luma-boot-splash: /etc/plymouth/plymouthd.conf is an earlier Luma file; the initramfs carries $theme, as it will be once luma-boot-splash-migrate runs"
    PLYMOUTH_THEME_NAME=$theme PLYMOUTH_POPULATE_SOURCE_FUNCTIONS="$dracutfunctions" \
        "$root$pkglib"/plymouth-populate-initrd -t "$initdir" 2>/dev/null || {
        dwarn "luma-boot-splash: could not add $theme to the initramfs"
        return 0
    }
    # The whole current default, not only its Theme= line.
    mkdir -p "$initdir/etc/plymouth"
    cat "$default" >"$initdir/etc/plymouth/plymouthd.conf"
    chmod 0644 "$initdir/etc/plymouth/plymouthd.conf"
}
