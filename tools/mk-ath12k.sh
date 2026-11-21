#!/bin/bash
# Build the phone's (OnePlus 13 / Peach / kiwi) Wi-Fi driver object for our kernel:
# ios-dodge's device-verified (2026-10-30) ath12k station stack on FreeBSD's LinuxKPI
# headers and the wifi/lx + wifi/lx12 compatibility layer, compiled in this container
# and linked into the kernel by xnu-build.sh (HAKIOS_OBJECTS).
#
# Sources live in wifi/ath12k/ (copied from ios-dodge source/wifi-peach/darwin-ath12k-v2):
#   src/ath12k      the driver, Peach-adapted (17cb:110e, adopted MHI transport)
#   wifi/lx         the LinuxKPI compatibility layer (also used by ath11k)
#   wifi/lx12       the ath12k-era additions (bus, glue, fixups)
#   linuxref        reference headers the fixup checks against
# The kernel's side of the ABI is bsd/sys/hakios.lx.h and bsd/sys/hakios.qmi.h.
set -e
# ROOT is the tree: this runs from its tools/ during development, from a device
# repository laid inside one (devices/<dev>/tools/, real or through the symlink
# apply-kernel-patches.sh makes), or from a repository holding its own tree at
# kernel/ -- one marker, checked at both levels, walking up
d=$(cd "$(dirname "$0")" && pwd)
while [ "$d" != "/" ]; do
	for t in "$d" "$d/kernel"; do
		[ -d "$t/xnu-src/xnu-8796.121.2" ] && ROOT=$t && break 2
	done
	d=$(dirname "$d")
done
[ -n "${ROOT:-}" ] && [ -d "$ROOT/xnu-src/xnu-8796.121.2" ] || { echo "mk-ath12k.sh: no kernel tree above this script (xnu-src/xnu-8796.121.2 not found)" >&2; exit 1; }
A12=$ROOT/wifi/ath12k
B=$ROOT/xnu-deps/build/ath12k
W=$A12/wifi/lx

FREEBSD=15.1
FREEBSD_SHA256=cf5762da53fd52e1eaf0f9ceee9bf58cbe314c821031d0d9ffa76823185a89e1
FSYS="compat/linuxkpi sys arm64/include net net80211 netinet netinet6 vm kern tools libkern ddb
	security fs/pseudofs crypto opencrypto geom bsm isa cam rpc xdr x86/include ofed/include
	dev/pci dev/ofw dev/fdt dev/acpica dev/usb dev/iicbus dev/backlight
	contrib/ck/include contrib/dev/acpica/include contrib/libnv"
PATCHES=$(cat "$W"/patches/*.patch "$A12"/wifi/lx12/fixup.py 2>/dev/null | sha256sum | cut -d' ' -f1)
if [ "$(cat $B/freebsd/.release 2>/dev/null)" != "$FREEBSD $FREEBSD_SHA256 $PATCHES" ]; then
	mkdir -p "$B"; cd "$B"
	[ -f freebsd-$FREEBSD-src.txz ] || curl -sL --fail -o freebsd-$FREEBSD-src.txz \
		https://download.freebsd.org/releases/amd64/$FREEBSD-RELEASE/src.txz
	# resume an interrupted download (the archive is 253 MB on a slow link)
	until echo "$FREEBSD_SHA256  freebsd-$FREEBSD-src.txz" | sha256sum -c --quiet 2>/dev/null; do
		curl -sL --fail -C - -o freebsd-$FREEBSD-src.txz \
			https://download.freebsd.org/releases/amd64/$FREEBSD-RELEASE/src.txz || sleep 5
	done
	rm -rf freebsd gen obj out
	mkdir freebsd
	tar xJf freebsd-$FREEBSD-src.txz -C freebsd --strip-components=2 usr/src/COPYRIGHT \
		$(for d in $FSYS; do echo usr/src/sys/$d; done)
	for p in "$W"/patches/*.patch; do
		[ -f "$p" ] && patch -s --no-backup-if-mismatch -d freebsd -p1 < "$p"
	done
	echo "$FREEBSD $FREEBSD_SHA256 $PATCHES" > freebsd/.release
else
	cd "$B"
fi
[ -d gen ] || {
	mkdir gen
	(
		cd gen
		S=$B/freebsd/sys
		ln -s $S/arm64/include machine
		for m in kern/device_if.m kern/bus_if.m dev/pci/pci_if.m dev/pci/pci_iov_if.m dev/pci/pcib_if.m \
			dev/iicbus/iicbus_if.m dev/iicbus/iicbb_if.m dev/backlight/backlight_if.m dev/usb/usb_if.m \
			dev/ofw/ofw_bus_if.m; do
			awk -f $S/tools/makeobjops.awk $S/$m -h
		done
		for o in h p q; do awk -f $S/tools/vnode_if.awk $S/kern/vnode_if.src -$o; done
		for o in $(grep -rhoE "opt_[a-z0-9_]+\.h" $S | sort -u) lkpi_iic_if.h; do : > $o; done
		printf '#define INET 1\n#define INET6 1\n' > opt_inet.h
		printf '#define INET6 1\n' > opt_inet6.h
		printf '#define SMP 1\n' > opt_global.h
	)
}
cd "$A12"
python3 wifi/lx12/fixup.py "$B/freebsd/sys" src/ath12k linuxref/include
S=$B/freebsd/sys
cd "$B"

CC="clang -target arm64-apple-ios16.5"
LD=$ROOT/xnu-deps/host/bin/ld64
NM=llvm-nm

DRIVER="core hal wmi mac reg htc qmi dp dp_tx dp_rx dp_htt dp_peer debug ce peer dbring dp_mon fw p2p
	wifi7/core wifi7/hw wifi7/hal wifi7/hal_qcc2072 wifi7/hal_wcn7850 wifi7/hal_qcn9274 wifi7/hal_rx wifi7/hal_tx
	wifi7/dp wifi7/dp_rx wifi7/dp_tx wifi7/dp_mon wifi7/ce wifi7/wmi"
CFLAGS="-mkernel -mgeneral-regs-only -fno-builtin -fno-common -ffreestanding -fno-strict-aliasing
	-funsigned-char -nostdinc -std=gnu17 -O2 -g -w
	-D__FreeBSD__=15 -D_KERNEL -DKLD_MODULE -DHAVE_KERNEL_OPTION_HEADERS -include opt_global.h
	-I$A12/wifi/lx12/include -I$W/include -I$B/gen -I$S -I$S/contrib/ck/include
	-I$S/compat/linuxkpi/common/include -I$S/compat/linuxkpi/dummy/include
	-include $S/compat/linuxkpi/common/include/linux/kconfig.h -DLINUXKPI_VERSION=70000"
DFLAGS="-I$A12/src/ath12k -I$A12/src -include $W/include/lx_prelude.h -include lx12_prelude.h -include lx12_hakios_compat.h -DCONFIG_ATH12K_DEBUG
	-DKBUILD_MODNAME=\"ath12k\""
for h in lx qmi; do
	[ -f "$ROOT/xnu-src/xnu-8796.121.2/bsd/sys/hakios.$h.h" ] || { echo "mk-ath12k.sh: no bsd/sys/hakios.$h.h in the kernel tree" >&2; exit 1; }
done
DFLAGS="$DFLAGS -DLX_REDWOOD_LX_H=\"$ROOT/xnu-src/xnu-8796.121.2/bsd/sys/hakios.lx.h\" -DLX_REDWOOD_QMI_H=\"$ROOT/xnu-src/xnu-8796.121.2/bsd/sys/hakios.qmi.h\""

mkdir -p "$B/obj" "$B/out"
rm -f "$B"/obj/*.err
BUILD_CONTEXT=$(printf '%s\n' "$ROOT" "$CC" "$CFLAGS" "$DFLAGS" | sha256sum | cut -d' ' -f1)
CACHE_VALID=0
[ "$(cat $B/obj/.build-context 2>/dev/null)" != "$BUILD_CONTEXT" ] || CACHE_VALID=1
compile() {
	local src=$1 obj=$2
	shift 2
	if [ "$CACHE_VALID" = 1 ] && [ -f "$obj" ] && [ "$obj" -nt "$src" ] && [ -f "$obj.d" ] && [ -z "$(find $(sed -e 's/^[^:]*://' -e 's/\\$//' "$obj.d") "$0" -newer "$obj" 2>/dev/null | head -1)" ]; then
		return
	fi
	$CC $CFLAGS "$@" -MMD -MF "$B/$obj.d" -c "$src" -o "$B/$obj" 2> "$B/$obj.err" || rm -f "$obj"
}
LXSRC="$W/src/lx_base.c $W/src/lx_80211.c $W/src/lx_wpa.c $A12/wifi/lx12/src/lx12_bus.c $A12/wifi/lx12/src/lx12_glue.c"
for f in $DRIVER; do compile $A12/src/ath12k/$f.c obj/ath12k_$(echo $f | tr / _).o $DFLAGS & done
for f in $LXSRC; do compile "$f" obj/lx_$(basename "$f" .c).o $DFLAGS -I$W/src & done
wait
bad=
for f in obj/*.err; do
	[ -s "$f" ] && [ ! -f "${f%.err}" ] && { echo "--- $(basename "$f" .o.err)"; grep -m6 -A3 "error:" "$f"; bad=1; }
done
[ -z "$bad" ] || exit 1
printf '%s\n' "$BUILD_CONTEXT" > obj/.build-context
OBJECTS=$(for f in $DRIVER; do echo obj/ath12k_$(echo $f | tr / _).o; done; for f in $LXSRC; do echo obj/lx_$(basename "$f" .c).o; done)

KERNEL="memcmp memcpy memmove memset strcmp strncmp strlen strchr strrchr strlcpy strlcat bzero
	snprintf vsnprintf __stack_chk_fail __stack_chk_guard"
$LD -r -arch arm64 $(for o in $OBJECTS; do echo $B/$o; done) -o out/all.o 2> out/ld.err || { cat out/ld.err; exit 1; }
$NM -u --format=just-symbols out/all.o | sed 's/^_//' | sort > out/undefined.txt
: > out/stubs.txt
: > out/stubs.s
missing=
for s in $(cat out/undefined.txt); do
	case " $(echo $KERNEL) " in *" $s "*) continue ;; esac
	case $s in
	lx_os_*|qmi_*|hakios_qmi_*|section\$*) continue ;;
	ath12k_*|__ath12k_*) ;;
	*) echo "mk-ath12k.sh: nothing defines $s" >&2; missing=1; continue ;;
	esac
	echo $s >> out/stubs.txt
	printf '\t.globl _%s\n_%s:\n\tadrp x0, 1f@PAGE\n\tadd x0, x0, 1f@PAGEOFF\n\tb _lx_stub\n\t.cstring\n1:\t.asciz "%s"\n\t.text\n\t.p2align 2\n' \
		$s $s $s >> out/stubs.s
done
[ -z "$missing" ] || exit 1
$CC -c out/stubs.s -o out/stubs.o
printf '_lx_prepare\n_lx_start\n_lx_stop\n_lx_transmit\n' > out/exported.txt
$LD -r -arch arm64 -exported_symbols_list out/exported.txt out/all.o out/stubs.o -o out/ath12k.o 2> out/ld.err ||
	{ cat out/ld.err; exit 1; }
rm -f out/all.o
echo "mk-ath12k.sh: out/ath12k.o: $(llvm-size out/ath12k.o | awk 'NR == 2 { print $1 + $2 }') bytes of code and data from" \
	"$(echo $DRIVER | wc -w) of the driver's files; $(wc -l < out/stubs.txt) functions of the others are stubs (out/stubs.txt);" \
	"asks the kernel for $($NM -u --format=just-symbols out/ath12k.o | grep -vc 'section\$') names"
