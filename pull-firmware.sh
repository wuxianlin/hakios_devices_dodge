#!/bin/bash
# Pull this phone's own firmware out of its Android into the fw-*/ directories at the top of
# the kernel tree, refreshing what the repository carries (the CI builds with it; the vendor's
# licence is why it lives in this private repository and not upstream). The phone is on adb,
# in Android, rooted.
#
# What this port loads:
#   fw-audio/  the audio DSP's signed image (adsp.mdt and its b-files) and the SM8750's second
#              signed image, the DSP's own device tree (adsp_dtb.*), which TrustZone starts before
#              the DSP itself; the compute DSP's pair (cdsp.*, cdsp_dtb.*) comes too, as the
#              cross-check of the audio DSP's release (boot-args adsp_fw=cdsp adsp_pas=18)
#   fw-wifi/   the WCN7850's set from /vendor/firmware_mnt/image/peach/ (the kiwi/ variant comes
#              too, as the hash cross-check): amss20, tmel, regdb, the board file (bdwlan.e* are
#              hashed to the one this phone is, into bdwlan.elf), the PHY and AUX ucode. Then
#              tools/compose_combined.py makes fw-wifi/combined20.bin, the image the loader
#              downloads over MHI, and rewrites the loader's file table (xnuloader/op13_wifi_files.h)
#   fw-bt/     the Bluetooth firmware (hmtbtfw*, hmtnv*) from /vendor/bt_firmware/image/
#   fw-video/  the Iris VPU's PAS image (vpu35_4v.mbn): the H.264/HEVC hardware decoder's
#   fw-gpu/    the Adreno 830's three images (gen80000_*) -- these three are small enough that the
#              repository carries them; this refreshes them from the phone
#
# On this phone adsp.mdt is r--r----- system:system: adb cannot read it directly, so root copies
# the lot to /data/local/tmp first (the old port's pull-firmware.sh, proven).
set -e
cd "$(dirname "$0")/../.."

adb get-state >/dev/null 2>&1 || { echo "pull-firmware.sh: the phone is not on adb"; exit 1; }

src=""
for d in /vendor/firmware_mnt/image /vendor/firmware; do
	if adb shell "su -c 'test -f $d/adsp.mdt'" >/dev/null 2>&1; then src=$d; break; fi
done
[ -n "$src" ] || { echo "pull-firmware.sh: no adsp.mdt under /vendor/firmware_mnt/image or /vendor/firmware"; exit 1; }
echo "== the audio DSP's images ($src)"
adb shell "su -c 'rm -rf /data/local/tmp/fw; mkdir -p /data/local/tmp/fw;
	cp $src/adsp.mdt $src/adsp.b* $src/adsp_dtb.* $src/cdsp.mdt $src/cdsp.b* $src/cdsp_dtb.* /data/local/tmp/fw/ 2>/dev/null;
	chmod 644 /data/local/tmp/fw/*'"
rm -rf fw-audio && mkdir -p fw-audio
adb pull /data/local/tmp/fw/. fw-audio/ >/dev/null 2>&1
adb shell "su -c 'rm -rf /data/local/tmp/fw'"
# the amplifiers' configuration: the AW88230s refuse to play without it (the sound driver
# looks for /fw/aw882xx_acf.bin in the image; it lived only in working trees until now)
adb shell "su -c 'cp /odm/firmware/aw882xx_acf.bin /data/local/tmp/ 2>/dev/null || cp /vendor/etc/aw882xx_acf.bin /data/local/tmp/ 2>/dev/null; chmod 644 /data/local/tmp/aw882xx_acf.bin 2>/dev/null'"
adb pull /data/local/tmp/aw882xx_acf.bin fw-audio/aw882xx_acf.bin >/dev/null 2>&1 \
    || echo "   WARN: no aw882xx_acf.bin on the phone -- the speakers will not play"
adb shell "su -c 'rm -f /data/local/tmp/aw882xx_acf.bin'"
ls fw-audio | head -4; echo "   ($(ls fw-audio | wc -l) files)"

echo "== the WCN7850's set (the six native files from /odm/etc/wifi/peach -- this phone's ODM"
echo "   calibration, the set the loader's hash table is made of; the base amss20 image from the"
echo "   vendor's own partition; the vendor's stock peach kept beside as the cross-check)"
mkdir -p fw-wifi/raw
adb shell "su -c 'rm -rf /data/local/tmp/wfw; mkdir -p /data/local/tmp/wfw/odm /data/local/tmp/wfw/peach;
	cp /odm/etc/wifi/peach/* /data/local/tmp/wfw/odm/ 2>/dev/null;
	cp /vendor/firmware_mnt/image/peach/amss20.bin /data/local/tmp/wfw/odm/ 2>/dev/null;
	cp /vendor/firmware_mnt/image/tmel_peach_*.elf /data/local/tmp/wfw/odm/ 2>/dev/null;
	cp /vendor/firmware_mnt/image/peach/* /data/local/tmp/wfw/peach/ 2>/dev/null;
	chmod -R a+rwX /data/local/tmp/wfw'"
adb shell "su -c 'cd /data/local/tmp/wfw && tar cf - .'" | tar xf - -C fw-wifi/raw/
adb shell "su -c 'rm -rf /data/local/tmp/wfw'"
mkdir -p fw-wifi/peach
# the hash table decides: of everything pulled, only files matching the loader's table are
# applied -- the stock board file and regulatory database never overwrite the ODM set
python3 devices/dodge/tools/match_files.py fw-wifi/raw/odm --apply fw-wifi/peach
cp -f fw-wifi/raw/odm/amss20.bin fw-wifi/peach/amss20.bin 2>/dev/null || true
echo "   composed of (fw-wifi/peach): $(ls fw-wifi/peach | tr '\n' ' ')"

echo "== the Bluetooth firmware (/vendor/bt_firmware/image)"
rm -rf fw-bt && mkdir -p fw-bt
adb shell "su -c 'cp /vendor/bt_firmware/image/hmt* /data/local/tmp/ 2>/dev/null'"
for f in $(adb shell "su -c 'ls /vendor/bt_firmware/image/ | grep ^hmt'" | tr -d '\r'); do
	adb pull /data/local/tmp/$f fw-bt/$f >/dev/null 2>&1
done
adb shell "su -c 'rm -f /data/local/tmp/hmt*'"
echo "   ($(ls fw-bt | wc -l) files)"

echo "== the Adreno 830's images (a refresh of the three the repository carries)"
for f in gen80000_aqe.fw gen80000_gmu.bin gen80000_sqe.fw; do
	adb pull /vendor/firmware/$f fw-gpu/$f >/dev/null 2>&1 || true
done
ls fw-gpu

echo "== the Iris VPU's image (the H.264/HEVC hardware decoder's; the one the repository carries)"
mkdir -p fw-video
adb pull /vendor/firmware/vpu35_4v.mbn fw-video/vpu35_4v.mbn >/dev/null 2>&1 || true
ls -la fw-video | tail -1

echo
echo "next: python3 devices/dodge/tools/compose_combined.py \$PWD   # fw-wifi/combined20.bin + the loader's file table"
echo "      (and rebuild the loader afterwards: devices/dodge/docker/build.sh loader)"
