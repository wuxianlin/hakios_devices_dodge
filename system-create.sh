#!/bin/sh
# system-create.sh: put this build's system image on the phone, in Android's /data -- so that a boot does not
# upload it.
#
#   ./system-create.sh [SYSTEM.APKFS]   default: the build's system.apfs (kernel/userland/ in a built repository,
#                                       beside this script in a release zip)
#   env NAME                             no effect here: the image's file is always "system"
#
# The image is written the way the data volume is (docs/data-persistence.md): a file in Android's /data/hakios,
# pinned and its size reserved, the image on its blocks of the raw storage in plaintext, every piece read back
# and compared, the whole of it checked by its SHA-256 at the end. From then on ./data-boot.sh has the loader
# read it from the phone's own storage in seconds, where uploading 2.4 GB over USB took a minute -- as long as
# it is the image of the kernel being booted: data-boot.sh compares the SHA-256 of system.apfs beside the scripts
# with the one on the phone at every boot, and uploads when they differ.
#
# The phone in Android, USB debugging on, with root for "adb shell su". Once per build: ./system-delete.sh
# takes it off. Nothing else of Android's is touched; a factory reset takes the image too.
exec python3 "$(dirname "$0")/tools/hakios-data.py" system-create "$@"
