#!/usr/bin/env python3
"""The slider's calibration from a probe run's log: cluster the sampled XYZ into
the three stable positions (a sweep leaves three bands), take each band's median,
and print them the order the driver's slider_cal wants (index 0 = top/DND end,
index 2 = bottom/ring end -- the calibration check wants c[0]-c[3] and c[3]-c[6]
of one sign and at least 2000 apart).

Usage: slider-cal.py LOG
"""
import re
import statistics
import sys

data = open(sys.argv[1], 'rb').read().decode(errors='replace')
xs = [(int(m.group(1)), int(m.group(2)), int(m.group(3)))
      for m in re.finditer(r'op13-slider: sample x=(-?\d+) y=(-?\d+) z=(-?\d+)', data)]
if len(xs) < 30:
    sys.exit(f'only {len(xs)} samples')

# split into bands wherever x moves more than 1200 from the last sample's band head
bands = []
cur = [xs[0]]
for s in xs[1:]:
    if abs(s[0] - statistics.median(b[0] for b in cur)) > 1200:
        bands.append(cur)
        cur = [s]
    else:
        cur.append(s)
bands.append(cur)
bands = [b for b in bands if len(b) >= 8]          # a stop is at least ~0.8 s of samples
print(f'{len(xs)} samples, {len(bands)} stable bands:')
anchors = []
for b in bands:
    med = tuple(int(statistics.median(v)) for v in zip(*b))
    anchors.append(med)
    print(f'  n={len(b):4d}  x={med[0]:7d} y={med[1]:7d} z={med[2]:7d}')
if len(anchors) < 3:
    sys.exit('fewer than three stops were held long enough')
# take the three biggest bands, order them by x descending (index 0 = the high-x end)
big = sorted(bands, key=len, reverse=True)[:3]
meds = [tuple(int(statistics.median(v)) for v in zip(*b)) for b in big]
meds.sort(key=lambda t: -t[0])
a, mid, z = meds
if not ((a[0] - mid[0] >= 2000 and mid[0] - z[0] >= 2000) or (a[0] - mid[0] <= -2000 and mid[0] - z[0] <= -2000)):
    sys.exit(f'bands too close: {meds}')
print('\nslider_cal (index 0 = the top/DND end, 2 = the bottom/ring end):')
print(f'slider_cal={a[0]},{a[1]},{a[2]},{mid[0]},{mid[1]},{mid[2]},{z[0]},{z[1]},{z[2]}')
