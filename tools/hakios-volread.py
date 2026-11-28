#!/usr/bin/env python3
"""hakios-volread.py: the data volume as it stands on the phone's raw storage, looked at from here --
without booting iOS.

usage: hakios-volread.py NAME [--full] [--fsck] [--image OUT]
       hakios-volread.py NAME --ls /PATH              what is in that folder of the volume
       hakios-volread.py NAME --cat /PATH [--sha256]  a file of the volume: its bytes, or its SHA-256 and size
       (env ANDROID_SERIAL; --fsck needs the build container, where fsck.hfsplus is)

NAME is the volume's file in Android's /data/hakios (tools/hakios-data.py create; "hakios-data.py look" is
this). Its blocks are read one of two ways, by where the phone is: in Android (with root) through
hakios-pin's "getblocks", which maps the file's blocks live, 64 at a time (HAKIOS_PIECE); in the loader
through "fastboot oem ufs-read hakios_data", 16 blocks at a time -- which is how a volume is looked at
right after a run, with the map the run used still in the loader. A loader that has no map is given
NAME.map from beside this script's folder (MAP=path: another) -- a map on paper, which may be out of
date: the loader then checks the volume against it and refuses if it has moved (look from Android
instead). Either way the first block (the HFS Plus volume header) and
the blocks of the catalog file are what is read. From those: what the volume is (its name, its size, how much of it is in use,
how many times it has been written), and what is in it -- the entries of its root, which is Darwin's
/private/var: its entries by default, the whole tree with --full.

  --ls PATH     the entries of a folder (paths are as Darwin sees them: /db is /private/var/db)
  --cat PATH    a file's contents as they stand on the raw storage -- its SHA-256 and size with --sha256, which
                is how a file is followed from one boot to the next
  --fsck        every block the volume has allocated is read as well, and the volume is put together
                again as a sparse image; fsck.hfsplus -n then says whether it is a volume in good
                order -- worth a run after iOS has used it. fsck.hfsplus: HAKIOS_FSCK's, the one in
                PATH or /sbin (Linux's hfsprogs package), or the build container's.
  --image OUT   where --fsck puts the image (default: a temporary one, removed afterwards)

A link that drops under long transfers is survived: a piece is 64 blocks, and one that does not
come back whole is asked for again.
"""
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

BS = 4096
DIR = '/data/hakios'
RAW = '/dev/block/by-name/userdata'
REMOTE = '/data/local/tmp/hakios-pin'
PIECE = int(os.environ.get('HAKIOS_PIECE') or 64)     # blocks at a time, through Android
MAC_EPOCH = 2082844800                 # seconds between 1904-01-01 (where HFS Plus counts from) and 1970-01-01
HERE = os.path.dirname(os.path.abspath(__file__))


class Unanswered:
    """what a call that timed out, or could not be made, answers: nothing"""
    said = b''
    stdout = b''
    returncode = -1


def adb(*args, **kw):
    """adb to the phone; its CompletedProcess (Unanswered when the link stopped answering)"""
    serial = os.environ.get('ANDROID_SERIAL', '').strip()
    try:
        return subprocess.run(['adb'] + (['-s', serial] if serial else []) + list(args),
                              timeout=kw.pop('timeout', 300), **kw)
    except (subprocess.TimeoutExpired, OSError):
        return Unanswered()


def fastboot(*args, **kw):
    """fastboot's reply and its text together (the tool prints the value of a getvar on standard error); Unanswered
    when the link stopped answering"""
    try:
        r = subprocess.run(['fastboot'] + list(args), capture_output=True, timeout=kw.pop('timeout', 60), **kw)
    except (subprocess.TimeoutExpired, OSError):
        return Unanswered()
    r.said = r.stdout + r.stderr
    return r


def in_loader():
    return b'xnuloader' in fastboot('getvar', 'product').said


def loader_map(name):
    """the loader holds the map of NAME's blocks: the run's, or NAME.map sent if it has none"""
    m = fastboot('getvar', 'hakios-map')
    if m.returncode == 0 and b'none' not in m.said and b'extents' in m.said:
        return True
    path = os.environ.get('MAP') or os.path.join(os.path.dirname(HERE), f'{name}.map')
    if not os.path.isfile(path):
        sys.exit(f'hakios-volread: the loader has no map of {name} (this is not the loader a run with it was made '
                 f'in), and there is no {path}: look at the volume from Android instead')
    if b'OKAY' not in fastboot('oem', 'hakios-map').said or b'OKAY' not in fastboot('stage', path).said:
        sys.exit(f'hakios-volread: the loader would not take {path}')
    fastboot('oem', 'hakios-info', timeout=30)
    verdict = fastboot('getvar', 'hakios-volume').said.decode('utf-8', 'replace').splitlines()
    verdict = verdict[0].split(':', 1)[-1].strip() if verdict else ''
    if not verdict.startswith('whole'):
        sys.exit(f'hakios-volread: the loader says of {name}: "{verdict}" -- {path} is not where the volume is '
                 f'now; look at it from Android, where its blocks are read as Android maps them')
    return True


def get_blocks_loader(first, count):
    out = b''
    while len(out) < count * BS:
        at = first + len(out) // BS
        n = min(16, count - len(out) // BS)
        for _ in range(10):
            r = fastboot('oem', 'ufs-read', 'hakios_data', str(at), str(n))
            if b'OKAY' in r.said:
                fd, tmp = tempfile.mkstemp(prefix='hakios-volread.')
                os.close(fd)
                try:
                    if fastboot('get_staged', tmp).returncode == 0 and os.path.getsize(tmp) == n * BS:
                        with open(tmp, 'rb') as f:
                            out += f.read()
                        break
                finally:
                    os.unlink(tmp)
            for _ in range(30):             # the loader answers again, or this has to give up
                if in_loader():
                    break
                time.sleep(2)
        else:
            sys.exit(f'hakios-volread: blocks {at}..{at + n} did not come back whole')
    return out


def wait_phone():
    for _ in range(100):
        r = adb('get-state', capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip() == 'device':
            return
        time.sleep(3)
    sys.exit('hakios-volread: the phone did not come back on adb')


def get_blocks(name, first, count):
    """count blocks of the volume from first on, as the raw device holds them"""
    out = b''
    while len(out) < count * BS:
        at = first + len(out) // BS
        n = min(PIECE, count - len(out) // BS)
        for _ in range(10):
            r = adb('exec-out', f'su -c "{REMOTE} getblocks {DIR}/{name} {RAW} {at} {n}"', capture_output=True)
            if len(r.stdout) == n * BS:
                out += r.stdout
                break
            wait_phone()
        else:
            sys.exit(f'hakios-volread: blocks {at}..{at + n} did not come back whole')
    return out


def find_fsck():
    """a fsck.hfsplus on this computer: HAKIOS_FSCK's, PATH's, or where Linux's hfsprogs package puts it"""
    p = os.environ.get('HAKIOS_FSCK', '').strip()
    if p:
        return p
    for d in os.environ.get('PATH', '').split(os.pathsep) + ['/sbin', '/usr/sbin', '/usr/local/sbin']:
        p = os.path.join(d, 'fsck.hfsplus')
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return ''


def u16s(b):
    return b.decode('utf-16-be', 'replace')


def mac_time(t):
    return time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(t - MAC_EPOCH)) if t else '-'


def fork(data, at):
    """HFSPlusForkData at at: its logical size, and [(startBlock, blockCount)] from its first eight extents"""
    logical, = struct.unpack('>Q', data[at:at + 8])
    return logical, [(s, c) for s, c in (struct.unpack('>II', data[at + 16 + 8 * i:at + 24 + 8 * i]) for i in range(8)) if c]


def read_fork(get, f):
    logical, extents = fork(*f)
    data = b''.join(get(s, c) for s, c in extents)
    if len(data) < logical:
        sys.exit('hakios-volread: a file of the volume is in more extents than its header says (not walked here)')
    return data[:logical]


def catalog(data):
    """the records of the catalog B-tree's leaf nodes: (parentID, name, kind, size, ownID)
    (kind 'folder': size is its valence, ownID the folder's; kind 'file': size is its data fork's)"""
    depth, root, leaf_records, first_leaf, last_leaf, node_size = struct.unpack('>HIIIIH', data[14:34])
    out, node, seen = [], first_leaf, set()
    while node not in seen and (node + 1) * node_size <= len(data):
        seen.add(node)
        base = node * node_size
        forward, backward, kind, height, nrec = struct.unpack('>IIbbH', data[base:base + 12])
        if kind not in (-1, 0xff):             # a leaf node's descriptor says 0xff
            break
        for i in range(nrec):
            offset, = struct.unpack('>H', data[base + node_size - 2 * (i + 1):base + node_size - 2 * i])
            rec = data[base + offset:]
            key_len, parent = struct.unpack('>HI', rec[0:6])
            name_len, = struct.unpack('>H', rec[6:8])
            name = u16s(rec[8:8 + 2 * name_len])
            body = rec[8 + 2 * name_len + 2 * name_len % 2:]    # the record's data is 16-bit aligned
            rtype, = struct.unpack('>H', body[0:2])
            if rtype == 1:                      # a folder: flags, valence, its own id
                valence, own = struct.unpack('>II', body[4:12])
                out.append((parent, name, 'folder', valence, own, None))
            elif rtype == 2:                    # a file: its data fork's logical size (the fork is at 88)
                size, = struct.unpack('>Q', body[88:96])
                out.append((parent, name, 'file', size, 0, body))
        node = forward
    return out


def at_path(entries, path):
    """the record of the folder or file at path, from the volume's root; None when there is none"""
    parent, rec = 2, None
    for part in [p for p in path.split('/') if p]:
        rec = next((e for e in entries if e[0] == parent and e[1] == part), None)
        if rec is None:
            return None
        parent = rec[4]
    return rec


def read_file(get, rec):
    """a file's bytes: its data fork's first eight extents (more are in the extents overflow file: not walked)"""
    fork = rec[5][88:168]
    logical, = struct.unpack('>Q', fork[0:8])
    extents = [(s, c) for s, c in (struct.unpack('>II', fork[16 + 8 * i:24 + 8 * i]) for i in range(8)) if c]
    data = b''.join(get(s, c) for s, c in extents)[:logical]
    if len(data) < logical:
        sys.exit(f'hakios-volread: {rec[1]!r} is in more extents than its header carries (not read here)')
    return data


def main():
    argv = sys.argv[1:]
    image_out = argv[argv.index('--image') + 1] if '--image' in argv and argv.index('--image') + 1 < len(argv) else None
    taken = {image_out}                           # the values of the options that have one
    for option in ('--ls', '--cat'):
        if option in argv and argv.index(option) + 1 < len(argv):
            taken.add(argv[argv.index(option) + 1])
    names = [a for a in argv if not a.startswith('--') and a not in taken]
    if len(names) != 1:
        sys.exit(__doc__)
    name, flags = names[0], [a for a in argv if a.startswith('--')]
    t0 = time.time()
    if in_loader():
        loader_map(name)
        print(f'(read through the loader, from the map of {name})')
        get = get_blocks_loader
    else:
        tool = os.path.join(HERE, 'hakios-pin', 'hakios-pin')
        if os.path.isfile(tool):
            adb('push', tool, REMOTE, capture_output=True)
        get = lambda first, count: get_blocks(name, first, count)

    block0 = get(0, 1)
    vh = block0[1024:1536]
    sig, version = struct.unpack('>HH', vh[0:4])
    if sig not in (0x482B, 0x4858):
        sys.exit(f'hakios-volread: no HFS Plus volume at {name}\'s first block (signature {sig:#x})')
    block_size, total, free = struct.unpack('>III', vh[40:52])
    files, folders = struct.unpack('>II', vh[32:40])
    write_count, = struct.unpack('>I', vh[68:72])
    created, modified, checked = struct.unpack('>III', vh[16:28])
    name_len, = struct.unpack('>H', block0[1024 + 502:1024 + 504])      # volumeName: the header's last member
    print(f'{name}: HFS Plus volume "{u16s(block0[1024 + 504:1024 + 504 + 2 * name_len])}", '
          f'{"case-sensitive" if sig == 0x4858 else "case-preserving"}, {total * block_size / 2**30:.2f} GiB in blocks '
          f'of {block_size}, {(total - free) * block_size / 2**20:.0f} MiB in use')
    print(f'   {folders} folders, {files} files, written {write_count} times; made {mac_time(created)}, '
          f'last written {mac_time(modified)}, last checked {mac_time(checked)}')
    entries = catalog(read_fork(get, (vh, 272)))                     # the forks: allocation 112, extents 192, catalog 272
    by_parent = {}
    for e in entries:
        by_parent.setdefault(e[0], []).append(e)

    def show(parent, prefix, depth):
        for _, n, kind, size, own, _body in sorted(by_parent.get(parent, []), key=lambda e: e[1]):
            if kind == 'folder':
                print(f'{prefix}{n}/   ({size} entries)')
                if depth > 1:
                    show(own, prefix + '   ', depth - 1)
            else:
                print(f'{prefix}{n}   ({size} bytes)')

    print(f'   its root, which Darwin mounts as /private/var ({len(by_parent.get(2, []))} entries):')
    show(2, '      ', 1000 if '--full' in flags else 1)
    print(f'   ({len(entries)} catalog records; {(time.time() - t0):.0f} s)')
    if '--ls' in argv:
        rec = at_path(entries, argv[argv.index('--ls') + 1])
        if rec is None or rec[2] != 'folder':
            sys.exit(f'hakios-volread: no folder {argv[argv.index("--ls") + 1]!r} in the volume')
        for _, n, kind, size, _, _body in sorted((e for e in entries if e[0] == rec[4]), key=lambda e: e[1]):
            print(f'      {n}/   ({size} entries)' if kind == 'folder' else f'      {n}   ({size} bytes)')
        return
    if '--cat' in argv:
        rec = at_path(entries, argv[argv.index('--cat') + 1])
        if rec is None or rec[2] != 'file':
            sys.exit(f'hakios-volread: no file {argv[argv.index("--cat") + 1]!r} in the volume')
        data = read_file(get, rec)
        if '--sha256' in argv:
            print(f'   {argv[argv.index("--cat") + 1]}: {len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()}')
        else:
            sys.stdout.buffer.write(data)
        return
    if '--fsck' not in flags:
        return

    bitmap = read_fork(get, (block0[:0] + vh, 112))                 # the allocation file's fork
    if len(bitmap) * 8 < total:
        sys.exit('hakios-volread: the allocation file is shorter than the volume')
    runs = []                                                        # [first, end)
    for b in range(total):
        if bitmap[b >> 3] & (0x80 >> (b & 7)):
            if runs and runs[-1][1] == b:
                runs[-1][1] += 1
            else:
                runs.append([b, b + 1])
    fsck = find_fsck()
    if image_out is None:
        # a temporary one: where this computer's fsck can read it, or else where the build container can
        base = tempfile.gettempdir() if fsck else os.path.join(HERE, '..', 'kernel', 'userland')
        path = os.path.join(base, f'.hakios-volread.{os.getpid()}.hfs')
    else:
        path = image_out
    os.makedirs(os.path.dirname(path), exist_ok=True)
    digest, got = hashlib.sha256(), 0
    with open(path, 'wb') as img:
        for start, end in runs:
            data = get(start, end - start)
            img.seek(start * BS)
            img.write(data)
            digest.update(data)
            got += end - start
        img.truncate(total * BS)
    print(f'   {got * BS / 2**20:.0f} MiB in {len(runs)} runs read back into {path} '
          f'(sha256 of it {digest.hexdigest()}; {(time.time() - t0):.0f} s)')
    if fsck:
        r = subprocess.run([fsck, '-n', path], capture_output=True, text=True)
    elif os.path.realpath(path).startswith(os.path.realpath(os.path.join(HERE, '..', 'kernel'))):
        r = subprocess.run(['docker', 'exec', '-u', '0', os.environ.get('HAKIOS_CTR', 'hakios-ctr'), 'fsck.hfsplus', '-n',
                            os.path.realpath(path)], capture_output=True, text=True)
    else:
        print('   (not fscked: no fsck.hfsplus on this computer -- Linux\'s hfsprogs package has it -- and the build'
              ' container cannot read that path)')
        r = None
    if r is not None:
        print('   fsck.hfsplus -n: ' + ((r.stdout + r.stderr).strip().replace('\n', '\n      ') or f'exit {r.returncode}'))
    if image_out is None:
        os.unlink(path)


main()
