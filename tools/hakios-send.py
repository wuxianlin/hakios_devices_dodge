#!/usr/bin/env python3
"""hakios-send.py: an image onto the phone, a piece at a time, onto a pinned file's blocks of the raw storage.

usage: hakios-send.py STREAM NAME TOTAL_BLOCKS         the stream of tools/hakios-datavol.py (an HFS Plus volume:
                                                        records of the blocks it uses), small: a few megabytes
       hakios-send.py --raw IMAGE NAME TOTAL_BLOCKS    an image file as it is (the system image: its last chunk is
                                                        padded with zeros to a block), gigabytes
       (env ANDROID_SERIAL; HAKIOS_PIECE: blocks in a piece at first, 256; HAKIOS_MAX_PIECE: the most, 2048;
        HAKIOS_CHUNK: MiB in a pushed chunk, 128; HAKIOS_PAUSE: seconds between pieces, 0.2; HAKIOS_SKIP_ZERO=1:
        blocks that are zero are not sent -- for blocks that are zero on the raw device already, as
        tools/hakios-data.py makes them before it sends)

The image goes onto the phone in one of two ways, by its size.

A stream comes through the standard input of "adb shell". It is cut into pieces, and a piece is one
"hakios-pin putblocks FILE RAW FIRST COUNT" with the piece on its standard input, which reads its
blocks back from the raw device and compares them. Between pieces a short pause.

An image file is pushed as a file, a chunk at a time ("adb push", whose packets are 64 KiB), the
chunk's SHA-256 is compared with the host's on the phone, and the phone's own tool then copies it
onto the volume's raw blocks -- "hakios-pin putblocks FILE RAW FIRST COUNT" with the chunk on its
standard input -- every piece read back and compared as with a stream; the chunk is taken off the
phone at the end. It is not streamed over the standard input of "adb shell": there the phone's tool
reads as fast as it writes and checks, and while it was busy the host's adb gathered what it had
into packets of 300-500 KiB -- and on a VMware guest no USB transfer over 256 KiB is mapped at all
(tools/hakios_adblink.py says it), so piece after piece was lost, where three gigabytes pushed as a
file went through whole.

When the link drops -- adb fails, or the phone is gone -- the script waits for the phone and sends
the piece again (a chunk: pushes it again): what a piece wrote was compared as it landed, so a piece
sent twice is harmless. The tool's own exit status comes back through adb's shell protocol; that,
and nothing else, says a piece is done. And after a drop the link is looked at: where this
computer's kernel has said why it drops, the adb server is started anew with its native backend
(tools/hakios_adblink.py), in the same run.

How big a piece is, the link decides: after sixteen pieces in a row that went well the next is
twice the size (up to HAKIOS_MAX_PIECE, eight megabytes); one that did not go well makes the next a
quarter (thirty-two blocks at the least). A good link is soon sending megabytes at a time, and a bad
one keeps to what survives it.

Every block is written once and no more, and never past the image's end: it is checked before each
piece, so that a fault in this script cannot write a block of the phone's flash over and over.
"""
import hashlib
import os
import struct
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # hakios_adblink, beside this script
import hakios_adblink

BS = 4096
DIR = '/data/hakios'
RAW = '/dev/block/by-name/userdata'
REMOTE = '/data/local/tmp/hakios-pin'
TMP = '/data/local/tmp'                        # where a pushed chunk waits on the phone
TRIES = 60                                     # a link that flaps for a minute or two is outlived
CHUNK = 128 * 1024 * 1024                      # HAKIOS_CHUNK: what is pushed, and copied, at a time


def adb(*args, **kw):
    serial = os.environ.get('ANDROID_SERIAL', '').strip()
    return subprocess.run(['adb'] + (['-s', serial] if serial else []) + list(args), **kw)


def say(what, end='\n'):
    print(what, end=end, file=sys.stderr, flush=True)


def wait_phone(secs=600):
    """the phone back on adb, in Android; False when it did not come in time"""
    t0 = time.time()
    while time.time() - t0 < secs:
        try:
            r = adb('get-state', capture_output=True, text=True, timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            r = None
        if r is not None and r.returncode == 0 and r.stdout.strip() == 'device':
            return True
        time.sleep(3)
    return False


def last_line(r):
    text = (r.stdout + r.stderr).decode('utf-8', 'replace').replace('\r', '') if r is not None else ''
    lines = [l for l in text.splitlines() if l.strip()]
    return lines[-1] if lines else 'no answer'


def run(*args, timeout=300):
    """adb to the phone with nothing on its standard input: (its CompletedProcess or None, the last line it said)"""
    try:
        r = adb(*args, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, 'the link stopped answering'
    except OSError as e:
        return None, str(e)
    return r, last_line(r)


def refused(r, why):
    """the tool itself said no (its exit status, its words): again would be the same"""
    return r is not None and r.returncode == 2 and 'hakios-pin' in why


def after_a_drop(what, attempt, link):
    """a try did not go: said, the link cured where this computer's kernel says why it drops (True when it
    was), and the phone waited for. The caller gives it a few seconds before the next try"""
    say(f'hakios-send: {what}')
    cured = link.after_a_drop()
    if not wait_phone():
        sys.exit('hakios-send: the phone did not come back on adb')
    return cured


def send_piece(cmd, buf):
    """one piece on the standard input of adb shell: True when the tool took it; a string saying why not"""
    try:
        r = adb('shell', '-T', cmd, input=buf, capture_output=True, timeout=300)
    except subprocess.TimeoutExpired:
        return 'the link dropped, or the tool did not finish: the link stopped answering'
    except OSError as e:
        return 'the link dropped, or the tool did not finish: ' + str(e)
    if r.returncode == 0:
        return True
    why = last_line(r)
    if refused(r, why):
        sys.exit(f'hakios-send: {why}')
    return 'the link dropped, or the tool did not finish: ' + why[:120]


def send_stream(path, name, total, piece, most, pause, skip_zero, link):
    first_piece = piece
    with open(path, 'rb') as f:
        head = f.read(16)
        if len(head) != 16:
            sys.exit('hakios-send: the stream is empty')
    size = os.path.getsize(path)
    records, at = [], 0
    with open(path, 'rb') as f:
        while at < size:
            f.seek(at)
            first, count = struct.unpack('<QQ', f.read(16))
            at += 16
            if count == 0 or first + count > total or at + count * BS > size:
                sys.exit(f'hakios-send: the stream is not whole at byte {at}: a record of {count} blocks at {first}')
            records.append((first, count, at))
            at += count * BS
    if at != size:
        sys.exit('hakios-send: the stream does not end on a record')

    # Every block of the image is written once and no more: a bug in the loop below must not be able
    # to write a block of the phone's flash over and over (one did, once).
    total_blocks = sum(count for _, count, _ in records)
    written = set()
    sent = skipped = streak = 0
    t0 = time.time()
    with open(path, 'rb') as f:
        for first, count, offset in records:
            start = 0
            while start < count:
                n = min(piece, count - start)
                f.seek(offset + start * BS)
                buf = f.read(n * BS)
                if len(buf) < n * BS:                 # the image's last piece, short of a block: zeros to its end
                    buf += bytes(n * BS - len(buf))
                if skip_zero and buf.count(0) == len(buf):
                    skipped += n
                    start += n
                    continue
                block = first + start
                if sent + skipped + n > total_blocks or any(b in written for b in range(block, block + n)):
                    sys.exit(f'hakios-send: blocks {block}..{block + n - 1} would be written twice or past the '
                             f'image: stopped before anything was sent of them')
                cmd = f'su -c "{REMOTE} putblocks {DIR}/{name} {RAW} {block} {n}"'
                for attempt in range(1, TRIES + 1):
                    how = send_piece(cmd, buf)
                    if how is True:
                        break
                    piece = max(32, piece // 4)
                    # (the phone's USB has been seen to drop and come back every few seconds for a minute or
                    # two: each try is made a few seconds on, and the piece is small until the link holds)
                    if after_a_drop(f'blocks {block}..{block + n - 1}, try {attempt}: {how}', attempt, link):
                        piece = first_piece     # the adb server is another one now: pieces as at the start
                    time.sleep(4 if attempt < 20 else 10)
                else:
                    sys.exit(f'hakios-send: blocks {block}..: {TRIES} tries, no good')
                sent += n
                written.update(range(block, block + n))
                start += n                  # on to the next piece: without this the same one is sent for ever
                streak += 1
                if streak % 16 == 0 and piece < most:
                    piece = min(most, piece * 2)
                say(f'\r{name}: {sent * BS / 2**20:.0f} MiB written in {time.time() - t0:.0f} s, '
                    f'{piece} blocks a piece', end='')
                time.sleep(pause)
    say('')
    say(f'hakios-send: {sent} blocks ({sent * BS / 2**20:.0f} MiB) onto {name} in {time.time() - t0:.0f} s; '
        f'{skipped} blocks of zeros not sent')


def push_chunk(local, remote, digest):
    """a chunk onto the phone as a file, and its SHA-256 as the phone sums it against the host's: True when
    the phone holds the chunk; a string saying why not, when to push it again"""
    try:
        r = adb('push', local, remote, capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        return 'the push did not finish: the link stopped answering'
    except OSError as e:
        return 'the push did not finish: ' + str(e)
    if r.returncode != 0:
        return 'the push did not finish: ' + last_line(r)[:120]
    r, why = run('shell', 'sha256sum', remote, timeout=120)
    if r is None or r.returncode != 0:
        return 'no sum of the chunk came back: ' + why[:120]
    if not r.stdout.decode('utf-8', 'replace').strip().startswith(digest):
        return 'what the phone holds is not the chunk: its SHA-256 is not the host\'s'
    return True


def send_file(path, name, total, skip_zero, link):
    """an image file: pushed a chunk at a time, summed there, and copied onto the volume's raw blocks by the
    phone's own tool (every piece of it read back and compared there)"""
    chunk = max(1, int(os.environ.get('HAKIOS_CHUNK', str(CHUNK // 2**20)))) * 1024 * 1024
    chunk -= chunk % BS
    per = chunk // BS
    remote = f'{TMP}/hakios-chunk-{name}.bin'
    size = os.path.getsize(path)
    fd, local = tempfile.mkstemp(prefix='hakios-chunk.', suffix='.bin')
    os.close(fd)
    written = set()                              # the blocks a chunk has been put onto (the cap, as with a stream)
    sent = skipped = 0
    t0 = time.time()
    try:
        with open(path, 'rb') as img:
            for first in range(0, total, per):
                n = min(per, total - first)
                img.seek(first * BS)
                buf = img.read(n * BS)
                if len(buf) < n * BS:             # the image's last chunk, short of a block: zeros to its end
                    buf += bytes(n * BS - len(buf))
                if skip_zero and buf.count(0) == len(buf):
                    skipped += n
                    continue
                digest = hashlib.sha256(buf).hexdigest()
                with open(local, 'wb') as f:
                    f.write(buf)
                for attempt in range(1, TRIES + 1):
                    how = push_chunk(local, remote, digest)
                    if how is True:
                        break
                    after_a_drop(f'chunk {first}..{first + n - 1}, try {attempt}: {how}', attempt, link)
                    time.sleep(4 if attempt < 20 else 10)
                else:
                    sys.exit(f'hakios-send: chunk {first}..: {TRIES} tries, the phone does not hold it')
                if first + n > total or any(b in written for b in range(first, first + n)):
                    sys.exit(f'hakios-send: blocks {first}..{first + n - 1} would be written twice or past the '
                             f'image: stopped before anything was written of them')
                cmd = f'su -c "{REMOTE} putblocks {DIR}/{name} {RAW} {first} {n} < {remote}"'
                for attempt in range(1, TRIES + 1):
                    r, why = run('shell', '-T', cmd)
                    if r is not None and r.returncode == 0:
                        break
                    if refused(r, why):
                        sys.exit(f'hakios-send: {why}')
                    after_a_drop(f'chunk {first}..{first + n - 1} was not put, try {attempt}: {why}', attempt, link)
                    time.sleep(4)
                else:
                    sys.exit(f'hakios-send: chunk {first}..: {TRIES} tries, not put')
                sent += n
                written.update(range(first, first + n))
                say(f'\r{name}: {sent * BS / 2**20:.0f} MiB put in {time.time() - t0:.0f} s, {n} blocks a chunk',
                    end='')
    finally:
        try:
            run('shell', 'rm', '-f', remote, timeout=60)       # the chunk is not left on the phone
            os.unlink(local)
        except OSError:
            pass
    say('')
    say(f'hakios-send: {sent} blocks ({sent * BS / 2**20:.0f} MiB) onto {name} in {time.time() - t0:.0f} s; '
        f'{skipped} blocks of zeros not sent')


def main():
    argv = sys.argv[1:]
    raw = bool(argv) and argv[0] == '--raw'
    if raw:
        argv = argv[1:]
    if len(argv) != 3 or not argv[2].isdigit() or not argv[1] or any(c in argv[1] for c in '/ \t\r\n"\\\''):
        sys.exit(__doc__)
    path, name, total = argv[0], argv[1], int(argv[2])
    piece = max(1, int(os.environ.get('HAKIOS_PIECE', '256')))
    most = max(piece, int(os.environ.get('HAKIOS_MAX_PIECE', '2048')))
    pause = float(os.environ.get('HAKIOS_PAUSE', '0.2'))
    skip_zero = os.environ.get('HAKIOS_SKIP_ZERO') == '1'
    if raw and os.path.getsize(path) > total * BS:
        sys.exit(f'hakios-send: {path} is {os.path.getsize(path)} bytes, more than the {total} blocks of the volume')
    link = hakios_adblink.Link('adb', say)      # the link cured where this computer's kernel says why it drops
    if raw:
        send_file(path, name, total, skip_zero, link)
    else:
        send_stream(path, name, total, piece, most, pause, skip_zero, link)


main()
