#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Native GTK activation control for the synthetic Android clipboard fixture.

Run as the disposable graphical user. Records activation/timing only, never
clipboard contents. Gtk.Window:is-active does not attest compositor input focus.
Keep its visible window next to Android and switch by normal clicks.
"""
import argparse
import json
import os
from pathlib import Path
import time

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if os.getuid()==0:
        parser.error('Run as the disposable graphical user, not root')
    descriptor=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'w',buffering=1) as stream:
        def record(active):
            stream.write(json.dumps({'event':'native-window-activation','active':bool(active),
                'source':'Gtk.Window:is-active','input_focus_attested':False,
                'elapsed_ms':time.clock_gettime_ns(time.CLOCK_BOOTTIME)//1_000_000})+'\n')
        app=Gtk.Application(application_id='org.projectluma.AndroidFocusControl')
        def activate(application):
            window=Gtk.ApplicationWindow(application=application,title='Luma clipboard focus control')
            window.set_default_size(360,200)
            label=Gtk.Label(label='Synthetic native focus control\n\nClick here after scheduling the\nAndroid clipboard test.')
            window.set_child(label)
            window.connect('notify::is-active',lambda current,_property:record(current.get_property('is-active')))
            window.present()
        app.connect('activate',activate)
        record(False)
        return app.run([])


if __name__=='__main__':raise SystemExit(main())
