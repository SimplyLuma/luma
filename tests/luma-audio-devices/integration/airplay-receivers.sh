#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
#
# AirPlay receivers for scenario_airplay.py, run inside a second Fedora 44
# container on the same bridge network as the one under test.
# Needs: shairport-sync avahi avahi-tools dbus-daemon python3 procps-ng.
#
#   airplay-receivers.sh start          all three receivers
#   airplay-receivers.sh stop-open      "Luma Test Speaker" leaves (avahi goodbye)
#   airplay-receivers.sh start-open     and comes back
#   airplay-receivers.sh audio-bytes    PCM bytes "Luma Test Speaker" has played
set -u
dir=/tmp/luma-airplay-rx
mkdir -p "$dir" /run/dbus

start_open() {
  cat >"$dir/open.conf" <<'CONF'
general = { name = "Luma Test Speaker"; port = 5000; udp_port_base = 6101; output_backend = "stdout"; mdns_backend = "avahi"; };
CONF
  touch "$dir/open.pcm"
  nohup shairport-sync -c "$dir/open.conf" >>"$dir/open.pcm" 2>>"$dir/open.log" &
  echo $! >"$dir/open.pid"
}

case "${1:-start}" in
  start)
    if ! pgrep -f "[d]bus-daemon --system" >/dev/null; then
      rm -f /run/dbus/system_bus_socket /run/dbus/pid /run/dbus/messagebus.pid
      dbus-daemon --system --fork
      sleep 1
    fi
    if ! avahi-daemon --check 2>/dev/null; then
      rm -f /run/avahi-daemon/pid
      avahi-daemon --daemonize --no-drop-root
    fi
    sleep 1
    pkill -x shairport-sync; pkill -f '[r]efusing-receiver'; pkill -x avahi-publish; sleep 0.5
    : >"$dir/open.pcm"
    start_open
    cat >"$dir/locked.conf" <<'CONF'
general = { name = "Luma Locked Speaker"; port = 5001; udp_port_base = 6201; output_backend = "stdout"; mdns_backend = "avahi"; password = "luma-test"; };
CONF
    nohup shairport-sync -c "$dir/locked.conf" >"$dir/locked.pcm" 2>"$dir/locked.log" &
    # Accepts the first OPTIONS (the connection check), refuses every later one,
    # which makes module-raop-sink give up when playback starts.
    cat >"$dir/refusing-receiver.py" <<'PY'
import socket, threading
server = socket.socket(); server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("0.0.0.0", 5009)); server.listen(8)
served = {"n": 0}
def handle(conn):
    with conn:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk: return
            data += chunk
        cseq = [l.split(":", 1)[1].strip() for l in data.decode(errors="replace").split("\r\n") if l.lower().startswith("cseq")]
        status = "200 OK" if served["n"] == 0 else "403 Forbidden"
        served["n"] += 1
        conn.sendall(f"RTSP/1.0 {status}\r\nCSeq: {cseq[0] if cseq else 1}\r\n\r\n".encode())
while True:
    c, _ = server.accept(); threading.Thread(target=handle, args=(c,), daemon=True).start()
PY
    nohup python3 "$dir/refusing-receiver.py" >"$dir/refusing.log" 2>&1 &
    nohup avahi-publish -s "0A0B0C0D0E0F@Luma Refusing Speaker" _raop._tcp 5009 \
      tp=UDP et=0 cn=0 pw=false am=LumaTest >"$dir/publish.log" 2>&1 &
    sleep 2
    pgrep -a shairport-sync
    ;;
  stop-open)
    kill "$(cat "$dir/open.pid")" 2>/dev/null
    sleep 1
    ;;
  start-open)
    start_open
    sleep 2
    ;;
  audio-bytes)
    stat -c %s "$dir/open.pcm"
    ;;
esac
