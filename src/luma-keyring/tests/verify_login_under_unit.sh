#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
# The sign-in password check under the session unit's real settings (NoNewPrivileges=yes):
# direct PAM is refused (setuid unix_chkpwd cannot run under it), the luma-keyring-verify
# socket answers. Run in a throwaway VM as a user with sudo (it makes the account lkverify):
#   tests/verify_login_under_unit.sh /path/to/src/luma-keyring
# Expect, for the right password: direct PAM False, check_login_password True; for a wrong
# one: both False.
SRC=${1:?the luma-keyring source tree}
set -u
U=lkverify; PW='Verify-Me-77!'
[ -f /etc/pam.d/luma-keyring ] || sudo install -m644 ${SRC}/data/pam/luma-keyring /etc/pam.d/luma-keyring
id $U >/dev/null 2>&1 || sudo useradd -m --no-subordinate-ids $U 2>/dev/null || sudo useradd -m -K SUB_UID_COUNT=0 -K SUB_GID_COUNT=0 $U
echo "$U:$PW" | sudo chpasswd
UID_=$(id -u $U); sudo loginctl enable-linger $U; sleep 2
sudo rm -rf /home/$U/lk && sudo cp -r ${SRC} /home/$U/lk && sudo chown -R $U /home/$U/lk
sudo -u $U mkdir -p /home/$U/.config/systemd/user
printf '#!/bin/sh\nexec env PYTHONPATH=/home/%s/lk python3 -sP /home/%s/lk/bin/luma-keyring "$@"\n' $U $U | sudo tee /home/$U/lk/run >/dev/null; sudo chmod 755 /home/$U/lk/run
sed "s#/usr/libexec/luma-keyring#/home/$U/lk/run#" ${SRC}/data/systemd/luma-keyring-verify@.service | sudo -u $U tee /home/$U/.config/systemd/user/luma-keyring-verify@.service >/dev/null
sudo -u $U cp ${SRC}/data/systemd/luma-keyring-verify.socket /home/$U/.config/systemd/user/
E="XDG_RUNTIME_DIR=/run/user/$UID_ DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$UID_/bus"
sudo -u $U env $E systemctl --user daemon-reload
sudo -u $U env $E systemctl --user restart luma-keyring-verify.socket
ls -l /run/user/$UID_/luma-keyring/
CHECK='import sys; sys.path.insert(0, "/home/'$U'/lk"); from luma_keyring import login
import os
pw = sys.stdin.read()
print("NoNewPrivs:", login._no_new_privileges())
try: print("direct PAM:", login._check_with_pam(pw))
except Exception as e: print("direct PAM: unavailable", e)
print("check_login_password:", login.check_login_password(pw))'
for label in right wrong; do
  P=$PW; [ $label = wrong ] && P=not-it
  echo "== $label password, run as a unit with NoNewPrivileges=yes"
  printf '%s' "$P" | sudo -u $U env $E systemd-run --user --quiet --wait --pipe -p NoNewPrivileges=yes -p LimitCORE=0 python3 -c "$CHECK"
done
