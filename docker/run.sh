#!/bin/bash
# docker/run.sh: make the build container if it is not there, then leave it running.
#
#   docker/run.sh [THEOS-DIR]        # default: ~/theos
#   HAKIOS_CTR=NAME                  the container's name (default hakios-ctr); HAKIOS_IMAGE=TAG the image
#
# The container mounts:
#   the tree  (this clone's kernel/ subdirectory, made by apply-kernel-patches.sh) at /work
#   the clone (the tree's devices/dodge is a symlink to it)                     at its own path
#   theos    (cctools' ld64 and the iPhoneOS SDK)                               at /theos
set -e
REPO=$(cd "$(dirname "$0")/.." && pwd -P)
TREE="$REPO/kernel"
NAME="${HAKIOS_CTR:-hakios-ctr}"
IMAGE="${HAKIOS_IMAGE:-hakios-build:24.04}"
THEOS_DIR="${1:-$HOME/theos}"

[ -f "$TREE/Makefile" ] || {
	echo "run.sh: no tree at $TREE -- run ./apply-kernel-patches.sh first" >&2
	exit 1
}
[ -x "$THEOS_DIR/toolchain/linux/iphone/bin/ld" ] || {
	echo "run.sh: $THEOS_DIR is not theos with its Linux toolchain" >&2
	echo "  the upstream's docs/getting-started/building.md says how; or pass its path" >&2
	exit 1
}

docker inspect "$NAME" >/dev/null 2>&1 || {
	docker image inspect "$IMAGE" >/dev/null 2>&1 || {
		echo "== building $IMAGE (once)"
		docker build -t "$IMAGE" --build-arg UID="$(id -u)" --build-arg GID="$(id -g)" "$REPO/docker"
	}
	echo "== making the container $NAME"
	docker run -d --name "$NAME" -w /work -u "$(id -u):$(id -g)" \
		-v "$TREE":/work \
		-v "$REPO":"$REPO" \
		-v "$THEOS_DIR":/theos \
		"$IMAGE"
}
docker start "$NAME" >/dev/null 2>&1 || true
docker exec -w /work "$NAME" true 2>/dev/null || {
	echo "run.sh: the container is there but not usable" >&2
	exit 1
}
echo "== the container is up"
