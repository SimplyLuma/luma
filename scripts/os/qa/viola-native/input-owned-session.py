#!/usr/bin/python3
"""Diagnostic input into the fresh QA user's exact parent Shell only.

This intentionally does not weaken private_input.py's private-bus guard. The
ordinary-user-manager browser fixture has a different bus. Its disposable UID,
exact headless automation parent and private display are pinned here instead.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import stat
import time

def identity(pid):
    root=Path('/proc')/str(pid)
    return (root.stat().st_uid,root.joinpath('stat').read_text().rsplit(')',1)[1].split()[19],
            root.joinpath('cmdline').read_bytes().split(b'\0')[:-1])

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('click','key-a'))
    parser.add_argument('--x',type=int)
    parser.add_argument('--y',type=int)
    args=parser.parse_args()
    uid=os.getuid();parent=os.getppid();user=pwd.getpwnam('luma-gate-browser-opens')
    assert os.geteuid()==uid==user.pw_uid and uid>=1000
    expected=identity(parent)
    assert expected[0]==uid and expected[2][0]==b'gnome-shell'
    display=os.environ.get('WAYLAND_DISPLAY','')
    assert re.fullmatch(r'viola-qa-shm-r[1-9][0-9]*',display)
    assert ('--wayland-display='+display).encode() in expected[2]
    actors=[v.decode() for v in expected[2] if v.startswith(b'--automation-script=')]
    assert len(actors)==1
    actor=Path(actors[0].split('=',1)[1])
    assert actor.name=='browser-opens.js' and re.fullmatch(r'luma-gate-browser-opens\.[A-Za-z0-9]+',actor.parent.name) and actor.parent.parent==Path('/tmp')
    assert actor.is_file() and not actor.is_symlink() and actor.stat().st_uid==uid
    runtime=Path('/run/user')/str(uid)
    assert os.environ.get('XDG_RUNTIME_DIR')==str(runtime)
    assert os.environ.get('DBUS_SESSION_BUS_ADDRESS')=='unix:path='+str(runtime/'bus')
    assert runtime.stat().st_uid==uid and stat.S_IMODE(runtime.stat().st_mode)==0o700
    for path in (runtime/'bus',runtime/display):
        info=path.lstat();assert stat.S_ISSOCK(info.st_mode) and info.st_uid==uid
    if args.action=='click':assert args.x is not None and args.y is not None and 0<=args.x<1280 and 0<=args.y<800
    else:assert args.x is None and args.y is None
    import gi
    from gi.repository import Gio,GLib
    bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
    service='org.gnome.Mutter.RemoteDesktop'
    def dbus(method,signature,values):
        return bus.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus',
            'org.freedesktop.DBus',method,GLib.Variant(signature,values),None,
            Gio.DBusCallFlags.NONE,5000,None).unpack()[0]
    owner=dbus('GetNameOwner','(s)',(service,))
    assert re.fullmatch(r':[0-9]+\.[0-9]+',owner)
    def owned_service():
        assert identity(parent)==expected,'Owned QA compositor identity changed'
        assert dbus('GetNameOwner','(s)',(service,))==owner,'QA input service owner changed'
        assert dbus('GetConnectionUnixProcessID','(s)',(owner,))==parent,'Input service does not belong to pinned Shell'
    owned_service()
    def call(path,interface,method,parameters=None):
        owned_service()
        reply=bus.call_sync(owner,path,interface,method,parameters,None,Gio.DBusCallFlags.NONE,5000,None)
        return reply.unpack() if reply else None
    session=call('/org/gnome/Mutter/RemoteDesktop',service,'CreateSession')[0]
    interface=service+'.Session'
    try:
        call(session,interface,'Start')
        if args.action=='click':
            call(session,interface,'NotifyPointerMotionRelative',GLib.Variant('(dd)',(-10000.,-10000.)))
            time.sleep(.1)
            call(session,interface,'NotifyPointerMotionRelative',GLib.Variant('(dd)',(float(args.x),float(args.y))))
            time.sleep(.1)
            for pressed in (True,False):
                call(session,interface,'NotifyPointerButton',GLib.Variant('(ib)',(272,pressed)))
                time.sleep(.06)
        else:
            # Match the existing private_input fixture: establish the virtual
            # keyboard/keymap with a neutral modifier before the actionable key.
            for pressed in (True,False):
                call(session,interface,'NotifyKeyboardKeysym',GLib.Variant('(ub)',(0xffe1,pressed)))
                time.sleep(.06)
            time.sleep(.1)
            for pressed in (True,False):
                call(session,interface,'NotifyKeyboardKeysym',GLib.Variant('(ub)',(ord('a'),pressed)))
                time.sleep(.06)
    finally:
        call(session,interface,'Stop')
    print(json.dumps(dict(result='REAL_OWNED_QA_COMPOSITOR_EVENT_SENT_NOT_PAGE_ACCEPTANCE',action=args.action,x=args.x,y=args.y,uid=uid,parent_pid=parent,parent_start=expected[1],display=display,service_unique_owner=owner)))

if __name__=='__main__':main()
