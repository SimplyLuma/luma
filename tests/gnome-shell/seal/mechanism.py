#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Seal evidence harness, test container only. Runs as root, like a system
# mechanism (udisks, flatpak-system-helper, timedated): asks polkitd whether a
# requesting process may perform an action, with the message and details that
# mechanism would pass, and reports the answer. Also drives the libfprint
# virtual reader. One JSON request per connection on /run/seal-mechanism.sock.
import json, os, socket, socketserver, threading, time
import gi
gi.require_version("Polkit", "1.0")
from gi.repository import Gio, GLib, Polkit

SOCK = "/run/seal-mechanism.sock"
READER = "/run/fprint-virtual/sock"

def reader(command):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(READER)
    s.sendall(command.encode())
    s.shutdown(socket.SHUT_WR)
    try:
        reply = s.recv(1024).decode(errors="replace")
    except OSError:
        reply = ""
    s.close()
    return reply

def check(req):
    authority = Polkit.Authority.get_sync(None)
    pid = int(req["pid"])
    subject = Polkit.UnixProcess.new_for_owner(pid, 0, os.stat(f"/proc/{pid}").st_uid)
    details = Polkit.Details.new()
    for key, value in (req.get("details") or {}).items():
        details.insert(key, value)
    started = time.monotonic()
    try:
        result = authority.check_authorization_sync(subject, req["action"], details,
            Polkit.CheckAuthorizationFlags.ALLOW_USER_INTERACTION, None)
        return {"authorized": result.get_is_authorized(), "challenge": result.get_is_challenge(),
                "dismissed": result.get_dismissed(), "seconds": round(time.monotonic() - started, 3)}
    except GLib.Error as e:
        return {"authorized": False, "error": e.message, "seconds": round(time.monotonic() - started, 3)}

class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        req = json.loads(self.rfile.readline())
        if "reader" in req:
            out = {"reply": reader(req["reader"])}
        elif "fprintd" in req:
            # "stop": no reader for this run (masked so pam_fprintd cannot
            # activate it); "start": back.
            import subprocess
            if req["fprintd"] == "stop":
                subprocess.run(["systemctl", "mask", "--runtime", "--now", "fprintd"], check=False)
            else:
                subprocess.run(["systemctl", "unmask", "--runtime", "fprintd"], check=False)
                subprocess.run(["systemctl", "start", "fprintd"], check=False)
            out = {"fprintd": subprocess.run(["systemctl", "is-active", "fprintd"], capture_output=True, text=True).stdout.strip()}
        else:
            out = check(req)
        self.wfile.write((json.dumps(out) + "\n").encode())

class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

if os.path.exists(SOCK):
    os.unlink(SOCK)
server = Server(SOCK, Handler)
os.chmod(SOCK, 0o666)
server.serve_forever()
