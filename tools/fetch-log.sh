#!/bin/bash
# fetch-log: the loader's whole log in 16 KB parts (the loader's "oem log-part"): the one-shot
# upload of a long log has been seen to stop at 0x7fff bytes, a 16 KB part always comes whole.
#   tools/fetch-log.sh [FILE]     default logs/<time>-fetched.log
cd "$(dirname "$0")/.." || exit 1
out=${1:-logs/$(date +%H%M%S)-fetched.log}
mkdir -p logs
: > "$out"
part=0
while :; do
	FB_TIMEOUT=15 fastboot oem log-part $part 2>&1 | grep -q OKAY || break
	FB_TIMEOUT=30 fastboot get_staged "$out.part" >/dev/null 2>&1
	[ -s "$out.part" ] || break
	tr -d '\0' < "$out.part" >> "$out"
	rm -f "$out.part"
	part=$((part + 1))
done
rm -f "$out.part"
echo "fetch-log: $out: $(wc -c < "$out") bytes in $part parts"
