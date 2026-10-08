#!/bin/bash
# analyze.sh RUN [CONTAINER]: after matrix.sh, decode recordings and compare captures.
# Writes evidence/RUN/analysis.txt and frame PNGs beside each recording.
RUN=${1:?run}; C=${2:-luma-shell-oracle}; E=/oracle/capture/evidence/$RUN
podman exec $C bash -c "
set -u
cd $E
{
echo '# Selection capture vs the same region of the screen afterwards'
python3 /oracle/capture/harness/diff.py flows/selection-capture.png flows/selection-live.png
echo '# Search capture vs the screen before search opened (the search dialog is not in the capture)'
python3 /oracle/capture/harness/diff.py search-keyboard/search-capture.png search-keyboard/search-baseline.png
for v in record/*.webm; do
  base=\${v%.webm}
  gst-launch-1.0 -q filesrc location=\$v ! decodebin ! videoconvert ! videorate ! video/x-raw,framerate=1/1 ! pngenc snapshot=false ! multifilesink location=\$base-frame%02d.png
  echo \"# \$v: \$(ls \$base-frame*.png | wc -l) frames decoded at 1 fps\"
done
} > analysis.txt 2>&1
"
podman exec -i $C python3 - "$E" <<'PY'
import gi, json, sys, glob
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf
E = sys.argv[1]
r = json.load(open(f'{E}/record/results.json'))['cases']['record']
def region_stats(path, x, y, w, h):
    pb = GdkPixbuf.Pixbuf.new_from_file(path)
    px = pb.get_pixels(); n = pb.get_n_channels(); rs = pb.get_rowstride()
    red = light = 0
    for yy in range(int(y), int(y + h)):
        for xx in range(int(x), int(x + w)):
            o = yy * rs + xx * n
            R, G, B = px[o], px[o + 1], px[o + 2]
            red += R > 180 and G < 110 and B < 110
            light += R > 200 and G > 200 and B > 200
    return red, light
out = []
px, py, pw, ph = r['screen']['pillRect']
for f in sorted(glob.glob(f'{E}/record/record-screen-frame*.png')):
    out.append({'frame': f.split('/')[-1], 'pillRegion': [px, py, pw, ph], 'redPixels_recordDot': region_stats(f, px, py, pw, ph)[0], 'lightPixels_pillSurface': region_stats(f, px, py, pw, ph)[1]})
s0 = r['stops'][0]
gx, gy, gw, gh = s0['geometry']
sx, sy, sw, sh = s0['pillRect']
for f in sorted(glob.glob(f'{E}/record/record-stop-frame*.png')):
    out.append({'frame': f.split('/')[-1], 'selection': s0['geometry'], 'pillRegionInVideo': [sx - gx, sy - gy, sw, sh],
        'redPixels_recordDot': region_stats(f, sx - gx, sy - gy, sw, sh)[0], 'lightPixels_pillSurface': region_stats(f, sx - gx, sy - gy, sw, sh)[1]})
live = f'{E}/record/record-screen-pill.png'
out.append({'frame': 'record-screen-pill.png (the screen while recording)', 'redPixels_recordDot': region_stats(live, px, py, pw, ph)[0], 'lightPixels_pillSurface': region_stats(live, px, py, pw, ph)[1]})
with open(f'{E}/analysis.txt', 'a') as fh:
    fh.write('# Recordings: the pill region in each decoded frame (whole screen, then a selection containing the pill) vs on screen\n')
    for o in out:
        fh.write(json.dumps(o) + '\n')
PY
cat /root/luma-shell-oracle/capture/evidence/$RUN/analysis.txt
