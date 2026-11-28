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
# the kernel under the launcher's own name for it, as the CI's zip has it (the workflow makes kernel.bin; a local
# build leaves the kernel as hakios-kernel -- which the launcher takes as well, but the zip should be the same zip)
[ -f "$DIR/kernel.bin" ] || [ ! -f "$DIR/hakios-kernel" ] || cp "$DIR/hakios-kernel" "$DIR/kernel.bin"
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

# the data volume (docs/data-persistence.md): iOS keeping /private/var in Android's /data from boot to
# boot -- and, the same way, the system image on the phone, so that a boot does not upload 2.4 GB. The five
# scripts at the root, and what they run out of tools/ -- the Python front-end and its helpers, and the
# phone-side tool built by tools/hakios-pin/build.sh (a static ARM64 program).
cp data-create.sh data-boot.sh data-delete.sh system-create.sh system-delete.sh "$DIR/"
chmod +x "$DIR"/data-*.sh "$DIR"/system-*.sh
mkdir -p "$DIR/tools/hakios-pin"
cp tools/hakios-data.py tools/hakios-send.py tools/hakios-volread.py tools/hakios_adblink.py tools/hakios-datavol.py \
	"$DIR/tools/"
if [ -f tools/hakios-pin/hakios-pin ]; then
	cp tools/hakios-pin/hakios-pin "$DIR/tools/hakios-pin/"
else
	echo "package.sh: no tools/hakios-pin/hakios-pin -- the zip's data-*.sh and system-*.sh" >&2
	echo "          will not work (tools/hakios-pin/build.sh makes it: clang and lld, or the build container)" >&2
fi

# the WCN7850's firmware, which the launcher stages for the loader's MHI download: both revisions an
# OnePlus 13 carries (peach and peach_v2) are in the one image, and the loader downloads the chip's own
# (it reads the revision off the chip, at the link step). It must sit beside hackios.py or the zip
# boots without the Wi-Fi bring-up. (fw-wifi/network holds this network's credentials and never ships)
if [ -f fw-wifi/combined.bin ]; then
	cp fw-wifi/combined.bin "$DIR/"
else
	echo "package.sh: no fw-wifi/combined.bin -- the zip will boot WITHOUT the Wi-Fi bring-up" >&2
fi

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

KEEPING WHAT iOS MAKES (music, photos, everything of its own) FROM BOOT TO BOOT: the data volume.
By default iOS starts with an empty /private/var every time. The data volume is a disk of iOS's own
that lives in Android's /data -- a pinned file whose blocks the loader gives iOS as a block
device -- made once and kept. Linux; the phone in Android with USB debugging AND root (adb shell su);
adb in PATH:
  sudo apt install hfsprogs              once: mkfs.hfsplus makes the volume's HFS Plus file system
  ./data-create.sh [SIZE]                once: 64G by default (a whole number of MiB); takes minutes
  ./data-boot.sh [SECS]                  every boot with the data kept (900 s by default)
  ./data-delete.sh                       when its space is wanted back: everything on it goes
  python3 tools/hakios-data.py list      the volumes there are
  python3 tools/hakios-data.py look      what the volume holds, without booting iOS
Nothing else of Android's is touched: the volume is a file Android cannot read, and a factory reset
removes it. Each boot reads where Android keeps the volume's blocks right before leaving Android, and
the loader refuses the volume if they have moved -- so Android's own data is never written over.
iOS without the volume (./run.sh) is as before.

WITHOUT THE SYSTEM IMAGE UPLOAD (2.4 GB, a minute at every boot):
  ./system-create.sh                     once per build: the system image on the phone, the same way
  ./system-delete.sh                     it off again: data-boot.sh uploads as before
data-boot.sh checks at every boot that the image on the phone is the one beside the scripts (by
its SHA-256) and uploads it when it is not.
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
