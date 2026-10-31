#!/usr/bin/env python3
"""Capture the CP's DM (umts_dm0) stream after the stock start sequence.

The packets are the ones the stock modem_logging_control builds for
DmStartRequest, the 0x06 control request it sends before the trace selection,
and DmTraceStartRequest (SDM framing: 0x7f, length, header with group 0xa0
and the command byte at offset 10, payload, 0x7e). No trace selection is
sent: whatever the CP streams comes from its own default mask.

Usage: luma-fold-modem-dm.py <dm device> <output file> <seconds> <max bytes>
"""
import os
import select
import sys
import time

START = bytes.fromhex("7f1200000f000000a0" + "00" * 7 + "6f6f47" + "7e")
CTRL06 = bytes.fromhex("7f1000000d000000a0" + "00" + "06" + "00000000" + "05" + "00" + "7e")
TRACE_START = bytes.fromhex("7f0f00000c000000a0" + "00" + "50" + "00000000" + "01" + "7e")
assert (len(START), len(CTRL06), len(TRACE_START)) == (20, 18, 17)


def main():
    dev, out, secs, cap = sys.argv[1], sys.argv[2], float(sys.argv[3]), int(sys.argv[4])
    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    total = frames = 0
    first = b""
    with open(out, "wb") as f:
        for name, pkt in (("DmStartRequest", START), ("ctrl 0x06", CTRL06),
                          ("DmTraceStartRequest", TRACE_START)):
            try:
                os.write(fd, pkt)
                print("dm: sent %s (%d bytes) %s" % (name, len(pkt), pkt.hex()), flush=True)
            except OSError as e:
                print("dm: %s write failed: %s" % (name, e), flush=True)
            time.sleep(0.3)
        end = time.time() + secs
        while time.time() < end and total < cap:
            r, _, _ = select.select([fd], [], [], 1.0)
            if not r:
                continue
            try:
                d = os.read(fd, 65536)
            except BlockingIOError:
                continue
            if not d:
                break
            if not first:
                first = d[:256]
                print("dm: first frame %d bytes at +%.1fs: %s" % (
                    len(d), secs - (end - time.time()), first.hex()), flush=True)
            f.write(d)
            total += len(d)
            frames += 1
    os.close(fd)
    print("dm: captured %d frames, %d bytes -> %s" % (frames, total, out), flush=True)


if __name__ == "__main__":
    main()
