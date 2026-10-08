#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify actual WebKit default remote-image denial and explicit image opt-in.

The only server is a private ephemeral loopback fixture, never an account host.
Sender JavaScript/local storage remain disabled under both policies.
"""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
import gi
gi.require_version('Gtk','4.0');gi.require_version('WebKit','6.0')
from gi.repository import Gtk, GLib
from charlie_luma.html_reader import reader

requests=[]
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        requests.append(self.path)
        image=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII=')
        self.send_response(200);self.send_header('Content-Type','image/png')
        self.send_header('Content-Length',str(len(image)));self.end_headers();self.wfile.write(image)
    def log_message(self,*_):pass

def pump(seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
        time.sleep(.005)
Gtk.init()
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
try:
    origin=f'http://127.0.0.1:{server.server_port}'
    for allow_remote in (False,True):
        path=f'/image-{allow_remote}.png'
        html=f'<h1>Private local mail</h1><img src="{origin}{path}"><script>fetch("{origin}/script")</script>'
        view=reader(html,allow_remote=allow_remote)
        window=Gtk.Window(default_width=450,default_height=340);window.set_child(view);window.present()
        deadline=time.monotonic()+12
        while view.is_loading():
            assert time.monotonic()<deadline,'WebKit local document failed to load'
            pump(.04)
        pump(.4)
        assert not view.get_settings().get_enable_javascript()
        assert not view.get_settings().get_enable_html5_local_storage()
        assert not view.get_settings().get_enable_page_cache()
        assert '/script' not in requests, 'sender script executed'
        assert (path in requests)==allow_remote, f'remote image policy broken: {allow_remote}/{requests}'
        print('PASS native remote image opt-in / JS / storage',allow_remote,flush=True)
        window.destroy();pump(.15)
finally:
    server.shutdown();server.server_close();thread.join(timeout=2)
