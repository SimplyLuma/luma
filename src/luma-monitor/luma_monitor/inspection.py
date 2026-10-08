# SPDX-License-Identifier: Apache-2.0
"""Read-only procfs inspection with PID/start-time validation on both sides."""
from pathlib import Path
import os
from .model import stat_record


def validate(process, proc='/proc'):
    root=Path(proc)/str(process.pid)
    record=stat_record((root/'stat').read_text())
    if record['start']!=process.start:
        raise ProcessLookupError('The selected process has exited')
    if process.cgroup:
        if not any(line.endswith(':'+process.cgroup) for line in (root/'cgroup').read_text().splitlines()):
            raise ProcessLookupError('The selected process changed groups')
    return root,record


def inspect_table(process, kind, proc='/proc'):
    if proc == '/proc' and Path('/.flatpak-info').exists():
        import json
        from gi.repository import GLib
        from .host_sampler import host_call
        return json.loads(host_call('Inspect', GLib.Variant('(uts)', (process.pid, process.start, kind)), '(s)')[0])
    if kind not in ('files','maps'):
        raise ValueError('Unknown inspection kind')
    root,_=validate(process,proc)
    rows=[]
    if kind=='files':
        columns=['FD','Type','Object']
        for fd in sorted((root/'fd').iterdir(),key=lambda p:int(p.name)):
            try:target=os.readlink(fd)
            except FileNotFoundError:continue
            type_='socket' if target.startswith('socket:') else 'pipe' if target.startswith('pipe:') else 'memfd' if 'memfd:' in target else 'file'
            rows.append([fd.name,type_,target])
    else:
        columns=['Address','Flags','Size','File']
        for line in (root/'maps').read_text().splitlines():
            parts=line.split(None,5)
            if len(parts)<5:raise ValueError('Malformed memory map')
            start,end=parts[0].split('-',1)
            size=int(end,16)-int(start,16)
            if size<0:raise ValueError('Malformed memory map address')
            value=f'{size/1024**2:.1f} MB' if size>=1024**2 else f'{size/1024:.0f} KB' if size>=1024 else f'{size} B'
            rows.append([start,parts[1],value,parts[5] if len(parts)>5 else ''])
    validate(process,proc)
    return columns,rows


def inspect_properties(process, proc='/proc'):
    """Inspect only the sampled identity, never a replacement that reused its PID."""
    if proc == '/proc' and Path('/.flatpak-info').exists():
        import json
        from gi.repository import GLib
        from .host_sampler import host_call
        return json.loads(host_call('Inspect', GLib.Variant('(uts)', (process.pid, process.start, 'properties')), '(s)')[0])
    import pwd
    import shlex
    import time
    from datetime import datetime
    from .model import fields, number
    from .v70_data import format_value
    root,record=validate(process,proc)
    status=fields((root/'status').read_text(errors='replace'))
    command=(root/'cmdline').read_bytes().decode('utf-8',errors='replace').rstrip('\0').split('\0')
    uid=number(status.get('Uid'))
    try:user=pwd.getpwuid(uid).pw_name if uid is not None else '—'
    except KeyError:user=str(uid)
    rss=number(status.get('VmRSS'));file=number(status.get('RssFile'));shared=number(status.get('RssShmem'))
    memory=format_value(rss/1024 if rss is not None else None,'mem')+' resident'
    if file is not None and shared is not None:
        total=file+shared;memory+=' · '+(f'{total} KB' if total<1024 else format_value(total/1024,'mem'))+' shared'
    hz=os.sysconf('SC_CLK_TCK');seconds=round(record['ticks']/hz)
    stat_text=(root/'stat').read_text();tail=stat_text[stat_text.rfind(')')+2:].split();nice=int(tail[16])
    age=getattr(process,'age',None)
    started=datetime.fromtimestamp(time.time()-age).strftime('%b %-d at %-I:%M %p') if age is not None else '—'
    facts=[('Status',status.get('State','—')),('Main process',f"{record['comm']} · PID {process.pid}"),
           ('User',user),('Started',started),('CPU time',f'{seconds//60} min {seconds%60} s'),
           ('Memory',memory),('Priority',f'nice {nice}'),('Threads',number(status.get('Threads')) or '—'),
           ('Sandbox','Flatpak' if 'flatpak' in process.cgroup else '—'),
           ('Command line',shlex.join(command) if command!=[''] else '['+record['comm']+']'),
           ('Control group',process.cgroup or '—')]
    validate(process,proc)
    return facts
