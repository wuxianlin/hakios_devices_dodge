#!/bin/bash
# Make ../../../Mu-Silicium: a clone of Project-Silicium/Mu-Silicium at the commit our port was
# made on (upstream already carries the dodgePkg platform), with this port put into it: a patch
# for the files upstream has, one for inside the Mu_Basecore submodule, and the XnuLoader sources.
# Built by devices/dodge/docker/build.sh uefi (the CI does the same steps).
set -e
# usage: uefi/apply.sh [TREE]
#   TREE   the common tree Mu-Silicium is cloned into. The default is the one this directory lies three levels
#          below (devices/dodge/uefi), as the CI has it; where the device directory is a link to this
#          repository (apply-kernel-patches.sh), the tree is not above this directory, and it is given.
HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd -P)
cd "$HERE"
MU=../../../Mu-Silicium
[ -z "$1" ] || MU="$(cd "$1" && pwd -P)/Mu-Silicium"
if [ -d "$MU/.git" ] && [ -f "$MU/Silicon/Silicium/SiliciumPkg/Library/BootManagerLib/XnuLoader.c" ]; then
	echo "uefi/apply.sh: $MU already at the base with the port applied"
	exit 0
fi
[ -e $MU ] && { echo "uefi/apply.sh: $MU exists but is not a clone with the port; remove it"; exit 1; }
git clone https://github.com/Project-Silicium/Mu-Silicium.git $MU
git -C $MU checkout -q "$(cat BASE)"
git -C $MU submodule update --init --recursive
git -C $MU apply --whitespace=nowarn "$PWD/mu-silicium.patch"
git -C $MU/Mu_Basecore apply --whitespace=nowarn "$PWD/mu-basecore.patch"
cp -r files/. $MU/
echo "uefi/apply.sh: done; next: the build itself (devices/dodge/docker/build.sh uefi, as the CI does it)"
