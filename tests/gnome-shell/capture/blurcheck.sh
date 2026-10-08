#!/bin/bash
# blurcheck.sh DIR [CONTAINER]: after the eval "blur" case, compare each shelf
# island in every screenshot with the same island in a frame of the screen
# recording (the screen's own pixels). The recording is VP8, so a match is a
# small mean difference, not zero; the bug made islands differ by tens.
D=${1:?dir with results.json}; C=${2:-luma-shell-oracle}
podman exec -i $C bash -s "$D" <<'SH'
set -u
cd "$1"
rec=$(ls blur-screen.* 2>/dev/null | head -1)
gst-launch-1.0 -q filesrc location="$rec" ! decodebin ! videoconvert ! videorate ! video/x-raw,framerate=1/1 ! pngenc snapshot=false ! multifilesink location=blur-frame%02d.png
python3 - <<'PY'
import gi, json, glob
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf
blur = json.load(open('results.json'))['cases']['blur']
frame = sorted(glob.glob('blur-frame*.png'))[1]
def load(p):
    pb = GdkPixbuf.Pixbuf.new_from_file(p)
    return pb, pb.get_pixels(), pb.get_n_channels(), pb.get_rowstride()
def mean_diff(a, b, r, off=(0, 0)):
    (pa, xa, na, ra), (pb, xb, nb, rb) = a, b
    x, y, w, h = r
    tot = n = 0
    for yy in range(y, y + h, 2):
        for xx in range(x, x + w, 2):
            ax, ay = xx - off[0], yy - off[1]
            if not (0 <= ax < pa.get_width() and 0 <= ay < pa.get_height() and xx < pb.get_width() and yy < pb.get_height()):
                continue
            oa, ob = ay * ra + ax * na, yy * rb + xx * nb
            tot += sum(abs(xa[oa + c] - xb[ob + c]) for c in range(3)); n += 3
    return round(tot / max(n, 1), 2)
ref = load(frame)
shots = {'stage screenshot': ('blur-screenshot.png', (0, 0)), 'Capture': ('blur-capture.png', (0, 0)),
         'D-Bus Screenshot': ('blur-dbus-screen.png', (0, 0)),
         'D-Bus ScreenshotArea': ('blur-dbus-area.png', tuple(blur['area'][:2]))}
print('reference:', frame)
for name, (path, off) in shots.items():
    try:
        img = load(path)
    except Exception as e:
        print(name, 'missing', e); continue
    print(name, [(i['style'].split()[1] if len(i['style'].split()) > 1 else i['style'], mean_diff(img, ref, i['rect'], off)) for i in blur['islands']])
PY
SH
