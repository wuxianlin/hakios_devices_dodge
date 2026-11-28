#!/bin/bash
# build.sh: hakios-pin, a static ARM64 Linux program with nothing from libc -- Android has no headers or
# libraries to build against, and the kernel's ABI is all this one needs.
#
#   ./build.sh          -> hakios-pin, to copy to the phone (adb push, then chmod 755)
#
# Built in the build container when it is running (docker/run.sh makes it), as the loader is; on this
# computer otherwise -- with clang and ld.lld (llvm-20's where they are, as the CI's runner has them),
# which HAKIOS_NO_DOCKER=1 asks for even when the container is up.
#
# The build ends with a status that says what became of it: the commands run in a shell of their own (a
# "bash -c" inside the container, or here), and it is that shell that stops at a command's failure -- set -e
# here alone would not reach into it, and a clang that refused the source left the program of the build
# before standing, to be listed and shipped.
set -e
cd "$(dirname "$0")"
NAME="${HAKIOS_CTR:-hakios-ctr}"

BUILD='
	set -e
	if [ -d /usr/lib/llvm-20/bin ]; then export PATH="/usr/lib/llvm-20/bin:$PATH"; fi
	export LC_ALL=C
	clang --target=aarch64-linux-gnu -fuse-ld=lld -nostdlib -nostdinc -ffreestanding -fno-builtin \
	  -fno-stack-protector -fno-asynchronous-unwind-tables -fno-ident -fno-stack-check \
	  -O2 -Wall -Wextra -Werror -static -Wl,--build-id=none -Wl,-z,noseparate-code -Wl,--no-pie \
	  -o hakios-pin hakios-pin.c
	ls -la hakios-pin
	readelf -h hakios-pin | grep -E "Class|Machine|Type"
	readelf -l hakios-pin | grep -E "INTERP|LOAD" || true
	readelf -d hakios-pin 2>/dev/null | head -3
	if command -v llvm-objdump >/dev/null 2>&1; then
		llvm-objdump -d hakios-pin | grep -c -E "\bbl\b" | sed "s/^/calls: /"
	fi
'

if [ "${HAKIOS_NO_DOCKER:-}" != 1 ] && [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = "true" ]; then
	docker exec -w "$PWD" -u "$(id -u):$(id -g)" "$NAME" bash -c "$BUILD"
else
	for t in clang ld.lld readelf; do
		command -v "$t" >/dev/null 2>&1 || {
			echo "build.sh: no $t on this computer, and no build container $NAME running: clang and lld (llvm-20)" >&2
			echo "          are needed -- or docker/run.sh, which builds it in the container" >&2
			exit 1
		}
	done
	bash -c "$BUILD"
fi
