# SPDX-License-Identifier: Apache-2.0
"""Read-only system facts and classification for MONITOR's resource views."""
from pathlib import Path
from .model import read, mount_unescape
from .v70_data import format_value


def process_groups(processes, app_pids, users):
    buckets={'sys':[],'bg':[],'kern':[]}
    for p in processes:
        if p.pid in app_pids:continue
        key='kern' if p.pid==2 or p.ppid==2 else 'sys' if p.name in {
            'gnome-shell','mutter','luma-session','luma-stage','luma-grid',
            'pipewire','pipewire-pulse','wireplumber','NetworkManager',
            'dbus-broker','dbus-daemon','xdg-desktop-portal',
        } or p.name.startswith(('gsd-','xdg-desktop-portal-')) else 'bg'
        buckets[key].append([p.name,p.pid,users.get(p.uid,str(p.uid)),p.cpu,
                             p.rss/1024**2 if p.rss is not None else None,p.threads])
    names={'sys':('Luma system','The desktop, sound, networking and the other parts that make Luma run'),
           'bg':('Background services','Helpers that start on their own: indexing, updates, printing, sync'),
           'kern':('Linux kernel','Work the kernel does itself. These can’t be ended')}
    return [dict(id=k,n=names[k][0],why=names[k][1],p=v,total=len(v),kernel=k=='kern') for k,v in buckets.items()]


def resource_facts(snapshot, *, proc='/proc', sys='/sys'):
    """Unavailable measurements stay explicit; files are read, never created or changed."""
    proc=Path(proc);sys=Path(sys);mem=snapshot['memory'];battery=snapshot.get('battery') or {}
    memory=lambda key:format_value(mem[key]/1024**2,'mem') if key in mem else '—'
    swap=(format_value(max(0,mem['SwapTotal']-mem['SwapFree'])/1024**2,'mem')+' of '+memory('SwapTotal')) if {'SwapTotal','SwapFree'}<=mem.keys() else '—'
    zram=[]
    for path in sorted((sys/'block').glob('zram*/mm_stat')):
        try:
            parts=(read(path) or '').split();zram.append((int(parts[0]),int(parts[1])))
        except (ValueError,IndexError):continue
    compressed=(format_value(sum(a for a,b in zram)/1024**2,'mem')+' → '+format_value(sum(b for a,b in zram)/1024**2,'mem')) if zram else '—'
    root=device=queue='—'
    for line in (read(proc/'self/mountinfo') or '').splitlines():
        try:
            before,after=line.split(' - ',1);parts=before.split()
            if mount_unescape(parts[4])!='/':continue
            root=after.split()[0]+' · /';block=(sys/'dev/block'/parts[2])
            if block.exists():
                resolved=block.resolve();device=resolved.name
                values=(read(resolved/'inflight') or '').split()
                if values:queue=str(sum(int(v) for v in values))
            break
        except (ValueError,IndexError,OSError):continue
    interface='—'
    for line in (read(proc/'net/route') or '').splitlines()[1:]:
        parts=line.split()
        if len(parts)>3 and parts[1]=='00000000':interface=parts[0];break
    link=packets='—'
    if interface!='—':
        speed=read(sys/'class/net'/interface/'speed')
        try:
            rate=int(speed or '')
            if rate>0:link=f'{rate/1000:g} Gb/s' if rate>=1000 else f'{rate} Mb/s'
        except ValueError:pass
        for line in (read(proc/'net/dev') or '').splitlines():
            if ':' not in line:continue
            name,stats=line.split(':',1)
            if name.strip()!=interface:continue
            try:
                parts=stats.split();packets=f'{int(parts[1]):,} in · {int(parts[9]):,} out'
            except (ValueError,IndexError):pass
            break
    draw=battery.get('draw');health=battery.get('health');cycles=battery.get('cycles')
    return {'mem':[('Cached',memory('Cached')),('Swap',swap),('zram',compressed),('Committed',memory('Committed_AS'))],
            'disk':[('Device',device),('Root',root),('Queue',queue),('Busy','—')],
            'net':[('Interface',interface),('Address','—'),('Link',link),('Packets',packets)],
            'en':[('Draw',f'{draw:.1f} W' if draw is not None else '—'),('Profile','—'),('Health',f'{health:g}%' if health is not None else '—'),('Cycles',str(cycles) if cycles is not None and cycles>=0 else '—')]}
