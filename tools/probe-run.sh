#!/bin/bash
# probe-run: while a kernel runs, ask the loader every 3 s whether it is alive (getvar product),
# grab a screenshot when it stops answering, and note the times. The kernel's own life is the
# screen: with -v it prints, and with sb_delay=8 SpringBoard appears if it gets that far.
#   tools/probe-run.sh [DIR]
cd "$(dirname "$0")/.." || exit 1
dir=${1:-logs/probe}
mkdir -p "$dir"
t0=$(date +%s)
alive=1
n=0
while :; do
	t=$(( $(date +%s) - t0 ))
	if ! fastboot devices 2>/dev/null | grep -q fastboot; then
		echo "$t s: loader gone from fastboot"
		break
	fi
	r=$(FB_TIMEOUT=6 fastboot getvar product 2>&1 | head -1)
	if echo "$r" | grep -q xnuloader; then
		echo "$t s: loader answers"
	else
		echo "$t s: loader NOT answering: $r"
		if [ "$alive" = 1 ]; then
			alive=0
			FB_TIMEOUT=10 fastboot oem screenshot >/dev/null 2>&1
			FB_TIMEOUT=15 fastboot get_staged "$dir/at-death.raw" >/dev/null 2>&1
			echo "$t s: screenshot taken"
		fi
	fi
	n=$((n + 1))
	[ $n -gt 80 ] && break
	sleep 3
done
echo "probe-run done: $dir"
