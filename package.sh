#!/bin/bash
# package.sh: assemble the release zip (the same zip the CI makes).
#
# Runs in place wherever this repository finds itself:
#   at <tree>/devices/dodge/   (the CI's copy, or the symlink the local flow makes) -- the tree is above
#   at this repository's root  (a local clone; apply-kernel-patches.sh put the tree at kernel/)
#   beside whatever was built  (a lone checkout: the pieces must lie beside this script)
set -e
cd "$(dirname "$(readlink -f "$0")")"

# the tree whose pieces we package: this script may sit inside one (devices/<dev>/),
# hold one at kernel/ beside it, or lie beside loose pieces -- one marker, walk up
TOP=
d=$PWD
while [ "$d" != "/" ]; do
	for t in "$d" "$d/kernel"; do
		[ -f "$t/xnuloader/mkpayload.py" ] && TOP=$t && break 2
	done
	d=$(dirname "$d")
done
TOP=${TOP:-$PWD}
VER=$(git describe --tags --always 2>/dev/null || echo dev)
DIR=dist/hakios-dodge-$VER
rm -rf dist && mkdir -p "$DIR"

# what the tree built
for f in boot.img hakios-kernel kernel.bin; do
	[ -f "$TOP/$f" ] && cp "$TOP/$f" "$DIR/"
done
[ -f "$TOP/xnuloader/loader.efi" ] && cp "$TOP/xnuloader/loader.efi" "$DIR/"
[ -f "$TOP/userland/root.apfs" ] && cp "$TOP/userland/root.apfs" "$DIR/"     # the boot RAM disk
[ -f "$TOP/userland/system.apfs" ] && cp "$TOP/userland/system.apfs" "$DIR/" # the system image
[ -f "$TOP/xnuloader/mkpayload.py" ] && cp "$TOP/xnuloader/mkpayload.py" "$DIR/"

# the kernel.ver mkpayload would ask a full tree for
if [ -f "$TOP/tools/kernel-version.py" ] && [ -f "$TOP/.config" ]; then
	HAKIOS_VERSION_SUFFIX="-gdodge-$(git rev-parse --short HEAD 2>/dev/null || echo dev)" \
		python3 "$TOP/tools/kernel-version.py" --config "$TOP/.config" > "$DIR/kernel.ver" 2>/dev/null || true
fi

# the launcher
cp launcher/hackios.py launcher/run.sh launcher/run.bat "$DIR/"
chmod +x "$DIR/run.sh"

# the device's kernel arguments: the BASE= line of run-linux.sh, the set the phone runs
python3 - "$DIR/device-args" <<'PYEOF'
import re, sys
base = ''
for line in open('run-linux.sh'):
    m = re.match(r'BASE="([^"]+)"', line)
    if m:
        base = m.group(1)
        break
open(sys.argv[1], 'w').write(base + '\n')
PYEOF

cat > "$DIR/README.txt" <<'EOF'
HakiOS for the OnePlus 13 (dodge) -- iOS 16.5 booted over fastboot, nothing flashed.

Needs on the computer: python3 and platform-tools (fastboot + adb) in PATH or in a
platform-tools/ folder next to these files:
  https://developer.android.com/studio/releases/platform-tools
The phone: unlocked bootloader, in Android with USB debugging or in fastboot.

  1. Everything needed is in this zip: system.apfs (the system, from Apple's IPSW),
     root.apfs (the small boot disk), boot.img, the kernel and the loader.  (once)
  2. Linux: ./run.sh        Windows: run.bat                     (every boot)
     Or by hand:
       python3 hackios.py boot
       python3 hackios.py ramdisk system.apfs   (once per power-on, ~1 min)
       python3 hackios.py run                  (SECS=900 CMDLINE="-v")

iOS boots in about two minutes; the touch screen, the speakers, Wi-Fi, Bluetooth and
the flashlight work. The session ends after SECS seconds (the screen then freezes --
that is the session ending, not a hang; python3 hackios.py stop ends it early,
fastboot reboot leaves). The kernel and iOS are never installed to the phone.
EOF

# the zip: python's own zipfile (deflated) -- no dependence on the Info-ZIP binary,
# which was once seen failing ENOENT on names that listed fine, for minutes
( cd dist && python3 -c "
import os, zipfile
with zipfile.ZipFile('hakios-dodge-$VER.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk('hakios-dodge-$VER'):
        for f in sorted(files):
            p = os.path.join(root, f)
            z.write(p, p)
" )
echo "package.sh: $(pwd)/dist/hakios-dodge-$VER.zip"
python3 -m zipfile -l dist/hakios-dodge-$VER.zip | tail -6
