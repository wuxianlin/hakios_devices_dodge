#!/bin/sh
# system-delete.sh: take the system image off the phone -- Android has its 2.4 GB again; ./data-boot.sh
# uploads the system image from the computer at every boot again, as it did before system-create.sh.
#
#   ./system-delete.sh [-y]              -y: no question asked
#
# The phone in Android with USB debugging and root. What is on the data volume (music, photos) is not touched.
exec python3 "$(dirname "$0")/tools/hakios-data.py" system-delete "$@"
