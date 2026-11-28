#!/usr/bin/env python3
"""hakios-datavol.py: an empty HFS Plus data volume -- iOS's own disk, kept in Android's userdata
(docs/data-persistence.md; tools/hakios-data.py create calls this) -- as a stream of the blocks it uses,
for tools/hakios-send.py to put onto the phone.

usage: hakios-datavol.py SIZE MARKER      (SIZE: 64G, 16M, ... a whole number of MiB; MARKER: 32 hex digits)
      -> the stream on standard output, what was made on standard error

HAKIOS_IMAGE=FILE: the volume is that file already made (with mkfs.hfsplus and, if wanted, files
mounted into it), not one made here: its own allocation bitmap says which blocks are sent.

Runs where a mkfs.hfsplus is: HAKIOS_MKFS names one, else the one in PATH (Linux's hfsprogs package puts it in
/sbin; a built tree has the build container's, which tools/hakios-hfsprogs.sh makes there). The volume is made with
mkfs.hfsplus the way the port's own data volume is (tools/mkdatavol.py): case-sensitive, journaled, in
blocks of 4096, root's and 0755 at its root, as on an iPhone -- with another owner Darwin takes it for a data volume
to migrate and stays at the progress bar.

Only the blocks the volume has allocated are sent, read from its own allocation bitmap (the volume headers, the
bitmap, the B-trees, the journal): what is in free space does not matter to HFS Plus, and sixty-four gigabytes need
not go over USB. They go as records, little endian:

    u64 first block, u64 count, count * 4096 bytes       (as many records as there are runs)

The marker goes into the volume's first block, at byte 2048: past the volume header (which ends at 1536) and before
the allocation file (which begins at block 8), where nothing of HFS Plus's ever comes. It says "this is the volume":
the loader looks for it there before it serves anything from the volume (xnuloader/loader.c, HAKIOS_MARKER_AT). That
the blocks are still where the map says is what the guard of every extent says (tools/hakios-pin/hakios-pin.c, "guard"):
a pinned file's blocks are not guaranteed to stay where they are, and a write by a stale map would land in whatever
Android keeps there instead.
"""
import os
import struct
import subprocess
import sys
import tempfile

BS = 4096
MARKER_AT = 2048


def size_blocks(text):
    if len(text) < 2 or not text[:-1].isdigit() or text[-1] not in 'MGT':
        sys.exit(__doc__)
    mib = int(text[:-1]) * {'M': 1, 'G': 1024, 'T': 1048576}[text[-1]]
    return mib * 1048576 // BS


def read_fork(img, fork):
    logical, = struct.unpack('>Q', fork[0:8])
    extents = [struct.unpack('>II', fork[16 + 8 * i:24 + 8 * i]) for i in range(8)]
    data = bytearray()
    for start, count in extents:
        if count:
            img.seek(start * BS)
            data += img.read(count * BS)
    if len(data) < logical:
        sys.exit(f'hakios-datavol: a fork of {logical} bytes is in more pieces than its header says')
    return data[:logical]


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    blocks = size_blocks(sys.argv[1])
    marker = bytes.fromhex(sys.argv[2])
    if len(marker) != 16:
        sys.exit('hakios-datavol: MARKER is 32 hex digits')
    given = os.environ.get('HAKIOS_IMAGE')
    if given:
        with open(given, 'rb') as img:
            stream(img, blocks, marker)
        return
    with tempfile.TemporaryDirectory(prefix='hakios-datavol.') as tmp:
        path = os.path.join(tmp, 'data.hfs')
        with open(path, 'wb') as f:
            f.truncate(blocks * BS)
        subprocess.run([os.environ.get('HAKIOS_MKFS', 'mkfs.hfsplus'), '-s', '-J', '-b', str(BS), '-U', '0', '-G', '0',
                        '-M', '755', '-v', 'Data', path],
                       check=True, stdout=subprocess.DEVNULL)
        with open(path, 'rb') as img:
            stream(img, blocks, marker)


def stream(img, blocks, marker):
    img.seek(1024)
    vh = img.read(512)
    sig, version = struct.unpack('>HH', vh[0:4])
    if sig not in (0x482B, 0x4858):                 # H+, HX
        sys.exit(f'hakios-datavol: no HFS Plus volume header in what mkfs made ({sig:#x})')
    block_size, total, free = struct.unpack('>III', vh[40:52])
    if block_size != BS or total != blocks:
        sys.exit(f'hakios-datavol: the volume has {total} blocks of {block_size}, wanted {blocks} of {BS}')
    bitmap = read_fork(img, vh[112:192])           # allocationFile (the forks begin at 112)
    if len(bitmap) * 8 < total:
        sys.exit('hakios-datavol: the allocation file is shorter than the volume')
    used = lambda b: bitmap[b >> 3] & (0x80 >> (b & 7))
    if not used(0):
        sys.exit('hakios-datavol: the volume does not count its first block as used')
    img.seek(0)
    first = bytearray(img.read(BS))
    if first[1536:4096] != bytes(BS - 1536):
        sys.exit('hakios-datavol: something is in the first block past the volume header: no room for the marker')
    first[MARKER_AT:MARKER_AT + 16] = marker
    out = sys.stdout.buffer
    records = sent = 0

    def record(first_block, count, data):
        nonlocal records, sent
        out.write(struct.pack('<QQ', first_block, count))
        out.write(data)
        records += 1
        sent += count

    runs = []                                     # [first, count]: the used blocks, in order
    for b in range(total):
        if used(b):
            if runs and runs[-1][1] + runs[-1][0] == b:
                runs[-1][1] += 1
            else:
                runs.append([b, 1])
    for first_block, count in runs:
        if first_block == 0:
            # the first block from memory: the marker is in it now
            img.seek(BS)
            record(0, count, bytes(first) + img.read((count - 1) * BS))
            continue
        img.seek(first_block * BS)
        record(first_block, count, img.read(count * BS))
    out.flush()
    print(f'hakios-datavol: {blocks} blocks ({blocks * BS >> 30} GiB), HFS Plus case-sensitive and journaled, '
          f'{free} blocks free; {sent} blocks in {records} records, the marker at byte {MARKER_AT} of the first',
          file=sys.stderr)


main()
