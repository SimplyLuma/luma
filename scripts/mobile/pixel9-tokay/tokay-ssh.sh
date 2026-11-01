#!/bin/sh
# Root shell on the Pixel 9 running Luma (hook 79 key-only sshd). The Fold also
# sits on 172.16.42.1, so go over the Pixel 9's own USB NCM link (macOS hardware
# port "Pixel 9") by IPv6 link-local.
K=$HOME/.ssh/luma_tokay_known_hosts
IF=$(networksetup -listallhardwareports | awk '/^Hardware Port: Pixel 9$/{getline; print $2}')
[ -n "$IF" ] || { echo "Pixel 9 USB link not found" >&2; exit 1; }
A=$(ping6 -c2 -I $IF ff02::1 2>/dev/null | awk '/bytes from/{sub(/[:,]+$/,"",$4); print $4}' | grep -v "$(ifconfig $IF | awk '/inet6 fe80/{split($2,a,"%"); print a[1]}')" | head -1)
[ -n "$A" ] || { echo "Pixel 9 not answering on $IF" >&2; exit 1; }
exec ssh -o BatchMode=yes -o ConnectTimeout=5 -o IdentitiesOnly=yes -i $HOME/.ssh/luma_lab_ed25519 -o UserKnownHostsFile=$K -o StrictHostKeyChecking=accept-new -o HostKeyAlias=luma-tokay -o ProxyCommand="nc $(echo $A | sed s/%/%%/) 22" root@luma-tokay "$@"
