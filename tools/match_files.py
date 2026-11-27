#!/usr/bin/env python3
"""match_files.py DIR [DIR ...] [--apply DIR2]: which files there are ones the repository already carries -- by
size and FNV-1a 64 against fw-wifi/sets.json, what tools/compose_combined.py wrote: the MHI image of a set, one
of a set's files, or none of them. With --apply DIR2, those found are copied into DIR2/<the set>/ under the
repository's names. What matches nothing is left alone: a stock board file never passes for the ODM's calibration.

    python3 devices/dodge/tools/match_files.py fw-wifi/raw/odm --apply fw-wifi
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SETS = os.path.join(os.path.dirname(HERE), 'fw-wifi', 'sets.json')


def fnv(b):
    h = 0xcbf29ce484222325
    for x in b:
        h ^= x
        h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h


args = [a for a in sys.argv[1:] if not a.startswith('-')]
apply_to = args[args.index('--apply') + 1] if '--apply' in sys.argv else None
dirs = [a for a in args if a != apply_to] or [os.getcwd()]

known = {}                       # (size, fnv) -> [(the set's name, the repository's name for the file)]
for variant, s in json.load(open(SETS)).items():
    for f in [s['mhi']] + s['files']:
        known.setdefault((f['size'], int(f['fnv'], 16)), []).append((variant, f['name']))

for d in dirs:
    for name in sorted(os.listdir(d)):
        path = os.path.join(d, name)
        if not os.path.isfile(path):
            continue
        data = open(path, 'rb').read()
        for variant, want in known.get((len(data), fnv(data)), []):
            print('MATCH %s/%-28s <- %s' % (variant, want, path))
            if apply_to:
                os.makedirs(os.path.join(apply_to, variant), exist_ok=True)
                shutil.copy2(path, os.path.join(apply_to, variant, want))
