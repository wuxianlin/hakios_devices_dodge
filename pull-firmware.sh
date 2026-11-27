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
#   fw-wifi/   the WCN7850's files, a set for each revision an OnePlus 13 carries. peach, the first
#              (Android's qca_cld3_peach): amss.bin, tmel_peach_10.elf, the ODM's bdwlang.elf and
#              regdb.bin, phy_ucode.elf, aux_ucode.elf, qdss_trace_config_v1.cfg; peach_v2, the
#              second (qca_cld3_peach_v2): amss20.bin, tmel_peach_20.elf, bdwlan.elf, the "20"
#              ucode, qdss_trace_config_v2.cfg. This refreshes the set of the revision the phone
#              carries, with the files Android itself loaded on it (its dmesg says which). Then
#              tools/compose_combined.py makes fw-wifi/combined.bin -- both sets, the image the
#              host stages and the loader downloads the chip's revision's set of -- and holds the
#              loader's table (xnuloader/op13_wifi_files.h, in the tree) to what was composed
#   fw-bt/     the Bluetooth firmware (hmtbtfw*, hmtnv*) from /vendor/bt_firmware/image/
#   fw-video/  the Iris VPU's PAS image (vpu35_4v.mbn): the H.264/HEVC hardware decoder's
#   fw-gpu/    the Adreno 830's three images (gen80000_*) -- these three are small enough that the
#              repository carries them; this refreshes them from the phone
#
# On this phone adsp.mdt is r--r----- system:system: adb cannot read it directly, so root copies
# the lot to /data/local/tmp first (the old port's pull-firmware.sh, proven). The WCN7850's files
# are read one by one with "adb pull": their bytes come back in 64 KiB packets, where a long stream
# out of "adb shell" is one a virtual machine's USB may drop.
set -e
cd "$(dirname "$0")"

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

# ---- the WCN7850: which revision this phone carries, by the stock driver Android has live ----
stock=$(adb shell "ls /sys/module" | tr -d '\r' | grep -m1 '^qca_cld3_peach')
case "$stock" in
	qca_cld3_peach)
		variant=peach; image=amss.bin; tmel=tmel_peach_10.elf; bdf=bdwlang.elf
		phy=phy_ucode.elf; aux=aux_ucode.elf; qdss=qdss_trace_config_v1.cfg ;;
	qca_cld3_peach_v2)
		variant=peach_v2; image=amss20.bin; tmel=tmel_peach_20.elf; bdf=bdwlan.elf
		phy=phy_ucode20.elf; aux=aux_ucode20.elf; qdss=qdss_trace_config_v2.cfg ;;
	*)
		echo "pull-firmware.sh: neither qca_cld3_peach nor qca_cld3_peach_v2 is live on this phone (is it in Android?)"
		exit 1 ;;
esac
echo "== the WCN7850's set for this phone's revision ($variant: Android drives it with $stock)"
echo "   what Android loaded at its boot -- its own choice of files:"
adb shell "su -c 'dmesg | grep -E \"Direct firmware load for peach|loading .(tmel|peach/)|Downloading (BDF|REGDB)\"'" \
	| tr -d '\r' | sed -E "s/.*(for |loading '|BDF: |REGDB: )([^' ,]*).*/      \2/" | sort -u
# the board file is the one Android downloaded, when its log still says so
said=$(adb shell "su -c 'dmesg | grep -m1 \"Downloading BDF\"'" | tr -d '\r' | sed -nE 's/.*BDF: [^/]*\/([^,]*),.*/\1/p')
[ -n "$said" ] && bdf=$said
I=/vendor/firmware_mnt/image; O=/odm/etc/wifi/peach
adb shell "su -c 'rm -rf /data/local/tmp/wfw; mkdir -p /data/local/tmp/wfw; cd /data/local/tmp/wfw;
	cp $I/peach/$image $I/$tmel $I/peach/$phy $I/peach/$aux $I/peach/$qdss $O/$bdf $O/regdb.bin .;
	chmod 644 *; chmod 755 .; chown shell:shell . *; ls'" | tr -d '\r' | tr '\n' ' '; echo
mkdir -p "fw-wifi/$variant"
for f in $image $tmel $phy $aux $qdss $bdf regdb.bin; do
	adb pull "/data/local/tmp/wfw/$f" "fw-wifi/$variant/$f" >/dev/null 2>&1 \
		|| echo "   WARN: $f is not on this phone -- fw-wifi/$variant keeps what it had"
done
adb shell "su -c 'rm -rf /data/local/tmp/wfw'"
echo "   fw-wifi/$variant: $(ls "fw-wifi/$variant" | tr '\n' ' ')"
# both sets into the one staged image, and the loader's table in the tree held to them
python3 tools/compose_combined.py ${TREE:-}

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
echo "next: nothing, when the loader's table checked (above); else the refreshed files are other firmware than"
echo "      the table was made of: python3 tools/compose_combined.py --header says the table afresh, for patches/"
