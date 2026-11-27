#!/bin/bash
# tools/xnu-patch.sh [TREE]: this repository's patch on Apple's XNU tree, over the common tree's files as they
# are now.
#
#   TREE   the common tree (default: this repository's kernel/; the CI gives its own)
#
# The XNU tree (xnu-src/xnu-8796.121.2) is Apple's release with the common tree's work on it, as xnu/apply.sh
# left it: xnu/hakios.patch and xnu/hfs/hfs.patch on Apple's files, the common tree's own files (xnu/files/)
# and its drivers copied in. This puts those files and drivers in again -- a change in the common tree's patch
# may have changed them since the tree was made -- and then patches/xnu.patch: this repository's changes to
# Apple's files there (HFS Plus's with them). docker/build.sh and the CI run it before every kernel build; a
# tree that carries the patch already is left alone.
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd -P)
TREE=$(cd "${1:-$HERE/kernel}" && pwd -P)
X="$TREE/xnu-src/xnu-8796.121.2"
P="$HERE/patches/xnu.patch"
export LC_ALL=C

[ -d "$X/.git" ] || { echo "xnu-patch.sh: no XNU tree at $X (xnu/apply.sh makes it)" >&2; exit 1; }
[ -f "$P" ] || { echo "xnu-patch.sh: no patches/xnu.patch beside this repository's root" >&2; exit 1; }

# the common tree's own files and drivers: as xnu/apply.sh put them there once, and as every kernel build puts
# the drivers there (xnu/sync-drivers.sh). Nothing of patches/xnu.patch's is among them: each file is one
# repository's to change (tools/mkpatch.sh)
cp -a "$TREE/xnu/files/." "$X/"
(cd "$TREE" && XNU_SRC="$X" xnu/sync-drivers.sh "$X")

if git -C "$X" apply --check --whitespace=nowarn "$P" 2>/dev/null; then
	git -C "$X" apply --whitespace=nowarn "$P"
	echo "xnu-patch: patches/xnu.patch on the XNU tree ($(git apply --numstat "$P" | wc -l | tr -d ' ') of Apple's files)"
elif git -C "$X" apply --check -R --whitespace=nowarn "$P" 2>/dev/null; then
	echo "xnu-patch: patches/xnu.patch is in the XNU tree already"
else
	echo "xnu-patch.sh: patches/xnu.patch neither applies to the XNU tree nor is in it: the tree was patched" >&2
	echo "             with another version of it. Remove kernel/xnu-src/xnu-8796.121.2 and build again:" >&2
	echo "             xnu/apply.sh makes the tree anew, and the kernel is built again from its sources" >&2
	exit 1
fi
