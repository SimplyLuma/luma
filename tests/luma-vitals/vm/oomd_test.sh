#!/bin/bash
# Runs as luma inside the VM. A runaway app in app.slice fills memory and zram swap;
# systemd-oomd must kill it (and only it); a bystander app and a session service stay.
set -u
cd /tmp
systemctl --user daemon-reload
systemctl --user reset-failed 2>/dev/null
run() { systemd-run --user --quiet --collect --slice="$1" --unit="$2" -p Type=exec -- python3 /tmp/leak.py "$3" "$4"; }
run app.slice app-gnome-org.example.Bystander-100.service 50 150
run session.slice org.example.SessionService.service 50 150
sleep 8
start=$(date +%s)
run app.slice app-gnome-org.example.Leak-200.service 120 0
for i in $(seq 1 240); do
  sleep 1
  systemctl --user -q is-active app-gnome-org.example.Leak-200.service || break
done
end=$(date +%s)
echo "runaway ended after $((end-start)) s"
echo "leak: $(systemctl --user show -p ActiveState -p Result app-gnome-org.example.Leak-200.service | tr '\n' ' ')"
echo "bystander: $(systemctl --user is-active app-gnome-org.example.Bystander-100.service)"
echo "session service: $(systemctl --user is-active org.example.SessionService.service)"
echo "user manager: $(systemctl is-active user@$(id -u).service)"
sudo journalctl -u systemd-oomd -S "@$start" --no-pager -o cat | grep -iE "kill|swap" | head -5
echo "kernel oom kills: $(sudo journalctl -k -S "@$start" --no-pager | grep -c 'Out of memory')"
systemctl --user stop app-gnome-org.example.Bystander-100.service org.example.SessionService.service 2>/dev/null
systemctl --user reset-failed 2>/dev/null
