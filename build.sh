#!/bin/bash
# build.sh: build everything -- the kernel, the loader, the images, the boot image, the package.
#
#   ./build.sh                 # from the root of this clone
#
# Steps (each skipped when its output already exists and is newer; IPSW= is needed by the first build only):
#   1. kernel + loader         docker/build.sh (the container compiles both)
#   2. firmware in place       the repository's fw-* directories copied to the tree
#   3. images                  the IPSW unpacked into the tree the first time (docker/build.sh unpack), then
#                              make images (GL=1 REST=1, as the CI: Mesa, and the rest of our programs)
#   4. boot image              mkpayload.py + mkxnuimg.py (the UEFI FD: made once, docker/build.sh uefi)
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

echo "== the images (GL=1 REST=1, as the CI builds them)"
if [ ! -f "$TREE/userland/system.apfs" ] || [ "$TREE/userland/build.sh" -nt "$TREE/userland/system.apfs" ]; then
	mkdir -p "$TREE/logs"
	if [ ! -d "$TREE/sysimage/rootfs" ]; then
		# the OS files the images are made of: out of the IPSW, once (about 8 GB of disk while it runs)
		if [ -z "${IPSW:-}" ]; then
			echo "build.sh: the tree has no sysimage/rootfs, and no IPSW= was given: the images cannot be built." >&2
			echo "          the first build: IPSW=<iPhone10,3,iPhone10,6_16.5_20F66_Restore.ipsw> ./build.sh" >&2
			exit 1
		fi
		echo "   the OS files out of $(basename "$IPSW") (once)"
		if ! "$REPO/docker/build.sh" unpack "$IPSW" > "$TREE/logs/unpack.log" 2>&1; then
			echo "build.sh: the unpack failed -- the last of $TREE/logs/unpack.log:" >&2
			tail -30 "$TREE/logs/unpack.log" >&2
			exit 1
		fi
	fi
	# the same options as the CI: GL for Mesa 26.2.5 with Freedreno, REST for the rest of our programs
	# (bluetool among them, without which bluetoothd is never driven). What the build says is kept whole:
	# a failure here ends the build.
	if docker exec -w /work -u "$(id -u):$(id -g)" "$CTR" bash -c \
		'export THEOS=/theos PATH="/usr/lib/llvm-20/bin:$PATH" LC_ALL=C; IMAGE_OPTS="GL=1 REST=1" make images' \
		> "$TREE/logs/images.log" 2>&1; then
		tail -3 "$TREE/logs/images.log"
	else
		echo "build.sh: the images were not built -- the last of $TREE/logs/images.log:" >&2
		tail -40 "$TREE/logs/images.log" >&2
		exit 1
	fi
else
	echo "   (already built)"
fi

echo "== the boot image"
FD="$TREE/Mu-Silicium/Build/dodgePkg/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd"
# the UEFI firmware volume: made once, as the CI makes it (a Mu-Silicium clone with this port in it)
[ -s "$FD" ] || "$REPO/docker/build.sh" uefi
if [ -s "$FD" ]; then
	(cd "$TREE" &&
		python3 xnuloader/mkpayload.py -o usb-payload.bin loader.efi=xnuloader/loader.efi --usb &&
		UEFI_FD="$FD" \
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
