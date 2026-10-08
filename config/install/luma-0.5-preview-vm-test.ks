# Unattended VM test only: never referenced from the boot menu.
text
lang en_US.UTF-8
keyboard --vckeymap=us --xlayouts=us
timezone America/Chicago --utc
ostreesetup --nogpg --osname=fedora --remote=luma --url=file:///run/install/repo/luma-repo --ref=luma/0.5/x86_64/desktop-preview
firewall --use-system-defaults
rootpw --lock
user --name=tester --groups=wheel --plaintext --password=luma-vm-test
zerombr
clearpart --all --initlabel
autopart --type=btrfs
bootloader --location=mbr
poweroff
%post --erroronfail
cp /etc/skel/.bash* /root
# Luma always starts at its login screen, however the install was driven.
systemctl set-default graphical.target
%end
