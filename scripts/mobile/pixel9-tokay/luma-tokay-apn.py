#!/usr/bin/python3
"""Pixel 9 (tokay): point the data mode at the SIM's own APN.

Hook 82 prepares /run/luma-fold-mobile/data with the Fold's APN (tello).
Wait for ModemManager's SIM, look its MCC/MNC up in
mobile-broadband-provider-info and rewrite the prepared data-mode files, so
luma-fold-mobile-mode attaches on the right APN when data mode is chosen.
"""
import json, os, subprocess, sys, time
import xml.etree.ElementTree as E

D = "/run/luma-fold-mobile/data"
MBPI = "/usr/share/mobile-broadband-provider-info/serviceproviders.xml"

def log(m):
    print(f"luma-tokay-apn: {m}", flush=True)

def sim_code():
    for _ in range(60):
        r = subprocess.run(["mmcli", "-i", "any", "-K"], capture_output=True, text=True)
        for line in r.stdout.splitlines():
            k, _, v = line.partition(":")
            if k.strip() == "sim.properties.operator-code" and v.strip() not in ("", "--"):
                return v.strip()
        time.sleep(3)
    return None

def apn_for(code):
    for p in E.parse(MBPI).iter("provider"):
        if code in [n.get("mcc") + n.get("mnc") for n in p.iter("network-id")]:
            for a in p.iter("apn"):
                if any("internet" in (u.get("type") or "") for u in a.iter("usage")):
                    return p.findtext("name"), a.get("value").lower()
    return None, None

code = sim_code()
if not code:
    log("no SIM operator code"); sys.exit(0)
name, apn = apn_for(code)
old = open(f"{D}/apn").read().strip() if os.path.exists(f"{D}/apn") else ""
if not apn or apn == old:
    log(f"SIM {code}: keeping {old or 'nothing'}"); sys.exit(0)
for f in os.listdir(D):
    p = f"{D}/{f}"
    if os.path.isfile(p):
        s = open(p).read()
        if old in s:
            open(p, "w").write(s.replace(old, apn))
if os.path.exists(f"{D}/{old}"):
    os.rename(f"{D}/{old}", f"{D}/{apn}")
log(f"SIM {code} ({name}): data APN {old} -> {apn}")
