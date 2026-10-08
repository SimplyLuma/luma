#!/bin/bash
# A luma-background-style agent unit (limits as luma-background writes them) that leaks is
# stopped at its memory limit and restarted cleanly.
set -u
systemd-run --user --quiet --slice=luma-background.slice --unit=luma-background-test-agent.service \
  -p Type=exec -p Restart=on-failure -p RestartSec=2s -p MemoryHigh=64M -p MemoryMax=128M -p MemorySwapMax=64M \
  -- python3 /tmp/leak.py 40 0
for i in $(seq 1 60); do
  sleep 1
  n=$(systemctl --user show -p NRestarts --value luma-background-test-agent.service)
  [ "${n:-0}" -ge 2 ] && break
done
echo "agent restarts: $(systemctl --user show -p NRestarts --value luma-background-test-agent.service)"
echo "agent state: $(systemctl --user is-active luma-background-test-agent.service)"
echo "agent oom result: $(systemctl --user show -p Result --value luma-background-test-agent.service)"
journalctl --user -u luma-background-test-agent.service --no-pager -o cat | grep -iE "oom|memory" | head -3
systemctl --user stop luma-background-test-agent.service; systemctl --user reset-failed 2>/dev/null
