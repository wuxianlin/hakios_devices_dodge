#!/bin/bash
# apply-kernel-patches.sh: clone the upstream, apply our patches, link ourselves in.
#
#   ./apply-kernel-patches.sh        # from the root of this clone
#
# The layout it makes (everything inside this clone; kernel/ is git-ignored):
#
#   this-clone/            the source of truth (untouched)
#     kernel/              the build tree -- the upstream clone at the pinned revision,
#                          our two patches, this clone symlinked in as devices/dodge
#     kernel/devices/dodge -> this clone (the drivers ride the tree's own sync)
#
# After this: ./build.sh
set -e
HERE=$(cd "$(dirname "$0")" && pwd -P)
DIR="$HERE/kernel"
URL="${HAKIOS_UPSTREAM:-https://github.com/oosexgang/hakios-xnu-kernel.git}"
REV=63fc2b4633bac3af8e57dadab9f5a8d4cb940438

if [ ! -d "$DIR/.git" ]; then
	echo "== the upstream at $REV (one commit, no history)"
	rm -rf "$DIR"
	git init -q "$DIR"
	(cd "$DIR" &&
		git remote add origin "$URL" &&
		git fetch -q --depth 1 origin "$REV" &&
		git checkout -q FETCH_HEAD)
fi

echo "== the two patches"
(cd "$DIR" &&
	for p in kernel-common xnu-dodge; do
		echo "   patches/$p.patch"
		git apply --whitespace=nowarn "$HERE/patches/$p.patch"
	done)

echo "== this clone linked in as devices/dodge"
rm -rf "$DIR/devices/dodge"
ln -s "$HERE" "$DIR/devices/dodge"

echo "== normalize the xnu-src git state (every build tells git describe the same story)"
(cd "$DIR/xnu-src/xnu-8796.121.2" && git add -A && git -c user.name=dodge -c user.email=dodge@local commit -q -m "the dodge patches" 2>/dev/null || true)

echo "== the ath12k stack into the tree's wifi/"
rm -rf "$DIR/wifi/ath12k"
cp -r "$HERE/wifi/ath12k" "$DIR/wifi/"

echo "== done"
echo "   next: ./build.sh"
