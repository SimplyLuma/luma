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
import select
import sys
import subprocess

def identity(pid):
    root=Path('/proc')/str(pid)
    return (root.stat().st_uid,root.joinpath('stat').read_text().rsplit(')',1)[1].split()[19],
            root.joinpath('cmdline').read_bytes().split(b'\0')[:-1])

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('click','key-a','seat','monitor','capture'))
    parser.add_argument('--ready',type=Path)
    parser.add_argument('--output',type=Path)
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
    if args.action in ('seat','monitor'):
        assert args.ready is not None and args.ready.parent==actor.parent/'out'
        assert args.ready.name==('virtual-seat.json' if args.action=='seat' else 'monitor-stream.json') and not args.ready.exists()
    else:assert args.ready is None
    if args.action=='capture':
        assert args.output is not None and args.output.parent==actor.parent/'out'
        assert re.fullmatch(r'native-[a-z-]+-[0-9]+\.png',args.output.name)
        assert args.output.parent.is_dir() and not args.output.parent.is_symlink()
        assert args.output.parent.stat().st_uid==uid
        assert not args.output.exists() and not args.output.is_symlink()
    else:assert args.output is None
    runtime=Path('/run/user')/str(uid)
    assert os.environ.get('XDG_RUNTIME_DIR')==str(runtime)
    assert os.environ.get('DBUS_SESSION_BUS_ADDRESS')=='unix:path='+str(runtime/'bus')
    assert runtime.stat().st_uid==uid and stat.S_IMODE(runtime.stat().st_mode)==0o700
    for path in (runtime/'bus',runtime/display):
        info=path.lstat();assert stat.S_ISSOCK(info.st_mode) and info.st_uid==uid
    if args.action=='click':assert args.x is not None and args.y is not None and 0<=args.x<1600 and 0<=args.y<1000
    else:assert args.x is None and args.y is None
    import gi
    from gi.repository import Gio,GLib
    bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
    service='org.gnome.Mutter.ScreenCast' if args.action in ('monitor','capture') else 'org.gnome.Mutter.RemoteDesktop'
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
    if args.action=='capture':
        capture_monitor(call,bus,service,owned_service,args.output,parent,expected,owner)
        return
    if args.action=='monitor':
        monitor(call,bus,service,owned_service,args.ready,parent,expected)
        return
    session=call('/org/gnome/Mutter/RemoteDesktop',service,'CreateSession')[0]
    interface=service+'.Session'
    try:
        call(session,interface,'Start')
        if args.action=='seat':
            # A headless desktop has no physical devices. Keep its real
            # virtual seat alive before GTK maps, just as EngineDisplay does
            # for Chromium's separate private compositor.
            call(session,interface,'NotifyPointerMotionRelative',GLib.Variant('(dd)',(0.,0.)))
            for pressed in (True,False):
                call(session,interface,'NotifyKeyboardKeysym',GLib.Variant('(ub)',(0xffe1,pressed)))
            fd=os.open(args.ready,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as stream:
                json.dump(dict(scope='OWNED_VIRTUAL_DEVICES_NOT_PAGE_INPUT_PROOF',parent_pid=parent,parent_start=expected[1],owner=owner),stream)
            deadline=time.monotonic()+420
            while time.monotonic()<deadline:
                owned_service()
                if select.select([sys.stdin],[],[],1)[0]:
                    break
        elif args.action=='click':
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

def monitor(call,bus,service,owned_service,ready,parent,expected):
    """Keep an actual PipeWire consumer on the owned headless monitor."""
    import gi
    gi.require_version('Gst','1.0')
    from gi.repository import Gio,GLib,Gst
    Gst.init(None)
    session=call('/org/gnome/Mutter/ScreenCast',service,'CreateSession',GLib.Variant('(a{sv})',({},)))[0]
    stream=call(session,service+'.Session','RecordMonitor',GLib.Variant('(sa{sv})',('Meta-0',{'cursor-mode':GLib.Variant('u',0)})))[0]
    loop=GLib.MainLoop(); pipeline=None; errors=[]; frames=0
    def diagnostic(name,document):
        path=ready.with_name(name)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as output:json.dump(document,output)
    def message(_bus,packet):
        if packet.type==Gst.MessageType.ERROR:
            error,detail=packet.parse_error();errors.append(str(error)+': '+str(detail));diagnostic('monitor-error.json',dict(errors=errors));loop.quit()
    def handoff(*_):
        nonlocal frames
        frames+=1
    def started(_bus,_sender,_path,_interface,_signal,parameters):
        nonlocal pipeline
        node=parameters.unpack()[0]
        diagnostic('monitor-node.json',dict(node_id=node,parent_pid=parent,monotonic=time.monotonic()))
        try:
            dump=subprocess.run(['pw-dump'],capture_output=True,text=True,timeout=5)
            (ready.with_name('pipewire-nodes.json')).write_text(dump.stdout[:1048576])
            diagnostic('pipewire-dump-status.json',dict(status=dump.returncode,stderr=dump.stderr[:8192]))
        except Exception as error:diagnostic('pipewire-dump-status.json',dict(error=str(error)))
        # A fakesink accepts ANY caps. Constrain the source to raw video so
        # PipeWire/WirePlumber can link it to GNOME's video output stream.
        pipeline=Gst.parse_launch(f'pipewiresrc path={int(node)} do-timestamp=true ! video/x-raw ! fakesink name=monitor-consumer sync=false signal-handoffs=true')
        pipeline.get_by_name('monitor-consumer').connect('handoff',handoff)
        channel=pipeline.get_bus();channel.add_signal_watch();channel.connect('message',message)
        pipeline.set_state(Gst.State.PLAYING)
    subscription=bus.signal_subscribe(service,service+'.Stream','PipeWireStreamAdded',stream,None,Gio.DBusSignalFlags.NONE,started)
    deadline=time.monotonic()+420
    def poll():
        try:
            owned_service()
            if frames and not ready.exists():
                fd=os.open(ready,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'w') as output:
                    json.dump(dict(scope='REAL_HEADLESS_MONITOR_CONSUMER_NOT_PAGE_PROOF',parent_pid=parent,parent_start=expected[1],frames_received=frames),output)
            if time.monotonic()>deadline or select.select([sys.stdin],[],[],0)[0]:loop.quit();return False
        except Exception as error:errors.append(str(error));loop.quit();return False
        return True
    timer=GLib.timeout_add(100,poll)
    try:
        call(session,service+'.Session','Start');loop.run()
    finally:
        GLib.source_remove(timer)
        if pipeline:pipeline.set_state(Gst.State.NULL)
        bus.signal_unsubscribe(subscription)
        call(session,service+'.Session','Stop')
    if errors:raise RuntimeError('; '.join(errors))
    assert frames>0
    print(json.dumps(dict(scope='REAL_HEADLESS_MONITOR_CONSUMER_NOT_PAGE_PROOF',frames_received=frames,parent_pid=parent)))


def capture_monitor(call,bus,service,owned_service,output,parent,expected,owner):
    """Capture the pinned Shell monitor through its real ScreenCast stream."""
    import gi
    gi.require_version('Gst','1.0')
    from gi.repository import Gio,GLib,Gst
    Gst.init(None)
    deadline=time.monotonic()+15
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    before=os.fstat(fd)
    session=None;subscription=None;pipeline=None;frames=0;errors=[];eos=False;node_id=None
    loop=GLib.MainLoop()
    def message(_bus,packet):
        nonlocal eos
        if packet.type==Gst.MessageType.ERROR:
            error,detail=packet.parse_error();errors.append(str(error)+': '+str(detail));loop.quit()
        elif packet.type==Gst.MessageType.EOS:
            eos=True;loop.quit()
    def handoff(*_):
        nonlocal frames
        frames+=1
    def started(_bus,_sender,_path,_interface,_signal,parameters):
        nonlocal pipeline,node_id
        try:
            owned_service()
            node_id=parameters.unpack()[0]
            assert pipeline is None
            pipeline=Gst.Pipeline.new('owned-shell-monitor-png')
            source=Gst.ElementFactory.make('pipewiresrc')
            caps=Gst.ElementFactory.make('capsfilter')
            tap=Gst.ElementFactory.make('identity')
            convert=Gst.ElementFactory.make('videoconvert')
            encode=Gst.ElementFactory.make('pngenc')
            sink=Gst.ElementFactory.make('filesink')
            elements=(source,caps,tap,convert,encode,sink)
            assert all(elements),'Installed compositor PNG elements unavailable'
            source.set_property('path',str(node_id))
            source.set_property('num-buffers',1)
            source.set_property('do-timestamp',True)
            caps.set_property('caps',Gst.Caps.from_string('video/x-raw'))
            tap.set_property('signal-handoffs',True);tap.connect('handoff',handoff)
            # Keep the exclusively created inode pinned; filesink cannot follow
            # a replaced output pathname or overwrite an existing user file.
            sink.set_property('location',f'/proc/self/fd/{fd}')
            sink.set_property('sync',False)
            for element in elements:pipeline.add(element)
            for left,right in zip(elements,elements[1:]):assert left.link(right)
            channel=pipeline.get_bus();channel.add_signal_watch();channel.connect('message',message)
            assert pipeline.set_state(Gst.State.PLAYING)!=Gst.StateChangeReturn.FAILURE
        except Exception as error:errors.append(str(error));loop.quit()
    def poll():
        try:
            owned_service()
            if time.monotonic()>=deadline:
                errors.append('Owned compositor PNG capture timed out');loop.quit();return False
        except Exception as error:errors.append(str(error));loop.quit();return False
        return True
    timer=GLib.timeout_add(100,poll)
    try:
        session=call('/org/gnome/Mutter/ScreenCast',service,'CreateSession',GLib.Variant('(a{sv})',({},)))[0]
        stream=call(session,service+'.Session','RecordMonitor',GLib.Variant('(sa{sv})',('Meta-0',{'cursor-mode':GLib.Variant('u',0)})))[0]
        subscription=bus.signal_subscribe(service,service+'.Stream','PipeWireStreamAdded',stream,None,Gio.DBusSignalFlags.NONE,started)
        call(session,service+'.Session','Start');loop.run()
    finally:
        if pipeline:pipeline.set_state(Gst.State.NULL)
        if subscription is not None:bus.signal_unsubscribe(subscription)
        if GLib.MainContext.default().find_source_by_id(timer):GLib.source_remove(timer)
        try:
            if session:call(session,service+'.Session','Stop')
        finally:os.close(fd)
    assert not errors,'; '.join(errors)
    assert eos and frames==1,'Actual one-frame compositor PNG did not complete'
    owned_service()
    after=output.lstat()
    assert stat.S_ISREG(after.st_mode) and (after.st_dev,after.st_ino)==(before.st_dev,before.st_ino)
    assert 33<=after.st_size<=16*1024*1024
    with output.open('rb') as image:assert image.read(8)==b'\x89PNG\r\n\x1a\n'
    print(json.dumps(dict(scope='REAL_OWNED_COMPOSITOR_PNG_NOT_PAGE_IDENTITY_PROOF',output=str(output),
                         parent_pid=parent,parent_start=expected[1],service_unique_owner=owner,
                         node_id=node_id,frames_received=frames,bytes=after.st_size)))

if __name__=='__main__':main()
