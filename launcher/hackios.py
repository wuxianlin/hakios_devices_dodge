#!/usr/bin/env python3
"""hackios.py — boot HakiOS on the OnePlus 13 from Linux or Windows.

The xl flow of the HakiOS repository (its README documents every part), in one
cross-platform script for the release zip: boot / ramdisk / run / screenshot /
log / stop / reboot / state.

  python hackios.py boot              fastboot-boot the UEFI+loader (boot.img)
  python hackios.py ramdisk SYS.apfs  upload the system image ONCE per power-on
  python hackios.py map FILE.map [sys]  give the loader the map of the data volume's blocks (of the
                                        system image's, with "sys")
  python hackios.py run               boot iOS, wait for the session's end
                                      (DATA=name: /private/var on the data volume)
  python hackios.py screenshot [F]    what is on the phone's screen, as PNG
  python hackios.py log               the log of the run in progress
  python hackios.py stop              end the run now
  python hackios.py reboot            back to Android (or into fastboot: --bl)
  python hackios.py state             where the phone is

Environment for `run`: SECS=900 (the session's length), CMDLINE="-v" (extra
kernel arguments; device-args beside this script is merged in automatically),
RAMDISK=root.apfs ("resident" uses only the big image already uploaded), STAY=1 (leave the phone in the
loader after the session instead of rebooting to Android — xl's own default).

Needs python3 (nothing else) and fastboot/adb from Android's platform-tools in
PATH or in a platform-tools/ folder beside this script.

Where the pieces are: beside this script (the release zip), or where HAKIOS_* says (a built tree:
tools/hakios-data.py sets them) -- HAKIOS_BOOT_IMG, HAKIOS_KERNEL, HAKIOS_RAMDISK, HAKIOS_SYSTEM,
HAKIOS_MKPAYLOAD, HAKIOS_DEVICE_ARGS (the kernel arguments of device-args), HAKIOS_COMBINED (the WCN7850's
firmware image: combined.bin otherwise, beside this script), HAKIOS_WORK (where the run's own files go:
the payload, the log).

The data volume (docs/data-persistence.md): DATA=name for "run" is the volume whose map, name.map, is
given to the loader first; the loader publishes the volume for writing and iOS keeps /private/var on it,
so what is there is there at the next boot. data-create.sh makes the volume, data-boot.sh runs with it,
data-delete.sh removes it.

The system image on the phone: SYS=<its map> for "ramdisk" and "run" has the loader read the image
from the phone's own storage (tools/hakios-data.py system-create put it there) instead of the 2.4 GB
upload: "oem hakios-sysmap" takes its map, "oem hakios-resident" reads it into the RAM disk's place.
Without SYS the image is uploaded, as always.
"""
import os, struct, subprocess, sys, time, zlib

HERE = os.path.dirname(os.path.abspath(__file__))

def piece(name, setting=None):
    """a file the flow needs: where HAKIOS_<SETTING> says, or beside this script"""
    path = os.environ.get('HAKIOS_' + setting, '') if setting else ''
    if path:
        return path if os.path.isabs(path) else os.path.join(HERE, path)
    return os.path.join(HERE, name)

def work(name):
    """a file the flow writes for itself (the payload, the log): HAKIOS_WORK's, or beside this script"""
    d = os.environ.get('HAKIOS_WORK', '').strip()
    if d:
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, name)
    return os.path.join(HERE, name)

def find_tool(name):
    for d in (os.path.join(HERE, 'platform-tools'), ''):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return os.path.abspath(p)
    return name  # from PATH

FASTBOOT, ADB = find_tool('fastboot'), find_tool('adb')

# ANDROID_SERIAL names one phone among several for adb. fastboot is given it only while a device of that
# serial is in fastboot now -- the phone's bootloader: in this port's loader the phone answers to "xnuloader"
# instead, and a fastboot told the other serial waits for what is not there for as long as the loader runs.
# (adb keeps the environment: one phone among several is adb's to pick.)
FB_ENV = {k: v for k, v in os.environ.items() if k != 'ANDROID_SERIAL'}

def phone_in_fastboot():
    """is the phone ANDROID_SERIAL names in fastboot now? True when nothing is named: the environment as it is"""
    serial = os.environ.get('ANDROID_SERIAL', '').strip()
    if not serial:
        return True
    try:
        r = subprocess.run([FASTBOOT, 'devices'], capture_output=True, text=True, timeout=10, env=FB_ENV)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return any(l.split()[:1] == [serial] for l in r.stdout.splitlines())

def fb(*args, timeout=20):
    try:
        r = subprocess.run([FASTBOOT, *args], capture_output=True, text=True, timeout=timeout,
                           env=None if phone_in_fastboot() else FB_ENV)
        return r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return ''

def adb(*args, timeout=10):
    try:
        r = subprocess.run([ADB, *args], capture_output=True, text=True, timeout=timeout)
        return r.stdout + r.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ''

def in_fastboot():
    return 'fastboot' in fb('devices', timeout=5)

def is_loader():
    return in_fastboot() and 'xnuloader' in fb('getvar', 'product', timeout=5)

def state():
    if is_loader(): return 'loader'
    if in_fastboot(): return 'fastboot'
    if adb('get-state', timeout=3).strip().endswith('device'): return 'android'
    return 'absent'

def wait_for(want, secs):
    for _ in range(0, secs, 2):
        if state() == want: return True
        time.sleep(2)
    return False

def die(msg):
    sys.exit(f'hackios: {msg}')

def enter():
    st = state()
    if st == 'android':
        adb('reboot', 'bootloader')
        wait_for('fastboot', 60) or die('phone did not reach fastboot')
    elif st == 'absent':
        print('waiting for the phone (Android or fastboot)...')
        if not wait_for('fastboot', 180) and state() != 'loader':
            die('phone not reachable')
        return enter()
    if is_loader(): return
    img = piece('boot.img', 'BOOT_IMG')
    os.path.isfile(img) or die(f'no boot image at {img}')
    for _ in range(2):     # the first boot right after entering fastboot is sometimes not taken
        if 'OKAY' in fb('boot', img, timeout=60):
            time.sleep(6)
            if wait_for('loader', 60): return      # it has taken a minute and more to answer
    die('loader did not come up')

def send_map(path, command='hakios-map', shape='hakios-map'):
    """the loader takes the map of a volume's blocks -- "oem hakios-map": the data volume's, "oem hakios-sysmap":
    the system image's -- then a download. What it says of the map, in a few words"""
    path = path if os.path.isabs(path) else os.path.join(HERE, path)
    os.path.isfile(path) or die(f'no map at {path}: data-create.sh (system-create.sh) makes it')
    'OKAY' in fb('oem', command, timeout=30) or die(f'the loader has no "oem {command}": it is not this tree\'s loader')
    out = fb('stage', path, timeout=60)
    'OKAY' in out or die(f'the map was refused: {out.strip()[:200]}')
    said = fb('getvar', shape, timeout=15).splitlines()
    return said[0].split(':', 1)[-1].strip() if said else ''

def volume_verdict(variable='hakios-volume'):
    """is the volume where its map says it is? The loader looks again and answers in a few words
    ("hakios-volume": the data volume; "hakios-system": the system image)"""
    fb('oem', 'hakios-info', timeout=30)
    said = fb('getvar', variable, timeout=15).splitlines()
    return said[0].split(':', 1)[-1].strip() if said else ''      # fastboot says "<the variable>: <the verdict>"

def ramdisk(path=None):
    sysmap = os.environ.get('SYS', '').strip()
    path = path or piece('system.apfs', 'SYSTEM')
    enter()
    if sysmap:
        # The system image is on the phone's own storage (tools/hakios-data.py system-create: a pinned file's
        # blocks, with a map of them taken from Android before the phone left it): the loader reads it from there
        # into the RAM disk's place -- seconds, where uploading 2.4 GB over USB took a minute.
        print('the map of the system image on the phone:', send_map(sysmap, 'hakios-sysmap', 'hakios-sysmap'))
        verdict = volume_verdict('hakios-system')
        print(f'the system image on the phone: {verdict}')
        verdict.startswith('whole') or die('the system image on the phone is not where its map says it is: '
                                          'tools/hakios-data.py boot says more')
        t0 = time.time()
        out = fb('oem', 'hakios-resident', timeout=300)
        if 'OKAY' not in out:
            die(f'the loader could not read the system image from the phone: {out.strip()[:200]}')
        print(f'the system image read from the phone\'s storage in {max(1, int(time.time() - t0))} s')
        return
    os.path.isfile(path) or die(f'{path} not found')
    print(f'uploading {os.path.getsize(path) // 1048576} MB (a minute or two)...')
    t0 = time.time()
    'OKAY' in fb('oem', 'ramdisk') or die('the loader refused (no memory window?)')
    'OKAY' in fb('stage', path, timeout=3600) or die('upload failed')
    print(f'uploaded in {max(1, int(time.time() - t0))} s -- stays in RAM across runs while powered')

def device_args():
    """the device's own kernel arguments (devices/oneplus-dodge/args in the
    repository; shipped beside this script with the comments stripped)"""
    if os.environ.get('HAKIOS_DEVICE_ARGS', '').strip():
        return os.environ['HAKIOS_DEVICE_ARGS'].strip()
    p = os.path.join(HERE, 'device-args')
    if not os.path.isfile(p):
        return ''
    return ' '.join(l.split('#', 1)[0].strip() for l in open(p)).strip()

def loader_log_now(name):
    """what the loader has logged by its last command, kept as <name>.log where this one's own logs go -- and
    said in one line. The loader empties its log at every command of the Wi-Fi bring-up, so a refusal has to be
    read at once, or never"""
    try:
        p = work(name + '.log')
        fb('oem', 'log', timeout=30)
        fb('get_staged', p, timeout=60)
        text = open(p, 'rb').read().replace(b'\0', b'').decode('utf-8', 'replace') if os.path.isfile(p) else ''
    except (OSError, ValueError):
        text = ''
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return ' (the loader said nothing)'
    said = [l for l in lines if 'failed' in l or 'refused' in l or 'fault' in l or 'not ' in l]
    return ' -- the loader said: ' + ' | '.join((said or lines)[-3:])[:400] + f' (all of it: {work(name + ".log")})'

def wifi():
    """the WCN7850's bring-up in the loader: the staged sequence the port's own rounds run
    (map, rails, clocks, link+survey, the firmware download over MHI, the QMI rounds,
    transfer-prepare). The kernel adopts the lease this leaves; without it there is no
    Wi-Fi.

    An OnePlus 13 carries one of two revisions of the WCN7850, peach or peach_v2, and each takes its own
    firmware. combined.bin holds both sets, and which is downloaded is not decided here: at the link step the
    loader reads the chip's revision off the chip itself (its second aperture: 2 MiB on the first, 32 KiB on
    the second) and takes that revision's set out of the image. It sits beside this script (HAKIOS_COMBINED
    names it elsewhere: a built tree keeps it in fw-wifi/); without it the bring-up is skipped; a step the
    loader refuses ends the run with what the loader said (wifi-*.log)."""
    fw = piece('combined.bin', 'COMBINED')
    if not os.path.isfile(fw):
        print(f'no combined.bin at {fw}: the Wi-Fi bring-up is skipped')
        return
    print('the Wi-Fi bring-up (map, rails, clocks, link; the chip\'s revision is read at the link)...')
    for step in ('map', 'rails', 'clocks'):
        'OKAY' in fb('oem', f'op13-wifi-{step}', timeout=60) or die(f'op13-wifi-{step} failed' + loader_log_now('wifi-refused'))
    ok = False
    for _ in range(3):
        ok = 'OKAY' in fb('oem', 'op13-wifi-link', timeout=120)
        if ok:
            break
    ok or die('op13-wifi-link failed (the survey never came up)' + loader_log_now('wifi-refused'))
    print('staging the firmware of both revisions, and downloading the chip\'s over MHI (a minute or two)...')
    'OKAY' in fb('stage', fw, timeout=600) or die('staging combined.bin failed' + loader_log_now('wifi-refused'))
    for n in (9, 8, 6):
        'OKAY' in fb('oem', 'op13-wifi-smmu', str(n), timeout=90) or die(f'op13-wifi-smmu {n} failed' + loader_log_now('wifi-refused'))
    if 'OKAY' not in fb('oem', 'op13-wifi-smmu', '7', timeout=900):     # the firmware download itself
        die('the radio would not take the firmware' + loader_log_now('wifi-download'))
    for n in (2, 1, 3):
        'OKAY' in fb('oem', 'op13-wifi-smmu', str(n), timeout=120) or die(f'op13-wifi QMI {n} failed' + loader_log_now('wifi-refused'))
    if 'OKAY' not in fb('oem', 'op13-wifi-smmu', '0', timeout=300):     # the file rounds
        die('the radio would not take its files' + loader_log_now('wifi-files'))
    if 'OKAY' not in fb('oem', 'op13-wifi-transfer-prepare', timeout=120):
        die('the loader would not hand the radio over' + loader_log_now('wifi-handover'))
    print('the WCN7850 is up; the lease is ready for the kernel to adopt')

def run():
    enter()
    data = os.environ.get('DATA', '').strip()
    if data:
        # /private/var on the data volume (docs/data-persistence.md): its map with the loader first, and
        # the loader's own word that the volume is where the map says -- or there is no run with it
        print('the map of the data volume:', send_map(data if data.endswith('.map') else data + '.map'))
        verdict = volume_verdict()
        print(f'the data volume: {verdict}')
        verdict.startswith('whole') or die('the data volume is not where its map says it is, so nothing would be '
                                          'written to it and iOS would start with an empty /private/var (tools/hakios-data.py boot says more)')
    sysimg = os.environ.get('SYS', '').strip()
    if sysimg:
        # the system image on the phone: its map with the loader again (ramdisk sent it), and the loader's word
        print('the map of the system image on the phone:', send_map(sysimg, 'hakios-sysmap', 'hakios-sysmap'))
        verdict = volume_verdict('hakios-system')
        print(f'the system image on the phone: {verdict}')
        verdict.startswith('whole') or die('the system image on the phone is not where its map says it is: '
                                          'tools/hakios-data.py boot says more')
    if os.environ.get('NOWIFI'):
        print('NOWIFI: the Wi-Fi bring-up is skipped at your word')
    else:
        wifi()
    kern = piece('kernel.bin', 'KERNEL')
    if not os.path.isfile(kern) and os.path.isfile(os.path.join(HERE, 'hakios-kernel')):
        kern = os.path.join(HERE, 'hakios-kernel')        # the tree's own name for it
    os.path.isfile(kern) or die(f'no kernel at {kern}')
    secs = os.environ.get('SECS', '900')
    args = os.environ.get('CMDLINE', '').strip()
    extra = device_args()
    if extra:
        args = (extra + ' ' + args).strip()
    if data:
        # the loader publishes the volume for writing, and init mounts it on /private/var (userland/src/init.c)
        args = (args + ' ufs=2 hakios_data=1 var_hfs=ufs_hakios_data').strip()
    # the run's RAM disk is the SMALL root image (~42 MB, /dev/md1, /redwood);
    # the big system image is already resident from `ramdisk` (/dev/md0)
    rd = os.environ.get('RAMDISK', 'root.apfs')
    payload = [sys.executable, piece('mkpayload.py', 'MKPAYLOAD'), '-o',
               work('run-payload.bin'), f'kernel={kern}']
    if rd and rd.lower() not in ('none', 'resident'):
        rp = piece(rd, 'RAMDISK')
        os.path.isfile(rp) or die(f'ramdisk {rp} not found (RAMDISK=resident to use only the resident system image)')
        payload.append(f'ramdisk={rp}')
    payload += ['--cmdline', f'loader_return_after={secs} epoch={int(time.time())} sb_delay=8 {args}']
    ver_p = piece('kernel.ver')
    if os.path.isfile(ver_p):
        ver = open(ver_p).read().strip()
        if ver:
            payload += ['--kernel-version', ver]
    try:
        subprocess.run(payload, check=True, stdout=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        die('mkpayload failed -- is kernel.bin valid?')
    out = fb('stage', work('run-payload.bin'), timeout=300)
    if 'OKAY' not in out:
        die(f'stage failed: {out.strip()[:500]}')
    fb('oem', 'run')
    print(f'iOS starting ({secs} s session). log: python {os.path.basename(__file__)} log')
    deadline = time.time() + int(secs) + 120
    while time.time() < deadline:
        for line in fb('getvar', 'result', timeout=15).splitlines():
            if line.startswith('result:') and 'running' not in line:
                print(line.strip())
                log()
                if data:
                    data_volume_lines()
                if not os.environ.get('STAY'):
                    reboot()      # xl leaves the phone in the loader; back to Android by default here
                return
        time.sleep(2)
    print('no answer -- the kernel hung; the watchdog resets the phone')

def data_volume_lines():
    """what the log of a run with DATA says of the data volume: whether the kernel made a writable block
    device of it, what iOS mounted on /private/var, and what was written through the loader"""
    try:
        text = open(work('last-run.log'), 'rb').read().replace(b'\0', b'').decode('utf-8', 'replace')
    except OSError:
        return
    for line in text.splitlines():
        s = line.strip()
        if (('/private/var:' in s and 'HFS Plus' in s) or s.startswith('ufs: /dev/ufs_')
                or s.startswith('ufs: hakios_data=1') or s.startswith('ufs: at the end:')):
            print('   ' + s[:260])

def log():
    """the whole log in one transfer when that works, else in 16 KB parts
    (some UEFI/host pairs stop a big upload at 32767 bytes; a small one
    always goes through -- oem log-part N on the loader stages slice N)"""
    p = work('last-run.log')
    whole = b''
    fb('oem', 'log')
    fb('get_staged', p, timeout=60)
    if os.path.isfile(p):
        whole = open(p, 'rb').read().replace(b'\0', b'')
    parts, i = [], 0
    while True:
        out = fb('oem', f'log-part {i}', timeout=20)
        if 'OKAY' not in out:        # "past the end", or the loader is gone
            break
        pp = p + f'.part{i}'
        fb('get_staged', pp, timeout=60)
        if not os.path.isfile(pp):
            break
        raw = open(pp, 'rb').read()  # judged by its RAW length: the NULs (block
        os.unlink(pp)                # padding) are stripped after joining, or a full
        if not raw:                  # part with one in it would end the loop early
            break
        parts.append(raw)
        if len(raw) < 16384:         # the last part is short
            break
        i += 1
        if i > 512:                  # 8 MB of log is far past anything real
            break
    joined = b''.join(parts).replace(b'\0', b'')
    data = joined if len(joined) > len(whole) else whole
    if len(data) < 1024:
        # a USB re-enumeration mid-fetch delivers a stub: one clean retry
        time.sleep(2)
        fb('oem', 'log')
        fb('get_staged', p, timeout=60)
        if os.path.isfile(p):
            again = open(p, 'rb').read().replace(b'\0', b'')
            data = again if len(again) > len(data) else data
    if data:
        open(p, 'wb').write(data)
        print(f'log: {p} ({len(data)} bytes)')

def screenshot(out=None):
    enter()
    out = out or work('screen.png')
    geom = ''
    for line in fb('getvar', 'screen', timeout=15).splitlines():
        if line.startswith('screen:'):
            geom = line[7:].strip()
    if not geom or 'OKAY' not in fb('oem', 'screenshot', timeout=30):
        die('the loader has no display')
    raw = work('screen.raw')
    fb('get_staged', raw, timeout=60)
    os.path.isfile(raw) or die('no screenshot came back')
    w, h, stride = map(int, geom.split()[:3])
    data = open(raw, 'rb').read()
    rows = bytearray()
    for y in range(h):
        line = data[y * stride:y * stride + w * 4]
        rows.append(0)                                   # PNG filter: none
        rgb = bytearray(w * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = line[2::4], line[1::4], line[0::4]   # the panel is BGRX
        rows += rgb
    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))
    with open(out, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' +
                chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) +
                chunk(b'IDAT', zlib.compress(bytes(rows), 6)) + chunk(b'IEND', b''))
    os.unlink(raw)
    print(f'{out} ({w}x{h})')

def reboot(to_bootloader=False):
    st = state()
    if st == 'android' and not to_bootloader:
        adb('reboot')                      # already Android, nothing to do
        print('rebooting Android')
        return
    if st == 'absent':
        die('phone not reachable')
    if st != 'loader':
        enter()                            # from fastboot, boot the loader first
    cmd = 'reboot-bootloader' if to_bootloader else 'reboot'
    out = fb(cmd, timeout=10)              # the connection drops: no OKAY to wait for
    time.sleep(4)
    # some UEFI ports do not carry the reset through (this phone's among them): the
    # command is answered and nothing happens. Say so instead of promising Android.
    if state() == 'loader':
        print('this port\'s UEFI does not reset the phone: hold power ~10 s to leave'
              + (' (to fastboot: keep holding, or "fastboot reboot" once in Android)' if not to_bootloader else ''))
        return
    print('back to ' + ('fastboot' if to_bootloader else 'Android') + ' (takes half a minute; '
          'if nothing happens: hold power ~10 s)')

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd in ('boot', 'enter'):
        enter()
    elif cmd == 'ramdisk':
        ramdisk(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == 'map':
        if len(sys.argv) < 3:
            die('usage: python hackios.py map FILE.map [sys]')
        enter()
        if len(sys.argv) > 3 and sys.argv[3] == 'sys':
            print('the map of the system image on the phone:', send_map(sys.argv[2], 'hakios-sysmap', 'hakios-sysmap'))
            print('the system image on the phone:', volume_verdict('hakios-system'))
        else:
            print('the map of the data volume:', send_map(sys.argv[2]))
            print('the data volume:', volume_verdict())
    elif cmd == 'run':
        run()
    elif cmd == 'screenshot':
        screenshot(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == 'log':
        log()
    elif cmd == 'stop':
        'OKAY' in fb('oem', 'stop', timeout=30) or print('stop sent')
    elif cmd == 'reboot':
        reboot('--bl' in sys.argv)
    elif cmd == 'state':
        print(state())
    else:
        print(__doc__)
