#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Protocol fixture only: actual Gio MPRIS name/properties/actions, no audio.
import os
from gi.repository import Gio,GLib
bus=Gio.bus_get_sync(Gio.BusType.SESSION)
xml="""<node><interface name="org.mpris.MediaPlayer2"><property name="Identity" type="s" access="read"/><property name="DesktopEntry" type="s" access="read"/><property name="CanRaise" type="b" access="read"/></interface><interface name="org.mpris.MediaPlayer2.Player"><property name="PlaybackStatus" type="s" access="read"/><property name="Metadata" type="a{sv}" access="read"/><property name="CanGoNext" type="b" access="read"/><property name="CanGoPrevious" type="b" access="read"/><property name="CanPlay" type="b" access="read"/><property name="CanPause" type="b" access="read"/><property name="CanControl" type="b" access="read"/><property name="Position" type="x" access="read"/><method name="PlayPause"/><method name="Next"/><method name="Previous"/></interface></node>"""
S=lambda v:GLib.Variant('s',v);B=lambda v:GLib.Variant('b',v)
values={'Identity':S('Media protocol fixture'),'DesktopEntry':S('org.projectluma.Tide'),'CanRaise':B(True),'PlaybackStatus':S('Playing'),'CanGoNext':B(True),'CanGoPrevious':B(True),'CanPlay':B(True),'CanPause':B(True),'CanControl':B(True),'Position':GLib.Variant('x',0),'Metadata':GLib.Variant('a{sv}',{'mpris:trackid':GLib.Variant('o','/fixture/track1'),'xesam:title':S('Live controls fixture'),'xesam:artist':GLib.Variant('as',['Native MPRIS input'])})}
def method(connection,sender,path,interface,name,params,inv):
 with open(os.environ['LUMA_MEDIA_ACTION_LOG'],'a') as f:f.write(name+'\n')
 inv.return_value(None)
for iface in Gio.DBusNodeInfo.new_for_xml(xml).interfaces:bus.register_object('/org/mpris/MediaPlayer2',iface,method,lambda c,s,p,i,n:values.get(n),None)
Gio.bus_own_name_on_connection(bus,'org.mpris.MediaPlayer2.luma_activity_fixture',0,None,None)
print('MPRIS fixture ready',flush=True)
GLib.MainLoop().run()
