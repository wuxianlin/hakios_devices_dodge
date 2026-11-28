#!/bin/sh
# data-delete.sh: remove the data volume -- everything iOS kept on it goes; Android has its space again.
#
#   ./data-delete.sh [-y]          -y: no question asked
#   env NAME                       another volume than "data"
#
# The phone in Android with USB debugging and root. Asks first, by default: what is on the volume (the
# music, the photos, all of iOS's own data) cannot be brought back.
exec python3 "$(dirname "$0")/tools/hakios-data.py" delete "$@"
