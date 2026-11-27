#!/usr/bin/env python3
"""Compose fw-wifi/combined.bin -- the one image the host stages for the loader's MHI download, which holds the
firmware of both revisions of the WCN7850 an OnePlus 13 carries -- and hold the loader's table to it.

    python3 devices/dodge/tools/compose_combined.py [TREE]          compose, then check TREE's table
    python3 devices/dodge/tools/compose_combined.py --check [TREE]  check only (the CI)
    python3 devices/dodge/tools/compose_combined.py --header        the table as these files compose it

An OnePlus 13 comes with one of two revisions of the WCN7850, "peach".  Android drives the first with its
qca_cld3_peach and the second -- peach_v2 -- with qca_cld3_peach_v2, chosen by the chip itself (dmesg, on the
first: "Driver built for chip ver 0x2, enumerated ver 0x1, reject unsupported driver"), and gives each its own
files, as pull-firmware.sh takes them off the phone:

    fw-wifi/peach/      the first revision:   amss.bin, tmel_peach_10.elf, the ODM's bdwlang.elf and
                                               regdb.bin, phy_ucode.elf, aux_ucode.elf, qdss_trace_config_v1.cfg
    fw-wifi/peach_v2/   the second:            amss20.bin, tmel_peach_20.elf, bdwlan.elf, regdb.bin,
                                               phy_ucode20.elf, aux_ucode20.elf, qdss_trace_config_v2.cfg

Each set is its MHI image from the set's base in the staged image, and the six other files each at its own
offset past the base's 0x800000.  The second revision's set is at the image's start -- where the image of one
set, combined20.bin, began, byte for byte -- and the first revision's follows it.

Which set is the phone's is not decided by what is staged: the loader reads the chip's revision off the chip
itself, before anything is downloaded -- its second aperture, 2 MiB on the first revision and 32 KiB on the
second, sized once BAR 0 is placed (xnuloader/op13_wifi_mhi_survey.inc) -- and takes that set's bytes where
xnuloader/op13_wifi_files.h says they are.  That table is the port's own patch (patches/, in this repository),
made of these files: so it is checked here against what they compose to, and --header says it afresh, for when
the firmware is refreshed (pull-firmware.sh runs this).

TREE is the common tree whose loader carries the table: this repository's kernel/ by default (where
apply-kernel-patches.sh puts it), or a worktree of it.  fw-wifi/sets.json says what was composed.
"""
import hashlib
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(REPO, 'fw-wifi')
OUT = os.path.join(FW, 'combined.bin')
HEADER = 'xnuloader/op13_wifi_files.h'

# the six files, in the order the loader sends them -- and that is the order of its table's entries
ORDER = ['tmel', 'regdb', 'bdf', 'phy', 'aux', 'qdss']
WHAT = {'tmel': 'TME lite', 'regdb': 'the regulatory database', 'bdf': 'the board file', 'phy': 'the PHY ucode',
        'aux': 'the AUX ucode', 'qdss': 'the QDSS configuration'}

# the two revisions: the files Android gives each; the set's base in the staged image; the six files' offsets
# past the base; and the set's whole size.  The peach_v2 set is as combined20.bin was, byte for byte: its
# slots were cut for the second revision's files, and the first revision's TME lite is larger than its slot
# there (63096 bytes against 0xe000), so every slot after it moves one on.
SETS = [
    dict(variant='peach', rev=10, base=0x890000, total=0x890000,
         note="the first revision: the set the stock's qca_cld3_peach gives the chip",
         places=[0x800000, 0x810000, 0x818000, 0x837000, 0x87e000, 0x88d000],
         names=dict(image='amss.bin', tmel='tmel_peach_10.elf', regdb='regdb.bin', bdf='bdwlang.elf',
                    phy='phy_ucode.elf', aux='aux_ucode.elf', qdss='qdss_trace_config_v1.cfg')),
    dict(variant='peach_v2', rev=20, base=0x0, total=0x88a861,
         note="the second revision: what the port was made on; at the image's start, where the image of one set began",
         places=[0x800000, 0x80e000, 0x814000, 0x833000, 0x87a000, 0x889000],
         names=dict(image='amss20.bin', tmel='tmel_peach_20.elf', regdb='regdb.bin', bdf='bdwlan.elf',
                    phy='phy_ucode20.elf', aux='aux_ucode20.elf', qdss='qdss_trace_config_v2.cfg')),
]


def fnv(b):
    h = 0xcbf29ce484222325
    for x in b:
        h ^= x
        h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h


def describe(b):
    return {'size': len(b), 'fnv': '0x%016x' % fnv(b), 'sha256': hashlib.sha256(b).hexdigest()}


def read_sets():
    """each set as it is in fw-wifi/: its image's bytes, and [(the file's key, its name, its offset, its bytes)]"""
    sets = []
    for s in SETS:
        where = os.path.join(FW, s['variant'])
        missing = [n for n in s['names'].values() if not os.path.isfile(os.path.join(where, n))]
        if missing:
            sys.exit('compose_combined: %s has no %s (pull-firmware.sh)' % (where, ', '.join(missing)))
        image = open(os.path.join(where, s['names']['image']), 'rb').read()
        files = []
        for key, offset in zip(ORDER, s['places']):
            files.append((key, s['names'][key], offset, open(os.path.join(where, s['names'][key]), 'rb').read()))
        # nothing of the set may run into what comes after it: the image into the first file, a file into the next
        ends = sorted(s['places'] + [s['total']])
        if len(image) > ends[0]:
            sys.exit('compose_combined: the %s set\'s %s (%d bytes) runs past %#x' % (s['variant'], s['names']['image'], len(image), ends[0]))
        for key, name, offset, b in files:
            limit = min(o for o in ends if o > offset)
            if offset + len(b) > limit:
                sys.exit('compose_combined: the %s set\'s %s (%d bytes at %#x) runs past %#x' % (s['variant'], name, len(b), offset, limit))
        sets.append(dict(s, image=image, files=files))
    return sets


def image_of(sets):
    """the staged image: every set's bytes at the set's base"""
    img = bytearray(max(s['base'] + s['total'] for s in sets))
    for s in sets:
        img[s['base']:s['base'] + len(s['image'])] = s['image']
        for key, name, offset, b in s['files']:
            img[s['base'] + offset:s['base'] + offset + len(b)] = b
    return bytes(img)


def as_table(sets):
    """what the loader's table has to say of these sets"""
    return [(s['variant'], s['base'], len(s['image']), fnv(s['image']),
             [(name, offset, len(b), fnv(b)) for key, name, offset, b in s['files']]) for s in sets]


def table_text(sets):
    """the table, as the header has it (between its own comment lines)"""
    lines = ['static const struct op13_wifi_set op13_wifi_sets[]={']
    for s in sets:
        lines.append(' /* %s */' % s['note'])
        lines.append(' {"%s",0x%x,0x%x,0x%016xULL,{' % (s['variant'], s['base'], len(s['image']), fnv(s['image'])))
        for i, (key, name, offset, b) in enumerate(s['files']):
            lines.append('  {0x%x,0x%x,0x%016xULL}%s /* %s */' % (offset, len(b), fnv(b), ',' if i + 1 < len(s['files']) else '', name))
        lines.append(' }},')
    lines.append('};')
    return '\n'.join(lines) + '\n'


def parse_table(text):
    """the table as a header has it, in as_table()'s shape; None when the header holds none this reads"""
    m = re.search(r'static const struct op13_wifi_set op13_wifi_sets\[\]=\{(.*?)\n\};', text, re.S)
    if not m:
        return None
    sets = []
    for name, base, image_bytes, image_fnv, body in re.findall(
            r'\s*\{"([a-z0-9_]+)",(0x[0-9a-f]+),(0x[0-9a-f]+),(0x[0-9a-f]+)ULL,\{(.*?)\}\},?', m.group(1), re.S):
        files = [(f.strip(), int(off, 16), int(size, 16), int(h, 16)) for off, size, h, f in re.findall(
            r'\{(0x[0-9a-f]+),(0x[0-9a-f]+),(0x[0-9a-f]+)ULL\}\s*,?\s*/\* ([^*]+) \*/', body)]
        sets.append((name, int(base, 16), int(image_bytes, 16), int(image_fnv, 16), files))
    return sets


def main():
    args = [a for a in sys.argv[1:]]
    check_only = '--check' in args
    want_header = '--header' in args
    rest = [a for a in args if not a.startswith('--')]
    tree = os.path.abspath(rest[0]) if rest else os.path.join(REPO, 'kernel')

    sets = read_sets()
    if want_header:
        sys.stdout.write(table_text(sets))
        return

    if not check_only:
        img = image_of(sets)
        with open(OUT, 'wb') as f:
            f.write(img)
        manifest = {}
        for s in sets:
            manifest[s['variant']] = {
                'revision': s['rev'], 'stock_driver': 'qca_cld3_' + s['variant'], 'base': s['base'], 'bytes': s['total'],
                'mhi': dict(name=s['names']['image'], **describe(s['image'])),
                'files': [dict(name=name, what=WHAT[key], offset=s['base'] + offset, **describe(b))
                          for key, name, offset, b in s['files']]}
            for key, name, offset, b in s['files']:
                print('%-8s %-28s at %#x: %d bytes, fnv %#x' % (s['variant'], name, s['base'] + offset, len(b), fnv(b)))
            print('%-8s %s: the MHI image, %d bytes at %#x' % (s['variant'], s['names']['image'], len(s['image']), s['base']))
        with open(os.path.join(FW, 'sets.json'), 'w') as f:
            json.dump(manifest, f, indent=1)
            f.write('\n')
        print('%s: %d bytes, the %s sets back to back' % (os.path.relpath(OUT, REPO), len(img), ' and '.join(manifest)))

    # the loader's table against the files: what the loader will look for has to be where it says
    header = os.path.join(tree, HEADER)
    if not os.path.isfile(header):
        if check_only:
            sys.exit('compose_combined: no %s in %s: not the common tree' % (HEADER, tree))
        print('no %s in %s: the table not checked (apply-kernel-patches.sh makes the tree)' % (HEADER, tree))
        return
    have = parse_table(open(header).read())
    if have is None:
        sys.exit('compose_combined: %s holds no table this reads' % header)
    want = as_table(sets)
    if have != want:
        for w, h in zip(want, have):
            if w[:4] != h[:4]:
                print('the %s set: the table says base %#x, an MHI image of %#x bytes summing to %#x; the files: %#x, %#x, %#x'
                      % (w[0], h[1], h[2], h[3], w[1], w[2], w[3]))
            for a, b in zip(w[4], h[4]):
                if a != b:
                    print('the %s set: the table says %s is at %#x, %#x bytes summing to %#x; the file: %#x, %#x, %#x'
                          % (w[0], b[0], b[1], b[2], b[3], a[1], a[2], a[3]))
            if len(w[4]) != len(h[4]):
                print('the %s set: %d files in the table, %d in fw-wifi/' % (w[0], len(h[4]), len(w[4])))
        sys.exit('compose_combined: the loader\'s table (%s) is not what fw-wifi/ composes to: the firmware was refreshed and '
                 'the table was not (--header says it afresh: it is in the port\'s patch, patches/)' % HEADER)
    print('the loader\'s table (%s) is what fw-wifi/ composes to' % HEADER)


if __name__ == '__main__':
    main()
