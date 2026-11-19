---
type: fix
component: Phone
highlight: false
---
Summary: The firewall runs on the Fairphone 6
Details: The Fairphone 6 kernel builds the nftables and connection-tracking modules firewalld needs, so firewalld loads its rules instead of stopping and leaving the phone without a firewall. Needs the new boot image and kernel-modules-luma-fp6 7.1.2-1.luma.ims1.gmu2.selinux1.8.
