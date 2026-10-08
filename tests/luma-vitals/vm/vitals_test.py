"""Vitals acting for real: a leaking autostarted agent is restarted through the user manager; an app is not."""
import os, subprocess, sys, time
sys.path.insert(0, "/tmp/vitals-src")
from luma_vitals import agents, sampler
agents.GROWTH_WINDOW_SECONDS = 60          # compressed time for the test
agents.GROWTH_MIN_BYTES = 64 * agents.MIB
agents.LARGEST_BYTES = 256 * agents.MIB
def start(unit, rate, limit):
    subprocess.run(["systemd-run", "--user", "--quiet", "--collect", "--slice=app.slice", f"--unit={unit}",
                    "-p", "Type=exec", "--", "python3", "/tmp/leak.py", str(rate), str(limit)], check=True)
start("app-gnome-org.example.Agent-4242.service", 8, 700)
start("app-gnome-org.example.Browser-4243.service", 8, 700)
watch = agents.RunawayAgents()
events = []
t0 = time.time()
while time.time() - t0 < 150 and not any(e.kind == "agent-restarted" for e in events):
    events += watch.observe(sampler.machine())
    time.sleep(5)
for e in events:
    print(e.kind, e.unit, e.summary)
units = subprocess.run(["systemctl", "--user", "list-units", "--no-legend", "--plain", "app-gnome-org.example.*"],
                       capture_output=True, text=True).stdout
print(units)
ok = (any(e.kind == "agent-restarted" and "Agent-4242" in e.unit for e in events)
      and "Browser-4243.service" in units and "Agent-4242.service" not in units
      and "app-gnome-org.example.Agent-" in units)
subprocess.run("systemctl --user stop 'app-gnome-org.example.*'", shell=True)
print("VITALS-ACTION", "PASS" if ok else "FAIL")
