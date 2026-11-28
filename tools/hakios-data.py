#!/usr/bin/env python3
"""hakios-data.py: HakiOS's data volume -- iOS keeping /private/var in Android's /data from boot to boot.

usage: hakios-data.py create [SIZE]  make the data volume (SIZE: 64G, 1G, 512M ... a whole number of
                                        MiB; 64G by default)
       hakios-data.py boot [SECS]       boot iOS with it (SECS: the session's seconds, 900 by default)
       hakios-data.py delete [-y]       remove it: what iOS kept on it goes, Android has the blocks again
       hakios-data.py list              the volumes there are, and whether Android still holds each in place
       hakios-data.py look [options]    what a volume holds, read from the phone's raw storage without iOS
                                        (--full, --ls /PATH, --cat /PATH [--sha256], --fsck: tools/hakios-volread.py)
       hakios-data.py system-create [SYSTEM.APKS]
                                        put this build's system image on the phone the same way (its file is
                                        "system"): "boot" then has the loader read it from the phone in seconds,
                                        where uploading its 2.4 GB over USB took a minute. Once per build
       hakios-data.py system-delete [-y]
                                        it off the phone: "boot" uploads the system image again
       (env NAME: the volume's name in Android's /data/hakios, "data" by default; ANDROID_SERIAL; HAKIOS_MKFS;
        for "boot" as for the launcher: CMDLINE, STAY; NOWIFI=1: without the Wi-Fi bring-up)

data-create.sh, data-boot.sh, data-delete.sh, system-create.sh and system-delete.sh -- at the root of
this repository and of the release zip -- are these. Python 3, adb and fastboot (Android's
platform-tools) are all that is needed; Linux, or anything else that has mkfs.hfsplus for "create".

The phone reaches the computer over adb, and on a VMware guest no USB transfer over 256 KiB is mapped,
which drops the link under load with adb's libusb backend (tools/hakios_adblink.py says it): an image
file is pushed as a file, not streamed over the standard input of "adb shell" (tools/hakios-send.py), and
where the kernel's log has its line for it the adb server is started anew with the native backend, which
writes in small pieces when a whole packet is refused -- before the first byte goes to the phone.

How it works (docs/data-persistence.md). Android's userdata is encrypted below its file system on
this phone (dm-default-key), so nothing written through Android's file system could be read back
from outside Android. The volume is an HFS Plus file system that lives in the blocks of a file in
/data/hakios: the file is pinned and its size reserved (f2fs keeps a pinned file's blocks where they
are), and the image is written onto those blocks of the raw partition in plaintext -- where the
loader gives it to the kernel as a block device, and init mounts it on /private/var.

"create" and "delete" need the phone in Android with root (adb shell su), and nothing else of the
phone's. "boot" needs that too, and here is why: a pin is not a guarantee -- with the storage nearly
full, Android's foreground compaction moves a pinned file's blocks a section at a time -- so the map
of the volume's blocks is taken afresh from Android each time, a moment before the phone leaves
Android, with a guard (a sum) of the first and the last block of every extent; and the loader serves
the volume only when every one of them still holds its guard. iOS is started with "ufs=2
hakios_data=1 var_hfs=ufs_hakios_data", and the run ends with the phone back in Android.

The system image is put on the phone the same way (a pinned file's blocks, read only, guarded as the
data volume is: "oem hakios-sysmap" and "oem hakios-resident" in the loader), and "boot" has the
loader read it from there into the RAM disk's place when it is this build's -- by the SHA-256 of
the image beside the scripts -- and uploads it otherwise, as it always did.

Nothing here touches a partition table: what is written is the volume's own blocks of userdata,
every piece read back and compared as it lands. Once a volume is made its file must not be written,
truncated or deleted through Android (a file manager with root could): Android would hand its blocks
out again. A factory reset takes the volume; an OTA update does not.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                        # hakios_adblink, beside this script
import hakios_adblink  # noqa: E402

HOME = os.path.dirname(HERE)                      # this repository, or the release zip's folder
IN_REPO = os.path.isfile(os.path.join(HOME, 'launcher', 'hackios.py'))
TREE = os.path.join(HOME, 'kernel') if IN_REPO else HOME       # a built tree's pieces
LAUNCHER = os.path.join(HOME, 'launcher' if IN_REPO else '', 'hackios.py')
PIN = os.path.join(HERE, 'hakios-pin', 'hakios-pin')            # the phone-side tool, built
SEND = os.path.join(HERE, 'hakios-send.py')
VOLREAD = os.path.join(HERE, 'hakios-volread.py')

DIR = '/data/hakios'                              # on the phone
RAW = '/dev/block/by-name/userdata'
REMOTE = '/data/local/tmp/hakios-pin'
CTR = os.environ.get('HAKIOS_CTR', 'hakios-ctr')
BS = 4096
MIB = 1048576
ZERO_CHUNK = 8 * 1024 ** 3 // BS                  # blocks zeroed in one call: eight gigabytes
SETTLE = float(os.environ.get('HAKIOS_SETTLE', '8'))   # seconds after the phone comes up on adb before anything else is asked



def in_dir(d, name):
    """d/name (or .exe: Windows) when it is there"""
    for f in (name, name + '.exe'):
        p = os.path.join(d, f)
        if os.path.isfile(p):
            return p
    return ''


def platform_tools():
    """Android's platform-tools where a release zip or the SDK keeps them -- beside this script's folder, or under
    ANDROID_HOME / ANDROID_SDK_ROOT; '' when they are to be PATH's"""
    for base in (HOME,) + tuple(os.environ.get(v, '').strip() for v in ('ANDROID_HOME', 'ANDROID_SDK_ROOT', 'ANDROID_SDK_HOME')):
        if base and in_dir(os.path.join(base, 'platform-tools'), 'adb') and in_dir(os.path.join(base, 'platform-tools'), 'fastboot'):
            return os.path.join(base, 'platform-tools')
    return ''


PT = platform_tools()
ADB = in_dir(PT, 'adb') or 'adb'
FASTBOOT = in_dir(PT, 'fastboot') or 'fastboot'
PATH_EXTRA = PT


MAKING = ''                                      # the file "create" is in the middle of making, if one


def die(what):
    sys.exit('hakios-data: ' + what + (f' (a {MAKING} may be left behind: "delete" takes it out)' if MAKING else ''))


def need_tools():
    """adb and fastboot: found where they are, or said where to get them"""
    (os.path.isabs(ADB) or shutil.which(ADB)) and (os.path.isabs(FASTBOOT) or shutil.which(FASTBOOT)) or die(
        'adb and fastboot (Android\'s platform-tools) are nowhere to be found: not in PATH, not in a platform-tools/'
        ' folder beside the scripts, not under ANDROID_HOME or ANDROID_SDK_ROOT -- '
        'https://developer.android.com/studio/releases/platform-tools')


def adb_link():
    """the adb server with its native USB backend on a computer whose kernel has said the link drops there with
    the other one (tools/hakios_adblink.py): asked of every command here before the first byte goes to the phone"""
    link = hakios_adblink.Link(ADB, lambda what: print('   ' + what, flush=True))
    if link.at_start():
        print('== the adb server started anew with ADB_LIBUSB=0: the backend with which the link holds', flush=True)


def env(**more):
    """the environment for what runs below: never an ANDROID_SERIAL that is set and empty (adb and fastboot
    would wait for a phone of no serial), the platform-tools folder in PATH when there is one"""
    e = {k: v for k, v in os.environ.items() if k != 'ANDROID_SERIAL' or v.strip()}
    if PATH_EXTRA:
        e['PATH'] = PATH_EXTRA + os.pathsep + e.get('PATH', '')
    e.update(more)
    return e


def serial_args():
    serial = os.environ.get('ANDROID_SERIAL', '').strip()
    return ['-s', serial] if serial else []


def adb(*args, stdin=None, timeout=60):
    """adb to the phone; its CompletedProcess (None when it did not answer in time)"""
    try:
        return subprocess.run([ADB] + serial_args() + list(args), input=stdin, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def text_of(r):
    return (r.stdout + r.stderr).decode('utf-8', 'replace').replace('\r', '') if r is not None else ''


def fb(*args, timeout=20):
    """fastboot's reply and its text together"""
    try:
        r = subprocess.run([FASTBOOT] + list(args), capture_output=True, text=True, timeout=timeout, env=env())
        return r.stdout + r.stderr
    except (subprocess.TimeoutExpired, OSError):
        return ''


def state():
    """where the phone is: android, fastboot (its bootloader), loader, or absent"""
    if 'fastboot' in fb('devices', timeout=10):
        return 'loader' if 'xnuloader' in fb('getvar', 'product', timeout=10) else 'fastboot'
    r = adb('get-state', timeout=10)
    if r is not None and r.stdout.strip() == b'device':
        return 'android'
    return 'absent'


def wait_android(secs=300):
    """Android up and booted; False when it did not come in time. Nothing but "get-state" is asked of the phone in
    the first seconds after it comes up: its USB link has been seen to drop under traffic that came at once"""
    t0 = time.time()
    seen = 0.0
    while time.time() - t0 < secs:
        if not seen and time.time() - t0 > 30 and fb('getvar', 'product', timeout=10).count('xnuloader'):
            die('this port\'s loader did not reset the phone: hold power for about ten seconds to leave it, '
                'then run this again')
        r = adb('get-state', timeout=10)
        if r is not None and r.stdout.strip() == b'device':
            if not seen:
                seen = time.time()
            if time.time() - seen >= SETTLE:
                r = adb('shell', 'getprop', 'sys.boot_completed', timeout=15)
                if r is not None and r.stdout.strip() == b'1':
                    return True
        time.sleep(3)
    return False


def to_android(why):
    """the phone in Android, its system up: from the loader or the bootloader by "fastboot reboot\""""
    if state() == 'android':
        wait_android() or die('Android did not finish starting')
        return
    if state() == 'absent':
        die('the phone is not on USB (it is to be in Android, or in its bootloader or the loader)')
    print(f'== {why}: back to Android first (about a minute)')
    sys.stdout.flush()
    fb('reboot', timeout=15)
    wait_android() or die('the phone did not come back to Android (if nothing happens: hold power for about '
                          'ten seconds)')


def su(script, timeout=60):
    """a few shell commands on the phone as root -- the script on standard input, for a ';' or a '|' in a command
    line would be cut by the phone's own shell: (the exit status, what it said)"""
    r = adb('shell', '-T', 'su -c sh', stdin=script.encode(), timeout=timeout)
    return (1, '') if r is None else (r.returncode, text_of(r))


def last_line(what):
    lines = [l for l in what.splitlines() if l.strip()]
    return lines[-1] if lines else 'nothing said'


PROGRESS = re.compile(r'hakios-pin: (hashed|put |rewritten |zeroed |written )')


def pin(*args, timeout=3600):
    """hakios-pin on the phone, as root: (its exit status, what it said without its progress lines). A link that
    drops under the call is waited for and the call made again; what the tool refused (2) is not asked twice"""
    command = f'su -c "{REMOTE} ' + ' '.join(str(a) for a in args) + '"'
    for attempt in range(40):                 # the phone's USB has been seen to flap for a minute or two
        r = adb('shell', '-T', command, timeout=timeout)
        said = text_of(r)
        if r is not None and (r.returncode != 1 or 'hakios-pin' in said):
            return r.returncode, '\n'.join(l for l in said.splitlines() if not PROGRESS.match(l))
        r is not None and re.search(r'permission denied|not found|unauthorized', said, re.I) and die(
            f'the tool could not be run on the phone as root: {last_line(said)[:160]}')
        print(f'   the link dropped (last: {last_line(said)[:100]}): waiting for the phone', flush=True)
        wait_android(600) or die('the phone did not come back on adb')
        time.sleep(5)
    die(f'hakios-pin {" ".join(str(a) for a in args[:2])}: the link dropped forty times')


def ok(*args, timeout=3600):
    """pin(), and the tool's last word when it did not do it"""
    rc, said = pin(*args, timeout=timeout)
    rc == 0 or die(f'the tool did not do it: {last_line(said)}')


def volume_name():
    name = os.environ.get('NAME', 'data').strip()
    re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name) or die(f'NAME is a file name in {DIR}: letters, digits, dots, '
                                                             f'dashes (got {name!r})')
    return name


def map_path(name):
    """the volume's map as it was made, for the record (the map a boot sends is made afresh each time)"""
    return os.path.join(HOME, name + '.map')


def need_root():
    """the phone in Android, with root for adb's shell: the pin, the raw device and f2fs's own numbers need it"""
    rc, said = su('id -u\n', timeout=20)
    said.strip() == '0' or die(f'no root on the phone through "adb shell su" (it said: {said.strip()[:80]!r}): the '
                               f'data volume needs it -- Magisk and the like give it; a phone without it cannot '
                               f'have one')


def have_tool():
    """hakios-pin on the phone: built here, pushed, executable"""
    if not os.path.isfile(PIN):
        if not IN_REPO:
            die(f'no {PIN}: the release zip carries it as tools/hakios-pin/hakios-pin')
        print('== hakios-pin (the phone-side tool): building it', flush=True)
        r = subprocess.run(['bash', os.path.join(HERE, 'hakios-pin', 'build.sh')], env=env())
        r.returncode == 0 and os.path.isfile(PIN) or die('tools/hakios-pin/build.sh did not make it (it said why)')
    # pushed every time (it is twenty-odd kilobytes: nothing against being sure it is this build and not another
    # of the same size -- which two of its builds have been)
    r = subprocess.run([ADB] + serial_args() + ['push', PIN, REMOTE], capture_output=True, timeout=120, env=env())
    (r.returncode == 0) or die(f'could not copy the tool to the phone: {text_of(r).strip()[:120]}')
    rc, said = su(f'chmod 755 {REMOTE}\n', timeout=20)
    rc == 0 or die(f'could not make the tool executable on the phone: {said.strip()[:120]!r}')


def first_block():
    """where f2fs's data area begins on userdata: below it is the file system's own metadata, where no file is"""
    rc, said = su('d=$(grep " /data " /proc/mounts | cut -d" " -f1)\n'
                  'cat /sys/fs/f2fs/$(basename "$d")/main_blkaddr\n', timeout=20)
    m = re.search(r'^\s*(\d+)\s*$', said, re.M)
    m or die(f'/data of the phone is not an f2fs that root can read (it said: {said.strip()[:100]!r})')
    return int(m.group(1))


def exists(file):
    rc, said = su(f'[ -e {file} ] && echo there\n', timeout=20)
    return 'there' in said


def fresh_map(name, first):
    """the volume's blocks as Android maps them now, the guard of every extent, and the marker in its first
    block: the text the loader takes (its summary line too)"""
    file = f'{DIR}/{name}'
    rc, said = pin('map', file, RAW, first, timeout=300)
    extents = [l for l in said.splitlines() if l.startswith('extent ')]
    summary = [l for l in said.splitlines() if l.startswith('extents ')]
    rc == 0 and extents and summary or die(
        f'{file} is not a volume these tools can serve ({last_line(said) if said else "nothing there"}): '
        f'"create" makes one')
    rc, said = pin('guard', file, RAW, first, timeout=300)
    guards = [l for l in said.splitlines() if l.startswith('guard ')]
    rc == 0 and len(guards) == len(extents) or die(f'the guards of {name}\'s extents: {last_line(said)}')
    rc, said = pin('mark', file, RAW, timeout=60)
    marker = last_line(said)
    re.fullmatch(r'marker 0x[0-9a-f]{32}', marker) or die(
        f'{file}: no marker in its first block ({marker}): not a volume these tools made')
    return '\n'.join(extents + guards + [marker]) + '\n', summary[0]


def size_bytes(text):
    m = re.fullmatch(r'(\d+)([MGT])?', text.strip(), re.I)
    m or die(f'SIZE is a number of MiB, GiB or TiB -- 512M, 8G, 64G (got {text!r})')
    n = int(m.group(1)) * {'M': MIB, 'G': 1024 ** 3, 'T': 1024 ** 4, None: 1024 ** 3}[m.group(2)]
    n >= 16 * MIB or die('a volume is 16 MiB at least')
    return n


def free_bytes():
    rc, said = su('df -k /data | tail -n 1\n', timeout=20)
    parts = said.split()
    len(parts) >= 4 and parts[3].isdigit() or die(f'/data\'s free space could not be read (it said: {said.strip()[:100]!r})')
    return int(parts[3]) * 1024


def container_up():
    r = subprocess.run(['docker', 'inspect', '-f', '{{.State.Running}}', CTR], capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == 'true'


def find_mkfs():
    """a mkfs.hfsplus on this computer: HAKIOS_MKFS's, PATH's, or where Linux's hfsprogs package puts it"""
    p = os.environ.get('HAKIOS_MKFS', '').strip()
    if p:
        os.path.isfile(p) or die(f'HAKIOS_MKFS={p}: no such file')
        return p
    for d in os.environ.get('PATH', '').split(os.pathsep) + ['/sbin', '/usr/sbin', '/usr/local/sbin']:
        p = os.path.join(d, 'mkfs.hfsplus')
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return ''


def mkfs_volume(path, blocks):
    """an empty HFS Plus volume at PATH: case-sensitive, journaled, blocks of 4096, root's and 0755 at
    its root (tools/hakios-datavol.py makes the same volume when it makes one itself)"""
    with open(path, 'wb') as f:
        f.truncate(blocks * BS)
    args = ['-s', '-J', '-b', str(BS), '-U', '0', '-G', '0', '-M', '755', '-v', 'Data', path]
    mkfs = find_mkfs()
    if mkfs:
        subprocess.run([mkfs] + args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return
    if IN_REPO and container_up():
        # (the container sees this repository at its own path, so PATH is the same there)
        tool = '/usr/local/bin/mkfs.hfsplus'
        r = subprocess.run(['docker', 'exec', '-u', '0', CTR, tool] + args, capture_output=True, text=True)
        if r.returncode != 0 and 'no such file' in r.stderr.lower():
            # a container made anew has none yet: tools/hakios-hfsprogs.sh builds it there, once
            print('== mkfs.hfsplus: built in the build container first (tools/hakios-hfsprogs.sh)', flush=True)
            b = subprocess.run(['docker', 'exec', '-u', '0', CTR, 'sh', os.path.join(HERE, 'hakios-hfsprogs.sh')],
                               capture_output=True, text=True)
            r = subprocess.run(['docker', 'exec', '-u', '0', CTR, tool] + args, capture_output=True, text=True) \
                if b.returncode == 0 else b
        if r.returncode == 0:
            return
        die(f'the build container could not make the volume: {r.stderr.strip()[:200]}')
    die('no mkfs.hfsplus here (HAKIOS_MKFS=, or the hfsprogs package, or the build container)')


def pieces():
    """this tree's pieces for the launcher (launcher/hackios.py reads HAKIOS_*); the release zip has them beside it"""
    if not IN_REPO:
        return {}
    args = ''
    try:
        m = re.search(r'^BASE="([^"]+)"', open(os.path.join(HOME, 'run-linux.sh')).read(), re.M)
        args = m.group(1) if m else ''
    except OSError:
        pass
    p = {'HAKIOS_BOOT_IMG': os.path.join(TREE, 'boot.img'),
         'HAKIOS_KERNEL': os.path.join(TREE, 'hakios-kernel'),
         'HAKIOS_RAMDISK': os.path.join(TREE, 'userland', 'root.apfs'),
         'HAKIOS_SYSTEM': os.path.join(TREE, 'userland', 'system.apfs'),
         'HAKIOS_MKPAYLOAD': os.path.join(TREE, 'xnuloader', 'mkpayload.py'),
         'HAKIOS_DEVICE_ARGS': args,
         'HAKIOS_WORK': os.path.join(TREE, 'logs')}
    # the WCN7850's image, both revisions in one (tools/compose_combined.py makes it of the sets in fw-wifi/): the
    # launcher stages it as the release zip's, which sits beside it; the loader takes the chip's own set out of it
    combined = os.path.join(HOME, 'fw-wifi', 'combined.bin')
    if os.path.isfile(combined):
        p['HAKIOS_COMBINED'] = combined
    return p


def launcher(*args, more=None):
    """launcher/hackios.py -- the release zip's launcher -- with this tree's pieces named"""
    sys.stdout.flush()
    return subprocess.call([sys.executable, LAUNCHER, *args], env=env(**pieces(), **(more or {})))


SYSTEM = 'system'                             # the system image's file in Android's /data/hakios (system-create.sh)


def work_dir():
    """where files of the moment go (the launcher's scratch): the tree's logs/ in a built repository, else beside the scripts"""
    return pieces().get('HAKIOS_WORK') or HOME


def system_image():
    """this build's system image: where HAKIOS_SYSTEM says (a built tree), or beside the scripts (a release zip)"""
    return pieces().get('HAKIOS_SYSTEM') or os.path.join(HOME, 'system.apfs')


def file_sha256(path):
    """the SHA-256 of what the phone is to hold for PATH: its bytes, and zeros to the end of its last block (what
    "hakios-pin hashraw" sums there). Kept from one call to the next while the file is what it was"""
    st = os.stat(path)
    pad = (BS - st.st_size % BS) % BS
    key = f'{path} {st.st_size} {st.st_mtime_ns}'
    cache = os.path.join(work_dir(), '.hakios-sha256.json')
    try:
        with open(cache) as f:
            kept = json.load(f)
    except (OSError, ValueError):
        kept = {}
    if kept.get(key):
        return kept[key]
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(16 << 20), b''):
            h.update(block)
    h.update(bytes(pad))
    digest = h.hexdigest()
    kept[key] = digest
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, 'w') as f:
            json.dump(dict(list(kept.items())[-32:]), f)
    except OSError:
        pass
    return digest


def system_record():
    """what is known here of the system image on the phone: system.map's lines. {} when there is none"""
    try:
        with open(map_path(SYSTEM)) as f:
            lines = f.read().splitlines()
    except OSError:
        return {}
    rec = {'extents': [l for l in lines if l.startswith('extent ')],
           'guards': [l for l in lines if l.startswith('guard ')]}
    for l in lines:
        for key in ('bytes', 'sha256'):
            if l.startswith(key + ' '):
                rec[key] = l.split()[1]
    return rec if rec.get('sha256') and rec.get('bytes') and rec['extents'] else {}


def system_fresh_map(first):
    """the system image's blocks as Android maps them now, with the guard of every extent: [extent..., guard...]"""
    file = f'{DIR}/{SYSTEM}'
    rc, said = pin('map', file, RAW, first, timeout=300)
    extents = [l for l in said.splitlines() if l.startswith('extent ')]
    rc == 0 and extents or die(f'{file} is not there as a volume any more ({last_line(said) if said else "nothing"}): '
                               f'system-create.sh puts it back')
    rc, said = pin('guard', file, RAW, first, timeout=300)
    guards = [l for l in said.splitlines() if l.startswith('guard ')]
    rc == 0 and len(guards) == len(extents) or die(f'the guards of the system image\'s extents: {last_line(said)}')
    return extents + guards


def system_for_run():
    """the map the loader is to take for the system image on the phone, when that is this build's image (else
    None, and the image is uploaded as always). Asked of Android before the phone leaves it, like the data volume's"""
    rec = system_record()
    image = system_image()
    if not rec or not os.path.isfile(image):
        return None
    if rec['sha256'] != file_sha256(image):
        print(f'   the system image on the phone is not this build\'s {os.path.basename(image)}: it is uploaded as '
              f'before (system-create.sh puts this one on the phone)')
        return None
    lines = system_fresh_map(first_block())
    lines != rec['extents'] + rec['guards'] and print(
        '   Android has moved the system image\'s blocks since it was put there: the map of now is what is used')
    return '\n'.join(lines + ['readonly', f"bytes {rec['bytes']}"]) + '\n'


def system_create(image=None):
    """the system image on the phone, as a pinned file's blocks: "boot" then has the loader read it from there
    instead of the host uploading 2.4 GB over USB"""
    global MAKING
    image = image or system_image()
    os.path.isfile(image) or die(f'no system image at {image} (./build.sh, or a release zip)')
    to_android('system-create')
    need_root()
    have_tool()
    first = first_block()
    file = f'{DIR}/{SYSTEM}'
    size = os.path.getsize(image)
    blocks = (size + BS - 1) // BS
    digest = file_sha256(image)
    rec = system_record()
    if exists(file):
        if rec.get('sha256') == digest and system_fresh_map(first) == rec['extents'] + rec['guards']:
            rc, said = pin('hashraw', file, RAW, timeout=1800)
            if rc == 0 and f'sha256 {digest}' in said:
                print(f'== {file} holds {image} already ({size} bytes, sha256 {digest[:16]}...): nothing to do')
                return 0
        print(f'== {file} holds another image: it is replaced')
        ok('unpin', file)
        rc, said = su(f'rm -f {file}\n', timeout=30)
        rc == 0 and not exists(file) or die(f'{file} could not be removed: {said.strip()[:120]!r}')
    MAKING = file
    free = free_bytes()
    spare = 4 * 1024 ** 3
    free >= blocks * BS + spare or die(
        f'/data has {free / 2**30:.1f} GiB free; {blocks * BS / 2**30:.2f} GiB are asked for and {spare / 2**30:.0f} '
        f'GiB are to be left to Android')
    print(f'== {file}: {blocks * BS / 2**30:.2f} GiB reserved in Android\'s /data for {os.path.basename(image)} '
          f'({size} bytes), pinned')
    rc, said = su(f'mkdir -p {DIR}; chmod 700 {DIR}\n', timeout=20)
    rc == 0 or die(f'could not make {DIR} on the phone: {said.strip()[:120]!r}')
    ok('create', file)
    ok('pin', file)
    ok('alloc', file, str(blocks * BS))
    print('== zeroing its blocks on the raw storage, every piece read back', flush=True)
    for at in range(0, blocks, ZERO_CHUNK):
        ok('zeroblocks', file, RAW, at, min(ZERO_CHUNK, blocks - at))
    print(f'== {os.path.basename(image)} onto the phone\'s raw storage, a piece at a time', flush=True)
    r = subprocess.run([sys.executable, SEND, '--raw', image, SYSTEM, str(blocks)], env=env(HAKIOS_SKIP_ZERO='1'))
    r.returncode == 0 or die('the image did not go onto the phone whole')
    rc, said = pin('hashraw', file, RAW, timeout=1800)
    rc == 0 and f'sha256 {digest}' in said or die(
        f'what the raw blocks hold is not {os.path.basename(image)}: {last_line(said)[:120]}')
    lines = system_fresh_map(first)
    with open(map_path(SYSTEM), 'w') as f:
        f.write('\n'.join(lines + ['readonly', f'bytes {size}', f'sha256 {digest}']) + '\n')
    MAKING = ''
    print(f'== done: the raw blocks hold {os.path.basename(image)} exactly (sha256 {digest[:16]}...); {len(lines) // 2} '
          f'extents, the map written to {map_path(SYSTEM)}')
    print('   from now on data-boot.sh has the loader read the system image from the phone: no upload')


def system_delete(ask):
    file = f'{DIR}/{SYSTEM}'
    to_android('system-delete')
    need_root()
    have_tool()
    exists(file) or die(f'{file} is not there (python3 tools/hakios-data.py list says what is)')
    rc, said = pin('map', file, RAW, 0, timeout=300)
    print(f'{file}: {last_line(said) if rc == 0 else "(its blocks could not be mapped: " + last_line(said) + ")"}')
    if ask:
        sys.stdout.flush()
        answer = input('delete the system image? Type "system" to go on: ')
        if answer.strip() != SYSTEM:
            print('not deleted')
            return 1
    ok('unpin', file)
    rc, said = su(f'rm -f {file}\n', timeout=30)
    rc == 0 and not exists(file) or die(f'{file} could not be deleted: {said.strip()[:120]!r}')
    if os.path.isfile(map_path(SYSTEM)):
        os.unlink(map_path(SYSTEM))
    print(f'{file}: unpinned and deleted; Android has its blocks again (data-boot.sh uploads the system image again)')
    return 0


def create(size_text):
    name = volume_name()
    bytes_ = size_bytes(size_text)
    blocks = bytes_ // BS
    file = f'{DIR}/{name}'
    to_android('create')
    need_root()
    have_tool()
    first = first_block()
    exists(file) and die(f'{file} is there already: "delete" it first, or NAME=another one')
    free = free_bytes()
    spare = 4 * 1024 ** 3
    free >= bytes_ + spare or die(
        f'/data has {free / 2**30:.1f} GiB free; {bytes_ / 2**30:.0f} GiB are asked for and {spare / 2**30:.0f} GiB '
        f'are to be left to Android: a smaller SIZE')
    print(f'== {file}: {bytes_ / MIB:.0f} MiB reserved in Android\'s /data (/data has {free / 2**30:.1f} GiB free)')
    rc, said = su(f'mkdir -p {DIR}; chmod 700 {DIR}\n', timeout=20)
    rc == 0 or die(f'could not make {DIR} on the phone: {said.strip()[:120]!r}')
    global MAKING
    MAKING = file
    ok('create', file)
    ok('pin', file)
    ok('alloc', file, str(bytes_))
    print('== zeroing its blocks on the raw storage, every piece read back', flush=True)
    for at in range(0, blocks, ZERO_CHUNK):
        ok('zeroblocks', file, RAW, at, min(ZERO_CHUNK, blocks - at))
    marker = os.urandom(16).hex()
    with tempfile.TemporaryDirectory(prefix='hakios-data.', dir=TREE) as tmp:
        image = os.path.join(tmp, 'volume.img')
        print(f'== an empty HFS Plus volume of {bytes_ / MIB:.0f} MiB (mkfs.hfsplus), its marker {marker}')
        mkfs_volume(image, blocks)
        # the marker, in the image's first block where the loader looks for it (the block is zeros there:
        # past the volume header, before the allocation file)
        with open(image, 'r+b') as f:
            f.seek(2048)
            f.write(bytes.fromhex(marker))
        # as a file, in chunks the phone takes whole (the fastboot-sized transfer the system image uses):
        # only the chunks that hold something are pushed -- the rest of the volume is zero on the device
        print(f'== onto the phone\'s raw storage, a chunk at a time: the {bytes_ / MIB:.0f} MiB image\'s '
              f'blocks that hold something', flush=True)
        r = subprocess.run([sys.executable, SEND, '--raw', image, name, str(blocks)],
                           env=env(HAKIOS_SKIP_ZERO='1'))
        r.returncode == 0 or die('the volume did not go onto the phone whole')
    rc, said = pin('mark', file, RAW, timeout=60)
    last_line(said) == f'marker 0x{marker}' or die(f'the marker is not in the volume\'s first block as it was sent: '
                                                    f'{last_line(said)}')
    text, summary = fresh_map(name, first)
    with open(map_path(name), 'w') as f:
        f.write(text)
    MAKING = ''
    print(f'== done: {summary}; {len([l for l in text.splitlines() if l.startswith("extent ")])} extents, the map '
          f'written to {map_path(name)}')
    print('   boot iOS with it: ./data-boot.sh   (what it holds: python3 tools/hakios-data.py look)')


def remap():
    """the volume's map and guards afresh, from Android, written to its map file: after a send that
    was made outside create (resumed, or a volume whose image was made on the host)"""
    name = volume_name()
    need_root()
    have_tool()
    first = first_block()
    text, summary = fresh_map(name, first)
    with open(map_path(name), 'w') as f:
        f.write(text)
    print(f'== {name}: {summary}; {len([l for l in text.splitlines() if l.startswith("extent ")])} extents, the map '
          f'written to {map_path(name)}')


def boot(secs):
    name = volume_name()
    need = pieces()
    missing = [p for k, p in need.items() if k in ('HAKIOS_BOOT_IMG', 'HAKIOS_KERNEL', 'HAKIOS_RAMDISK', 'HAKIOS_SYSTEM')
               and not os.path.isfile(p)]
    if not IN_REPO:                    # the release zip: everything beside the scripts; the kernel under either name
        missing = [os.path.join(HOME, f) for f in ('boot.img', 'root.apfs', 'system.apfs')
                   if not os.path.isfile(os.path.join(HOME, f))]
        missing += [] if [f for f in ('kernel.bin', 'hakios-kernel') if os.path.isfile(os.path.join(HOME, f))] else \
            ['kernel.bin (or hakios-kernel)']
    missing and die('the build\'s pieces are missing: ' + ', '.join(missing) + ' (./build.sh, or a release zip)')
    to_android('boot')              # the map is read from Android, and a kernel runs once per loader
    need_root()
    have_tool()
    first = first_block()
    print(f'== the map of {name}\'s blocks, as Android holds them now', flush=True)
    text, summary = fresh_map(name, first)
    if ' not pinned' in summary:
        print(f'   {name} is NOT pinned any more: Android may move its blocks at any time. iOS is started only '
              f'where the volume really is, and what it finds may be an older state of it')
    made = map_path(name)
    if os.path.isfile(made):
        was = [l for l in open(made).read().splitlines() if l.startswith('extent ')]
        now = [l for l in text.splitlines() if l.startswith('extent ')]
        was != now and print(f'   the blocks are not all where they were when the volume was made ({len(was)} extents '
                             f'then, {len(now)} now): Android moved them; the map of now is what is used')
    work = need.get('HAKIOS_WORK', HOME)
    os.makedirs(work, exist_ok=True)
    fresh = os.path.join(work, f'{name}.map')
    with open(fresh, 'w') as f:
        f.write(text)
    print(f'== iOS with /private/var on {name}: {summary}')
    more = {'DATA': fresh}
    if os.environ.get('HAKIOS_NOSYS'):
        print('== the system image: uploaded (HAKIOS_NOSYS=1)')
    else:
        sysmap = system_for_run()
        if sysmap:
            # the image is on the phone and is this build's: the loader reads it from there (launcher/hackios.py, ramdisk)
            path = os.path.join(work, f'{SYSTEM}.map')
            with open(path, 'w') as f:
                f.write(sysmap)
            more['SYS'] = path
            print(f'== the system image: on the phone as {SYSTEM}, this build\'s: the loader reads it from there '
                  f'({len(sysmap.splitlines())} lines of map)')
        else:
            print(f'== the system image: uploaded from {system_image()}')
    launcher('ramdisk', more=more) == 0 or die('the system image did not get into the phone')
    return launcher('run', more=dict(more, **({'SECS': str(secs)} if secs else {})))


def delete(ask):
    name = volume_name()
    file = f'{DIR}/{name}'
    to_android('delete')
    need_root()
    have_tool()
    exists(file) or die(f'{file} is not there (python3 tools/hakios-data.py list says what is)')
    rc, said = pin('map', file, RAW, 0, timeout=300)
    print(f'{file}: {last_line(said) if rc == 0 else "(its blocks could not be mapped: " + last_line(said) + ")"}')
    if ask:
        sys.stdout.flush()
        answer = input(f'delete it? Everything iOS kept on this volume goes. Type its name ("{name}") to go on: ')
        if answer.strip() != name:
            print('not deleted')
            return 1
    ok('unpin', file)
    rc, said = su(f'rm -f {file}\n', timeout=30)
    rc == 0 and not exists(file) or die(f'{file} could not be deleted: {said.strip()[:120]!r}')
    if os.path.isfile(map_path(name)):
        os.unlink(map_path(name))
    print(f'{file}: unpinned and deleted; Android has its blocks again')
    return 0


def list_volumes():
    to_android('list')
    need_root()
    have_tool()
    rc, said = su(f'ls -1 {DIR} 2>/dev/null\n', timeout=20)
    names = [n for n in said.split() if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', n)]
    if not names:
        print(f'no volumes in {DIR} (./data-create.sh makes one; ./system-create.sh the system image\'s)')
        return 0
    first = first_block()
    for n in names:
        rc, said = pin('map', f'{DIR}/{n}', RAW, first, timeout=300)
        if n == SYSTEM:
            rec = system_record()
            image = system_image()
            same = (rec.get('sha256') and os.path.isfile(image) and rec['sha256'] == file_sha256(image))
            print(f'{n}: the system image, {last_line(said) if rc == 0 else "not a volume (" + last_line(said) + ")"}; '
                  f'{len(rec.get("extents", []))} extents and {rec.get("bytes", "?")} bytes when put; '
                  f'{"this build\'s image: boot reads it from the phone" if same else "not this build\'s image (or none known here): boot uploads"}')
            continue
        rc2, said2 = pin('mark', f'{DIR}/{n}', RAW, timeout=60)
        made = 'made here' if os.path.isfile(map_path(n)) else 'no map beside this script'
        print(f'{n}: {last_line(said) if rc == 0 else "not a volume (" + last_line(said) + ")"}; '
              f'{last_line(said2) if rc2 == 0 else "?"}; {made}')
    return 0


def look(args):
    name = volume_name()
    made = map_path(name)
    sys.stdout.flush()
    return subprocess.call([sys.executable, VOLREAD, name, *args], env=env(**({'MAP': made} if os.path.isfile(made) else {})))


def main():
    argv = sys.argv[1:]
    cmd = argv[0] if argv else ''
    rest = argv[1:]
    if cmd in ('create', 'boot', 'delete', 'list', 'look', 'system-create', 'system-delete') and \
            not (cmd == 'create' and rest == ['--help']):
        need_tools()
        adb_link()
    if cmd == 'create':
        rest and len(rest) <= 1 and not rest[0].startswith('--') or die('create takes SIZE alone (64G, 1G, 512M ...)')
        return create(rest[0] if rest else '64G') or 0
    if cmd == 'boot' and len(rest) <= 1:
        return boot(int(rest[0]) if rest else 0) if not rest or rest[0].isdigit() else die('SECS is a number of seconds')
    if cmd == 'delete' and rest in ([], ['-y'], ['--yes']):
        return delete(ask=not rest)
    if cmd == 'system-create' and len(rest) <= 1:
        return system_create(rest[0] if rest else None) or 0
    if cmd == 'system-delete' and rest in ([], ['-y'], ['--yes']):
        return system_delete(ask=not rest)
    if cmd == 'list' and not rest:
        return list_volumes()
    if cmd == 'look':
        return look(rest)
    if cmd == 'remap' and not rest:
        return remap()
    sys.exit(__doc__)


main()
