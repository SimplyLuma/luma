# Pixel 9: NetworkManager does not always autoconnect the new modem after the
# ModemManager restart in a data switch; ask for the connection once.
F = "/var/lib/luma-fold/modem/luma-fold-mobile-mode.py"
s = open(F).read()
old = '''    def wait_nm_connected(self, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _, out = self.modem.run("nmcli", "-t", "-f", "TYPE,STATE", "device")
            if any(line.startswith("gsm:connected") for line in out.splitlines()):
                return True
            self.modem.sleep(3)
        return False
'''
new = '''    def wait_nm_connected(self, timeout=90):
        deadline = time.monotonic() + timeout
        # NetworkManager does not always autoconnect the modem it sees after the
        # ModemManager restart; ask for the connection once if it has not.
        nudge = time.monotonic() + 15
        while time.monotonic() < deadline:
            _, out = self.modem.run("nmcli", "-t", "-f", "TYPE,STATE", "device")
            if any(line.startswith("gsm:connected") for line in out.splitlines()):
                return True
            if nudge and time.monotonic() >= nudge:
                nudge = None
                uuid = None
                for line in read(self.paths.nm_connection).splitlines():
                    if line.startswith("uuid="):
                        uuid = line.split("=", 1)[1].strip()
                if uuid:
                    self.modem.run("nmcli", "connection", "up", "uuid", uuid, timeout=60)
            self.modem.sleep(3)
        return False
'''
assert s.count(old) == 1
open(F, "w").write(s.replace(old, new))
print("patched")
