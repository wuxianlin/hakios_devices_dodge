#!/bin/bash
# docker/build.sh: build the kernel (or the loader) inside the container.
#
#   docker/build.sh             # make kernel
#   docker/build.sh loader      # make loader
#   docker/build.sh uefi        # make the UEFI firmware volume (once: Mu-Silicium with this port in it)
#   docker/build.sh unpack IPSW # the OS files out of your IPSW into the tree's sysimage/ (once)
#   docker/build.sh clean       # remove the old kernel to force a full rebuild
#
# pipefail: the kernel step's output ends in "| tail -8", and a pipeline's status is its last command's
# -- a compile that failed inside the container came out of here as 0, and ./build.sh went on with no
# kernel (the step takes the old one out of the way first).
set -eo pipefail
REPO=$(cd "$(dirname "$0")" && pwd -P)
TREE=$(cd "$REPO/.." && pwd -P)/kernel     # the build tree (REPO is this directory: the kernel step's /work)
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
unpack)
	# 1b. the OS files out of the IPSW into sysimage/ (sysimage/unpack.sh: once; what userland/build.sh makes the
	#     images of). A container of its own for this step, with the IPSW's directory mounted at /ipsw: the build
	#     container mounts the tree, the repository and theos, and nothing else.
	IPSW="${2:?usage: docker/build.sh unpack IPSW}"
	[ -f "$IPSW" ] || { echo "docker/build.sh: no $IPSW" >&2; exit 1; }
	IPSW=$(cd "$(dirname "$IPSW")" && pwd -P)/$(basename "$IPSW")
	docker run --rm -v "$TREE":/work -v "$(dirname "$IPSW")":/ipsw:ro -w /work -u "$(id -u):$(id -g)" \
		"${HAKIOS_IMAGE:-hakios-build:24.04}" bash -c "export LC_ALL=C; make unpack IPSW=/ipsw/$(basename "$IPSW")"
	;;
uefi)
	# 2b. the UEFI firmware volume, the way the CI makes it -- Mu-Silicium at the revision devices/dodge/uefi/BASE
	# names, with this repository's port applied (devices/dodge/uefi/apply.sh) -- into
	# Mu-Silicium/Build/dodgePkg/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd, what ./build.sh's boot image needs.
	# Once: a tree that has it is left as it is.
	docker exec -w /work -u "$(id -u):$(id -g)" "$NAME" bash -c '
		set -e
		FD=Mu-Silicium/Build/dodgePkg/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd
		DTB=Mu-Silicium/Resources/DTBs/dodge.dtb
		export LC_ALL=C
		if [ -s "$FD" ] && [ -s "$DTB" ]; then echo "the UEFI FD is there: $FD ($(wc -c < "$FD") bytes)"; exit 0; fi
		devices/dodge/uefi/apply.sh /work          # (the tree by name: devices/dodge is a link to the repository here)
		[ -d venv ] || python3 -m venv venv
		venv/bin/pip -q install -r Mu-Silicium/pip-requirements.txt
		D=Mu-Silicium/Mu_Basecore/BaseTools/Bin/mu_nasm_extdep   # mu_nasm without Mono
		mkdir -p "$D/Linux-x-64" && echo "version: 20016.1.1" > "$D/extdep_state.yaml"
		ln -sf /usr/bin/nasm "$D/Linux-x-64/nasm" && ln -sf /usr/bin/ndisasm "$D/Linux-x-64/ndisasm"
		( cd Mu-Silicium && ../venv/bin/python3 build_uefi.py -d dodge -r RELEASE )
		ls -la "$FD" "$DTB"' 2>&1 | tail -15
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
	# 2. this repository's patch on Apple's tree (patches/xnu.patch: the fixes the phone's kernel needs), over
	#    the common tree's files and drivers as they are now (tools/xnu-patch.sh: every time, and idempotent)
	docker exec -w /work -u "$(id -u):$(id -g)" "$NAME" "$REPO/../tools/xnu-patch.sh" /work

	# 2. the ath12k station stack: BUILT from FreeBSD's LinuxKPI by mk-ath12k.sh
	#    (the first build downloads the 253 MB archive; its own stamp makes every
	#    later one incremental). Without the object the kernel loses Wi-Fi.
	docker exec -w /work -u "$(id -u):$(id -g)" "$NAME" bash -c \
		'export THEOS=/theos PATH="/usr/lib/llvm-20/bin:$PATH" LC_ALL=C;
		 [ ! -e xnu-deps/host/bin/ld64 ] && THEOS=/theos xnu-deps/setup.sh;
		 [ -f xnu-deps/build/ath12k/out/ath12k.o ] || devices/dodge/tools/mk-ath12k.sh'

	# 3. the kernel. The kernel of the build before is taken out of the way first: "make kernel" goes on past
	#    a failed step (tools/make-kernel.sh judges the log instead), and an older kernel still standing there
	#    would pass for the new one -- a build that fails must fail.
	docker exec -w /work -u "$(id -u):$(id -g)" "$NAME" bash -c \
		'export THEOS=/theos PATH="/usr/lib/llvm-20/bin:$PATH" LC_ALL=C;
		 [ ! -f .config ] && make dodge_defconfig;
		 [ ! -e xnu-deps/host/bin/ld64 ] && THEOS=/theos xnu-deps/setup.sh;
		 K=xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837;
		 rm -f hakios-kernel "$K";
		 make kernel > /tmp/make-kernel.log 2>&1; rc=$?;
		 if [ ! -s "$K" ]; then
			 echo "the kernel was NOT built (make-kernel said $rc): the errors, as the build gave them:" >&2;
			 grep -n -B3 -A5 -E "error:|fatal error|undefined symbol|make-kernel:" /tmp/make-kernel.log | tail -40 >&2;
			 exit 1;
		 fi;
		 cp "$K" hakios-kernel;
		 ls -la hakios-kernel;
		 N=$(strings -a hakios-kernel | grep -c "ath12k\|OnePlus13 Peach");
		 [ "$N" -ge 100 ] || { echo "ath12k: NOT linked ($N markers) -- the kernel would be 565 KB short and every address shifted; failing the build" >&2; exit 1; };
		 echo "ath12k: linked, $N markers"' 2>&1 | tail -8
	;;
esac
