#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Install a sealed refresh state as part of approved OS publication.

Run with CONTROL CONTROL_SHA STATE STATE_SHA CHANNEL --apply before the graph
flip, then the same arguments with --activate after public signature/readback
verification. Repeating this sequence replaces the previous approved state;
the old bootstrap job never runs alongside the replacement.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

def protected(path):
    for member in (path,*path.parents):
        entry=member.lstat()
        if entry.st_uid or entry.st_mode&0o022 or stat.S_ISLNK(entry.st_mode):
            raise ValueError('Unsafe administrator-owned deployment path')

def main():
    if os.getuid() or len(sys.argv)!=7:
        raise ValueError('Root-only usage: CONTROL CONTROL_SHA STATE STATE_SHA CHANNEL --apply|--activate')
    control,control_sha,state,state_sha,channel,mode=sys.argv[1:]
    if channel not in ('beta','nightly') or mode not in ('--apply','--activate'):
        raise ValueError('Unsupported graph refresh deployment')
    if any(not re.fullmatch('[a-f0-9]{64}',s) for s in (control_sha,state_sha)):
        raise ValueError('Missing administrator seal identity')
    control,state=Path(control),Path(state);protected(control);protected(state)
    helper=control/'scripts/depot/seal-signing-control.py'
    spec=importlib.util.spec_from_file_location('deploy_seal',helper)
    verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)
    verifier.verify(control,control_sha);verifier.verify(state,state_sha,'org.projectluma.signing-inputs/v1')
    cfg=json.loads((state/'approved.json').read_text())
    if channel not in cfg['channels']:raise ValueError('No admitted state for this channel')
    for value in (control,state,Path(cfg['pipeline_root']),Path(cfg['keys_root']),Path(cfg['storage_config'])):
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+',str(value)):raise ValueError('Unsupported unit path')
    command=['/usr/bin/python3','-B',str(control/'scripts/os/refresh-approved-graphs.py'),
             str(control),control_sha,str(state),state_sha,channel]
    name='luma-approved-os-graph-refresh-'+channel
    units=Path('/etc/systemd/system')
    service='\n'.join([
        '[Unit]','Description=Refresh the approved signed Luma '+channel+' OS graph',
        'Wants=network-online.target','After=network-online.target',
        'RequiresMountsFor='+cfg['pipeline_root']+' '+cfg['keys_root']+' '+cfg['storage_config'],
        '[Service]','Type=oneshot','UMask=0077','ExecStart='+' '.join(command),'TimeoutStartSec=5min',''])
    timer='\n'.join(['[Unit]','Description=Keep the signed Luma '+channel+' graph fresh',
        '[Timer]','OnCalendar=*-*-* 00,12:15:00 UTC','RandomizedDelaySec=5min','Persistent=true',
        'Unit='+name+'.service','[Install]','WantedBy=timers.target',''])
    if mode=='--apply':
        subprocess.run(['systemctl','disable','--now','luma-beta-os-graph-refresh.timer'],check=True)
        subprocess.run(['systemctl','stop','luma-beta-os-graph-refresh.service'],check=True)
        for suffix,text in (('.service',service),('.timer',timer)):
            target=units/(name+suffix);temporary=units/('.'+name+suffix+'.new')
            with temporary.open('w') as stream:
                stream.write(text);stream.flush();os.fsync(stream.fileno())
            temporary.chmod(0o644);temporary.replace(target)
        subprocess.run(['systemctl','daemon-reload'],check=True)
        subprocess.run(['systemctl','stop',name+'.timer',name+'.service'],check=True)
        print('Replacement installed but inactive; activate only after the approved public graph flip')
    else:
        if (units/(name+'.service')).read_text()!=service or (units/(name+'.timer')).read_text()!=timer:
            raise ValueError('Installed unit differs from this admitted state')
        subprocess.run(command+['--check'],check=True)
        subprocess.run(['systemctl','enable','--now',name+'.timer'],check=True)
        subprocess.run(['systemctl','start',name+'.service'],check=True)
        print('Approved '+channel+' graph refreshed and its 12-hour timer enabled')

if __name__=='__main__':main()
