#!/bin/bash
# build.sh: build everything -- the kernel, the loader, the images, the boot image, the package.
#
#   ./build.sh                 # from the root of this clone
#
# Steps (each skipped when its output already exists and is newer):
#   1. kernel + loader         docker/build.sh (the container compiles both)
#   2. firmware in place       the repository's fw-* directories copied to the tree
#   3. images                  make images (needs IPSW=; GL=1 for Mesa)
#   4. boot image              mkpayload.py + mkxnuimg.py (needs the UEFI FD)
#   5. package                 package.sh -> dist/hakios-dodge-*.zip
set -e
REPO=$(cd "$(dirname "$0")" && pwd -P)
TREE="$REPO/kernel"
CTR="${HAKIOS_CTR:-hakios-ctr}"

[ -f "$TREE/Makefile" ] || {
	echo "build.sh: no tree at $TREE -- run ./apply-kernel-patches.sh first" >&2
	exit 1
}

echo "== the kernel and the loader"
"$REPO/docker/build.sh"
"$REPO/docker/build.sh" loader

echo "== the firmware in place"
for d in fw-gpu fw-audio fw-bt fw-wifi fw-video; do
	[ -d "$REPO/$d" ] && rm -rf "$TREE/$d" && cp -r "$REPO/$d" "$TREE/$d"
done

echo "== the images (needs IPSW= for the first build; GL=1)"
if [ ! -f "$TREE/userland/system.apfs" ] || [ "$TREE/userland/build.sh" -nt "$TREE/userland/system.apfs" ]; then
	docker exec -w /work -u "$(id -u):$(id -g)" "$CTR" bash -c \
		'export THEOS=/theos PATH="/usr/lib/llvm-20/bin:$PATH" LC_ALL=C;
		 [ -n "'"${IPSW:-}"'" ] && IPSW="'"${IPSW:-}"'" IMAGE_OPTS="GL=1" make images ||
		 { echo "(skipped: no IPSW= given, or images already built)" >&2; }' 2>&1 | tail -3
else
	echo "   (already built)"
fi

echo "== the boot image"
if [ ! -f "$TREE/Mu-Silicium/Build/dodgePkg/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd" ]; then
	echo "   (the UEFI FD is not there -- building it takes a Mu-Silicium clone; for now,"
	echo "    copy it from a previous build, or let the CI make it)"
fi
if [ -f "$TREE/Mu-Silicium/Build/dodgePkg/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd" ]; then
	(cd "$TREE" &&
		python3 xnuloader/mkpayload.py -o usb-payload.bin loader.efi=xnuloader/loader.efi --usb &&
		UEFI_FD=Mu-Silicium/Build/dodgePkg/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd \
		DTB=Mu-Silicium/Resources/DTBs/dodge.dtb \
		FD_BASE=0xA7100000 FD_SIZE=0x300000 PAYLOAD_BASE=0xF0000000 BOOT_HEADER=4 \
		python3 mkxnuimg.py -p usb-payload.bin -o boot.img)
else
	echo "   (no UEFI FD -- boot.img skipped)"
fi

echo "== the package"
if [ -f "$TREE/boot.img" ]; then
	cd "$REPO" && ./package.sh
else
	echo "   (no boot.img -- package skipped; the kernel is at $TREE/hakios-kernel)"
fi
