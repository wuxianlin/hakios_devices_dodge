#!/bin/bash
# watch-live: while a kernel runs, fetch the loader's live console every 2 s (the kernel's
# early-boot output goes to the loader's console hook -- the normal return's log starts at
# init, so the EARLY boot (the driver hub, the DSP steps) is only visible here, live).
#   tools/watch-live.sh [DIR]     snapshots DIR/<n>-live.log
cd "$(dirname "$0")/.." || exit 1
dir=${1:-logs/live}
mkdir -p "$dir"
n=0
while :; do
	if ! fastboot devices 2>/dev/null | grep -q fastboot; then
		break
	fi
	if FB_TIMEOUT=8 fastboot oem log 2>&1 | grep -q OKAY; then
		FB_TIMEOUT=15 fastboot get_staged "$dir/$n-live.log" >/dev/null 2>&1
		[ -s "$dir/$n-live.log" ] && { tr -d '\0' < "$dir/$n-live.log" > "$dir/$n-live.txt"; rm -f "$dir/$n-live.log"; n=$((n + 1)); }
	fi
	sleep 2
done
echo "watch-live: $n snapshots in $dir"
