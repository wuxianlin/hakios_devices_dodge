#!/bin/bash
# docker/build.sh: build the kernel (or the loader) inside the container.
#
#   docker/build.sh             # make kernel
#   docker/build.sh loader      # make loader
#   docker/build.sh clean       # remove the old kernel to force a full rebuild
set -e
REPO=$(cd "$(dirname "$0")" && pwd -P)
NAME="${HAKIOS_CTR:-hakios-ctr}"

[ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = "true" ] || {
	"$REPO/run.sh"
	[ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = "true" ] || {
		echo "the container did not come up" >&2
		exit 1
	}
}

STEP="${1:-kernel}"
case "$STEP" in
clean)
	docker exec -w /work -u "$(id -u):$(id -g)" "$NAME" bash -c 'rm -f hakios-kernel'
	;;
loader)
	docker exec -w /work -u "$(id -u):$(id -g)" "$NAME" bash -c \
		'export THEOS=/theos PATH="/usr/lib/llvm-20/bin:$PATH" LC_ALL=C; cd xnuloader && ./build.sh && ls -la loader.efi'
	;;
kernel|*)
	# 1. the XNU sources (once; three tries -- Apple's GitHub can flake).
	#    XNU_URL/HFS_URL pass through: a local clone or bundle works offline.
	docker exec -w /work -u "$(id -u):$(id -g)" \
		${XNU_URL:+-e XNU_URL="$XNU_URL"} ${HFS_URL:+-e HFS_URL="$HFS_URL"} \
		"$NAME" bash -c '
		[ -d xnu-src/xnu-8796.121.2 ] && exit 0
		for try in 1 2 3; do
			xnu/apply.sh && exit 0
			echo "xnu/apply.sh: try $try failed; waiting 15 s"
			sleep 15
		done
		exit 1'
	# 2. the fixes the phone needs (every time -- idempotent)
	docker exec -w /work -u "$(id -u):$(id -g)" "$NAME" "$REPO/xnu-fixes.sh" /work/xnu-src/xnu-8796.121.2

	# 2. the ath12k station stack: BUILT from FreeBSD's LinuxKPI by mk-ath12k.sh
	#    (the first build downloads the 253 MB archive; its own stamp makes every
	#    later one incremental). Without the object the kernel loses Wi-Fi.
	docker exec -w /work -u "$(id -u):$(id -g)" "$NAME" bash -c \
		'export THEOS=/theos PATH="/usr/lib/llvm-20/bin:$PATH" LC_ALL=C;
		 [ ! -e xnu-deps/host/bin/ld64 ] && THEOS=/theos xnu-deps/setup.sh;
		 [ -f xnu-deps/build/ath12k/out/ath12k.o ] || devices/dodge/tools/mk-ath12k.sh'

	# 3. the kernel
	docker exec -w /work -u "$(id -u):$(id -g)" "$NAME" bash -c \
		'export THEOS=/theos PATH="/usr/lib/llvm-20/bin:$PATH" LC_ALL=C;
		 [ ! -f .config ] && make dodge_defconfig;
		 [ ! -e xnu-deps/host/bin/ld64 ] && THEOS=/theos xnu-deps/setup.sh;
		 make kernel;
		 [ -s hakios-kernel ] || cp xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837 hakios-kernel;
		 ls -la hakios-kernel;
		 N=$(strings -a hakios-kernel | grep -c "ath12k\|OnePlus13 Peach");
		 [ "$N" -ge 100 ] || { echo "ath12k: NOT linked ($N markers) -- the kernel would be 565 KB short and every address shifted; failing the build" >&2; exit 1; };
		 echo "ath12k: linked, $N markers"' 2>&1 | tail -8
	;;
esac
