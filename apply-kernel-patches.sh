#!/bin/bash
# apply-kernel-patches.sh: clone the upstream, apply our patch, link ourselves in.
#
#   ./apply-kernel-patches.sh        # from the root of this clone
#
# The layout it makes (everything inside this clone; kernel/ is git-ignored):
#
#   this-clone/            the source of truth (untouched)
#     kernel/              the build tree -- the upstream clone at the pinned revision, our patch on it,
#                          this clone symlinked in as devices/dodge
#     kernel/devices/dodge -> this clone (the drivers ride the tree's own sync)
#
# One patch per repository. patches/kernel-common.patch is everything this port changes in the common
# repository (the rewrites of its drivers, the loader, user space, the tools, its own files for Apple's
# tree); this script puts it on the tree. patches/xnu.patch is what it changes in Apple's XNU tree, and
# tools/xnu-patch.sh puts that on the XNU tree before every kernel build (docker/build.sh, and the CI).
#
# Run this again after the patch changed (tools/mkpatch.sh writes it from a tree that was changed): the
# tree keeps the patch it carries (kernel/.git/hakios-patch/). The same patch is left alone; another one
# takes the old one off the tree first -- which fails, and says so, if the tree was changed since by hand
# (tools/mkpatch.sh is for that: it writes the tree's changes into the patch) -- and goes on in its place.
# xnu-src, xnu-deps and the rest of what was built stay.
#
# After this: ./build.sh
set -e
HERE=$(cd "$(dirname "$0")" && pwd -P)
DIR="$HERE/kernel"
URL="${HAKIOS_UPSTREAM:-https://github.com/oosexgang/hakios-xnu-kernel.git}"
REV=63fc2b4633bac3af8e57dadab9f5a8d4cb940438
PATCH="$HERE/patches/kernel-common.patch"
MARK="$DIR/.git/hakios-patch"       # what the tree carries: "patch", a copy of the patch as it was put on
export LC_ALL=C

[ -f "$PATCH" ] || { echo "apply-kernel-patches.sh: no patches/kernel-common.patch beside this script" >&2; exit 1; }

if [ ! -d "$DIR/.git" ]; then
	echo "== the upstream at $REV (one commit, no history)"
	rm -rf "$DIR"
	git init -q "$DIR"
	(cd "$DIR" &&
		git remote add origin "$URL" &&
		git fetch -q --depth 1 origin "$REV" &&
		git checkout -q FETCH_HEAD)
fi

if [ -f "$MARK/patch" ] && cmp -s "$MARK/patch" "$PATCH"; then
	echo "== the patch (patches/kernel-common.patch: in the tree already)"
else
	if [ -f "$MARK/patch" ]; then
		echo "== the tree carries another patches/kernel-common.patch: that one off, this one on"
		(cd "$DIR" && git apply -R --whitespace=nowarn "$MARK/patch") ||
			{ echo "apply-kernel-patches.sh: the tree was changed since that patch was put on it, by hand:" >&2
			  echo "  tools/mkpatch.sh writes such changes into patches/kernel-common.patch (and then there is nothing to do)" >&2
			  exit 1; }
	fi
	echo "== the patch (patches/kernel-common.patch)"
	(cd "$DIR" && git apply --whitespace=nowarn "$PATCH") ||
		{ echo "apply-kernel-patches.sh: patches/kernel-common.patch does not apply to the tree at $REV" >&2; exit 1; }
	rm -rf "$MARK"
	mkdir -p "$MARK"
	cp "$PATCH" "$MARK/patch"
fi

echo "== this clone linked in as devices/dodge"
rm -rf "$DIR/devices/dodge"
ln -s "$HERE" "$DIR/devices/dodge"

echo "== the ath12k stack into the tree's wifi/"
rm -rf "$DIR/wifi/ath12k"
cp -r "$HERE/wifi/ath12k" "$DIR/wifi/"

echo "== done"
echo "   next: ./build.sh"
