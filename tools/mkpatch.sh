#!/bin/bash
# tools/mkpatch.sh: this repository's patches, written afresh from the trees as they are.
#
#   ./tools/mkpatch.sh [FILE ...]   patches/kernel-common.patch: the common tree (kernel/, as
#                                   apply-kernel-patches.sh made it) against the upstream's revision, with
#                                   whatever was changed in it since. A file the change made is named once.
#   ./tools/mkpatch.sh --xnu        patches/xnu.patch: Apple's XNU tree (kernel/xnu-src/xnu-8796.121.2)
#                                   against a copy of it as xnu/apply.sh leaves it -- made beside it, from
#                                   the tree's own clone of XNU and its own copy of the HFS sources, so
#                                   nothing is fetched.
#
# One patch per repository, and each file is one repository's:
#   the common tree's patch (kernel-common.patch) carries everything this port changes in the common
#     repository: the rewrites of its drivers, the loader, user space, the tools, and its own files for
#     Apple's tree (xnu/files/), which xnu/apply.sh copies into the XNU tree and docker/build.sh puts there
#     again at every build;
#   Apple's tree has its own (xnu.patch): Apple's files there, HFS Plus's with them, as xnu/apply.sh leaves
#     them (xnu/hakios.patch and xnu/hfs/hfs.patch put on). Nothing that the common tree owns is changed
#     through it, and xnu/hakios.patch, xnu/hfs/hfs.patch are not changed at all: xnu/apply.sh puts them on
#     Apple's files once, when it makes the tree.
#
# A change is made in a tree (kernel/, where the builds run), tried, and then written into its patch here;
# afterwards ./apply-kernel-patches.sh says of the common tree, and docker/build.sh of Apple's, whether it is
# as the patch says. Then the patch is committed.
#
# The files the common tree's patch makes are known to it (its "create mode" lines) and are taken in again; a
# new one is given as an argument. Nothing else that the upstream does not track enters the patch: a build's
# output cannot.
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd -P)
TREE="$HERE/kernel"
XNU=xnu-src/xnu-8796.121.2
PATCHES="$HERE/patches"
export LC_ALL=C

created() {	# the files a patch makes
	git apply --summary "$1" 2>/dev/null | sed -n 's/^ create mode [0-9]* //p' | sed 's/^"//; s/"$//'
}

touched() {	# the files a patch changes
	git apply --numstat "$1" | awk '{print $3}' | sed 's/^"//; s/"$//'
}

say_what() {	# a patch, in one line
	echo "$(git apply --numstat "$1" | wc -l | tr -d ' ') files, +$(git apply --numstat "$1" | awk '$1 != "-"{a += $1} END{print a + 0}') -$(git apply --numstat "$1" | awk '$2 != "-"{d += $2} END{print d + 0}')"
}

if [ ! -d "$TREE/.git" ]; then
	echo "mkpatch.sh: no tree at $TREE -- run ./apply-kernel-patches.sh first" >&2
	exit 1
fi

if [ "${1:-}" = "--xnu" ]; then
	X="$TREE/$XNU"
	PATCH="$PATCHES/xnu.patch"
	[ -d "$X/.git" ] || { echo "mkpatch.sh: no $XNU in the tree (docker/build.sh makes it)" >&2; exit 1; }
	[ -d "$TREE/xnu-src/hfs-627.100.6/core" ] || { echo "mkpatch.sh: no xnu-src/hfs-627.100.6 beside it (xnu/apply.sh fetched it)" >&2; exit 1; }
	command -v rsync >/dev/null || { echo "mkpatch.sh: rsync is not installed" >&2; exit 1; }
	P=$(mktemp -d "${TMPDIR:-/tmp}/hakios-xnu-pristine.XXXXXX")
	trap 'rm -rf "$P"' EXIT
	(cd "$TREE" && XNU_SRC="$P/$XNU" XNU_URL="file://$X" HFS_SRC="$TREE/xnu-src/hfs-627.100.6" xnu/apply.sh) >/dev/null
	(cd "$P/$XNU" && git add -A && git -c user.name=dodge -c user.email=dodge@local commit -q -m "as xnu/apply.sh leaves it")
	# the tree as it is, over it -- not what the build makes again for itself: the two files hakios-config.py writes
	rsync -a --delete --exclude=.git --exclude=BUILD --exclude=config/MASTER.hakios --exclude=bsd/dev/hakios.drivers.h \
		"$X/" "$P/$XNU/"
	(cd "$P/$XNU" && git add -A && git diff --binary HEAD) > "$PATCH.new"
	if [ ! -s "$PATCH.new" ]; then
		echo "mkpatch.sh: nothing in $XNU that xnu/apply.sh did not put there -- patches/xnu.patch left as it is" >&2
		rm -f "$PATCH.new"
		exit 1
	fi
	# each file is one repository's: what xnu/apply.sh brings from the common tree is the common tree's to
	# change (a difference here is the XNU tree standing behind the common tree: docker/build.sh brings it
	# up to date)
	drivers=$(cd "$TREE" && python3 tools/hakios-config.py external-files | cut -f2)
	own=""
	while IFS= read -r f; do
		if [ -f "$TREE/xnu/files/$f" ]; then
			own="$own   $f (the common tree's: xnu/files/)"$'\n'
		elif printf '%s\n' "$drivers" | grep -qxF "$f"; then
			own="$own   $f (the common tree's: a driver)"$'\n'
		fi
	done <<EOF2
$(touched "$PATCH.new")
EOF2
	if [ -n "$own" ]; then
		echo "mkpatch.sh: the XNU tree differs from the common tree in what the common tree owns -- not written:" >&2
		printf '%s' "$own" >&2
		echo "   a change to such a file is made in the common tree (patches/kernel-common.patch); docker/build.sh" >&2
		echo "   puts the common tree's files into the XNU tree again" >&2
		rm -f "$PATCH.new"
		exit 1
	fi
	# the patch has to describe the tree: taken back off it, it has to fit exactly
	git -C "$X" apply --check -R --whitespace=nowarn "$PATCH.new" ||
		{ echo "mkpatch.sh: the patch does not describe $XNU as it is -- not written" >&2; rm -f "$PATCH.new"; exit 1; }
	mv "$PATCH.new" "$PATCH"
	echo "wrote patches/xnu.patch: $(say_what "$PATCH")"
else
	PATCH="$PATCHES/kernel-common.patch"
	[ -f "$PATCH" ] || { echo "mkpatch.sh: no patches/kernel-common.patch to grow from" >&2; exit 1; }
	cd "$TREE"
	for f in "$@"; do
		[ -e "$f" ] || { echo "mkpatch.sh: no $f in the tree" >&2; exit 1; }
		git add -N -f -- "$f"
	done
	for f in $(created "$PATCH"); do
		[ -e "$f" ] && git add -N -f -- "$f"
	done
	git diff --binary HEAD > "$PATCH.new"
	git reset -q
	# the patch has to describe the tree: taken back off it, it has to fit exactly
	git apply --check -R --whitespace=nowarn "$PATCH.new" ||
		{ echo "mkpatch.sh: the patch does not describe the tree as it is -- not written" >&2; rm -f "$PATCH.new"; exit 1; }
	# what xnu/apply.sh puts on Apple's files once, when it makes the XNU tree, is not this patch's to change
	own=$(touched "$PATCH.new" | grep -E '^xnu/(hakios|hfs/hfs)\.patch$' || true)
	if [ -n "$own" ]; then
		echo "mkpatch.sh: the tree changes $own: Apple's files are changed through patches/xnu.patch (the XNU tree's own) -- not written" >&2
		rm -f "$PATCH.new"
		exit 1
	fi
	mv "$PATCH.new" "$PATCH"
	# the tree carries this patch now: what apply-kernel-patches.sh reads
	rm -rf .git/hakios-patch && mkdir -p .git/hakios-patch && cp "$PATCH" .git/hakios-patch/patch
	echo "wrote patches/kernel-common.patch: $(say_what "$PATCH")"
fi
