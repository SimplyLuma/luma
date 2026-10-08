"""Write the 220 Hz tone the sound oracle plays while testing 'something is playing'."""
import math, struct, sys
rate, seconds = 48000, 120
with open(sys.argv[1] if len(sys.argv) > 1 else 'tone.raw', 'wb') as out:
    out.write(b''.join(struct.pack('<h', int(3000 * math.sin(2 * math.pi * 220 * i / rate)))
                       for i in range(rate * seconds)))
