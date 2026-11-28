#!/bin/sh
# data-boot.sh: boot iOS with the data volume -- /private/var kept from boot to boot.
#
#   ./data-boot.sh [SECS]          SECS: the session's seconds (default: 900)
#   env CMDLINE="-v" STAY=1 NAME   as for the launcher (launcher/hackios.py); NAME: another volume
#
# The phone in Android with USB debugging and root, or in its bootloader or the loader (it is taken back to
# Android first). The map of the volume's blocks is read from Android as the phone leaves it -- a pin is not
# a guarantee -- and the loader checks that the volume is where the map says before iOS is started on it; if
# it is not, nothing is written and this says so. When the session ends the phone is back in Android
# (STAY=1: it stays in the loader).
#
# Without a volume (./data-create.sh makes one) this stops at once and says so.
exec python3 "$(dirname "$0")/tools/hakios-data.py" boot "$@"
