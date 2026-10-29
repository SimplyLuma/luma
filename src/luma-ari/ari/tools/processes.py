# SPDX-License-Identifier: Apache-2.0
"""Exact same-user process control, pinned by pidfd and process start time."""
import os
from pathlib import Path
import secrets
import select
import signal
import sys
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ari.mcp import serve

_targets = {}


def _identity(pid):
    root = Path('/proc')/str(pid)
    raw = (root/'stat').read_text()
    values = raw.rsplit(')',1)[1].split()
    return {'pid':pid,'uid':root.stat().st_uid,'start':values[19],
            'parent':int(values[1]),'name':raw.split('(',1)[1].rsplit(')',1)[0],'state':values[0]}


def _ancestors():
    protected = {1,2,os.getpid()}
    pid = os.getpid()
    while pid > 1:
        try: pid = _identity(pid)['parent']
        except (OSError,ValueError,IndexError): break
        if pid in protected: break
        protected.add(pid)
    return protected


def find_processes(arguments):
    query = arguments['query'].strip().casefold()
    if not query: raise ValueError('Name the process or give its PID.')
    now = time.monotonic()
    for key,row in list(_targets.items()):
        if now-row['found']>180: del _targets[key]
    while len(_targets)>472:
        del _targets[next(iter(_targets))]
    protected = _ancestors()
    rows = []
    for root in Path('/proc').iterdir():
        if not root.name.isdecimal(): continue
        try: row = _identity(int(root.name))
        except (OSError,ValueError,IndexError): continue
        if (row['uid'] != os.getuid() or row['pid'] in protected or row['state']=='Z'
                or not (str(row['pid'])==query or query in row['name'].casefold())): continue
        key = secrets.token_urlsafe(18)
        _targets[key] = {**row,'found':now,'query':query}
        rows.append({'process':key,'pid':row['pid'],'name':row['name']})
        if len(rows)>=40: break
    return {'ok':True,'summary':f'Found {len(rows)} matching processes owned by you.',
            'data':{'processes':rows},'untrusted':True}


def control_process(arguments):
    row = _targets.get(arguments['process'])
    if row is None or time.monotonic()-row['found']>180:
        raise ValueError('Find this process again before changing it.')
    action = arguments.get('action','end')
    if action not in ('end','kill'): raise ValueError('Choose end or kill.')
    current = _identity(row['pid'])
    if (current['uid']!=os.getuid() or current['pid'] in _ancestors()
            or any(current[key]!=row[key] for key in ('start','uid','name'))):
        raise ValueError('The process changed. Find it again.')
    description = f'{row["name"]} (PID {row["pid"]})'
    if arguments.get('preview'):
        return {'ok':True,'preview':f'{"Kill" if action=="kill" else "End"} {description}',
                'detail':'Force termination can discard this process’s unsaved work.' if action=='kill' else 'Ask this process to exit.',
                'requested_name':row['query']}
    if not hasattr(os,'pidfd_open') or not hasattr(signal,'pidfd_send_signal'):
        raise ValueError('This system cannot safely control the selected process.')
    descriptor = os.pidfd_open(row['pid'],0)
    try:
        again = _identity(row['pid'])
        if any(again[key]!=current[key] for key in ('start','uid','name')):
            raise ValueError('The process changed before the signal. Find it again.')
        signal.pidfd_send_signal(descriptor,signal.SIGKILL if action=='kill' else signal.SIGTERM,None,0)
        poller=select.poll();poller.register(descriptor,select.POLLIN)
        exited=bool(poller.poll(1000))
    finally: os.close(descriptor)
    del _targets[arguments['process']]
    return {'ok':True,'untrusted':True,'summary':(f'{description} exited.' if exited else f'Sent {action} to {description}; it is still running.'),
            'data':{'pid':row['pid'],'exited':exited,'signal':'SIGKILL' if action=='kill' else 'SIGTERM'}}


TOOLS = {
 'find_processes':('Find this user’s processes by name or exact PID. Read the returned identities; ask which one if ambiguous. Do not guess which process “this” means.',
  {'type':'object','properties':{'query':{'type':'string'}},'required':['query'],'additionalProperties':False},find_processes),
 'control_process':('End (SIGTERM) or kill (SIGKILL) the exact process selected from find_processes. Kill only when explicitly requested. Reports whether it actually exited; never infer exit from sending a signal.',
  {'type':'object','properties':{'process':{'type':'string'},'action':{'type':'string','enum':['end','kill']},'preview':{'type':'boolean'}},
   'required':['process','action'],'additionalProperties':False},control_process),
}
if __name__=='__main__':serve('ari-processes',TOOLS)
