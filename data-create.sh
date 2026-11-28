#!/bin/sh
# data-create.sh: make HakiOS's data volume -- iOS's own disk, kept in Android's /data.
#
#   ./data-create.sh [SIZE]        SIZE: 64G (the default), 1G, 512M ... a whole number of MiB
#   env NAME                       the volume's name in Android's /data/hakios (default: data)
#
# With it, iOS keeps /private/var -- music, photos, everything of its own -- from boot to boot
# (docs/data-persistence.md). Without it, iOS starts with an empty /private/var every time.
#
# The phone in Android, USB debugging on, with root for "adb shell su" (Magisk and the like); adb in PATH.
# Linux with mkfs.hfsplus ("sudo apt install hfsprogs" -- it makes the volume's HFS Plus file system); in a
# built repository the build container's serves instead. What it does, in Android's /data/hakios/NAME: a
# file, pinned and its size reserved -- an empty HFS Plus volume written onto its blocks of the raw storage,
# in plaintext, where the loader and iOS read and write it. Nothing else of Android's is touched; the space
# is Android's again after ./data-delete.sh, or a factory reset. Takes minutes (the first writing of the
# volume is most of it).
#
# Then: ./data-boot.sh
exec python3 "$(dirname "$0")/tools/hakios-data.py" create "$@"
