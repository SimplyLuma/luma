"""Grow resident memory at RATE MiB/s with ~3:1 compressible pages, up to LIMIT MiB (0 = forever)."""
import os, sys, time
rate, limit = float(sys.argv[1]), int(sys.argv[2])
chunks, block = [], 1 << 20
pattern = (os.urandom(1400) + bytes(2696)) * 256  # ~1/3 random per page
while not limit or len(chunks) < limit:
    chunks.append(bytearray(pattern))
    time.sleep(1 / rate)
time.sleep(10 ** 6)
