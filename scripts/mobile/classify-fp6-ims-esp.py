#!/usr/bin/env python3
"""Count FP6 IMS ESP traffic without recording packets or identifiers.

The probe is deliberately metadata-only: it keeps counters in memory, never
writes a capture, and reports neither addresses nor SPI values.  It correlates
wire ESP packets with the currently installed XFRM states so a physical
incoming-call gate can distinguish carrier non-delivery from an unexpected
security-association path.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import select
import socket
import struct
import subprocess
import time
from collections import Counter


ETH_P_ALL = 0x0003
PACKET_OUTGOING = 4


def command(*args: str) -> str:
    return subprocess.run(
        args,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    ).stdout


def configured_pcscf(path: str) -> str:
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            key, separator, value = line.partition("=")
            if separator and key.strip() == "PCSCF":
                return value.strip().strip("'\"")
    raise RuntimeError("PCSCF is not configured")


def route_interface(destination: str) -> str:
    route = json.loads(command("ip", "-j", "route", "get", destination))
    if not route or not route[0].get("dev"):
        raise RuntimeError("IMS route has no interface")
    return str(route[0]["dev"])


def local_addresses(interface: str) -> set[ipaddress._BaseAddress]:
    result: set[ipaddress._BaseAddress] = set()
    for link in json.loads(command("ip", "-j", "address", "show", "dev", interface)):
        for info in link.get("addr_info", []):
            local = info.get("local")
            if local:
                result.add(ipaddress.ip_address(local))
    return result


def parse_xfrm_text(
    text: str, local: set[ipaddress._BaseAddress]
) -> dict[int, tuple[bool, int]]:
    states: dict[int, tuple[bool, int]] = {}
    destination: str | None = None
    for line in text.splitlines():
        header = re.match(r"^src\s+\S+\s+dst\s+(\S+)$", line)
        if header:
            destination = header.group(1)
            continue
        details = re.match(
            r"^\s*proto\s+esp\s+spi\s+(0x[0-9a-fA-F]+|\d+)"
            r"(?:\s+reqid\s+(\d+))?",
            line,
        )
        if details and destination:
            spi = int(details.group(1), 0)
            reqid = int(details.group(2) or 0)
            states[spi] = (ipaddress.ip_address(destination) in local, reqid)
    return states


def xfrm_states(local: set[ipaddress._BaseAddress]) -> dict[int, tuple[bool, int]]:
    states: dict[int, tuple[bool, int]] = {}
    output = command("ip", "-j", "xfrm", "state")
    try:
        decoded = json.loads(output)
    except json.JSONDecodeError:
        states = parse_xfrm_text(output, local)
    else:
        for state in decoded:
            spi_text = state.get("spi")
            destination = state.get("dst")
            if spi_text is None or destination is None:
                continue
            spi = int(str(spi_text), 0)
            inbound = ipaddress.ip_address(destination) in local
            reqid = int(state.get("reqid", 0))
            states[spi] = (inbound, reqid)
    if not states:
        raise RuntimeError("no XFRM states are installed")
    return states


def esp_header(packet: bytes) -> tuple[ipaddress._BaseAddress, int] | None:
    """Return (destination, SPI) for a direct IPv4/IPv6 ESP packet."""
    offset = 0
    if packet and packet[0] >> 4 not in (4, 6):
        if len(packet) < 15:
            return None
        offset = 14

    if len(packet) <= offset:
        return None
    version = packet[offset] >> 4
    if version == 4:
        if len(packet) < offset + 20:
            return None
        ihl = (packet[offset] & 0x0F) * 4
        if ihl < 20 or len(packet) < offset + ihl + 4 or packet[offset + 9] != 50:
            return None
        destination = ipaddress.ip_address(packet[offset + 16 : offset + 20])
        spi = struct.unpack_from("!I", packet, offset + ihl)[0]
        return destination, spi
    if version == 6:
        if len(packet) < offset + 44 or packet[offset + 6] != 50:
            return None
        destination = ipaddress.ip_address(packet[offset + 24 : offset + 40])
        spi = struct.unpack_from("!I", packet, offset + 40)[0]
        return destination, spi
    return None


def classify(
    packet: bytes,
    packet_type: int,
    local: set[ipaddress._BaseAddress],
    states: dict[int, tuple[bool, int]],
) -> str | None:
    parsed = esp_header(packet)
    if parsed is None:
        return None
    destination, spi = parsed
    wire_inbound = packet_type != PACKET_OUTGOING and destination in local
    direction = "in" if wire_inbound else "out"
    state = states.get(spi)
    if state is None:
        return f"{direction}-unknown"
    state_inbound, reqid = state
    if state_inbound != wire_inbound:
        return f"{direction}-state-direction-mismatch"
    role = "client" if reqid == 1 else "server" if reqid == 2 else "other"
    return f"{direction}-{role}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=90)
    parser.add_argument("--env", default="/etc/imsd.env")
    parser.add_argument("--interface")
    args = parser.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("must run as root")
    if not 1 <= args.duration <= 300:
        raise SystemExit("duration must be between 1 and 300 seconds")

    interface = args.interface or route_interface(configured_pcscf(args.env))
    local = local_addresses(interface)
    if not local:
        raise SystemExit("IMS interface has no local address")
    states = xfrm_states(local)

    counters: Counter[str] = Counter()
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
    sock.bind((interface, 0))
    deadline = time.monotonic() + args.duration
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            readable, _, _ = select.select([sock], [], [], min(remaining, 1.0))
            if not readable:
                continue
            packet, address = sock.recvfrom(65535)
            result = classify(packet, int(address[2]), local, states)
            if result:
                counters[result] += 1
    finally:
        sock.close()

    fields = [
        "in-client",
        "in-server",
        "in-other",
        "in-unknown",
        "in-state-direction-mismatch",
        "out-client",
        "out-server",
        "out-other",
        "out-unknown",
        "out-state-direction-mismatch",
    ]
    summary = " ".join(f"{field.replace('-', '_')}={counters[field]}" for field in fields)
    print(f"event=ims-esp-classification {summary} packets_saved=0 identifiers=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
