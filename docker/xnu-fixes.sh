#!/bin/bash
# Five fixes the phone's kernel needs, applied after xnu/apply.sh:
#   1. The IO submap: 128 MB (this phone's driver stack needs more than 32 MB)
#   2. host.c: skip NULL processors (the boot CPU is 5, only four online)
#   3. loader_return.c: the watchdog pet must write through loader_log_putc --
#      printf from the thread-call context never reaches the log and can block
#      there; a pet that blocks stops feeding, and ~100 s later the hypervisor
#      resets the phone with no panic -- the "silent freeze at 182 s" rounds
#   4. boot.h: the boot line must be 4096 -- with the stock 608, the loader's
#      own arguments pushed sound= and slider= past the end; they never arrived
#   5. loader_return.c: the log ring is 8 MB -- a 1 MB ring wrapped during the
#      launch storm and the printing path, interrupts off, spun per character
#      until every CPU froze with its console dead mid-word
set -e
XNU=${1:-xnu-src/xnu-8796.121.2}

[ -f "$XNU/osfmk/arm/io_map.c" ] || { echo "fixes: no $XNU/osfmk/arm/io_map.c" >&2; exit 1; }

# fix 1: the IO submap
sed -i "s|#define IO_MAP_SIZE.*|#define IO_MAP_SIZE            (128ul << 20)|" \
	"$XNU/osfmk/arm/io_map.c"

# fix 2: host.c NULL processor skip
sed -i "s|assert(processor != PROCESSOR_NULL);|if (processor == PROCESSOR_NULL) { continue; }|" \
	"$XNU/osfmk/kern/host.c"

# fix 4: the boot line must hold the loader's own arguments plus the phone's
# whole stack -- with the stock 608, sound= and slider= never arrived
sed -i "s|#define BOOT_LINE_LENGTH.*608.*|#define BOOT_LINE_LENGTH        4096  /* hakios: the loader prepends its own arguments; 608 cut sound= and slider= off */|" \
	"$XNU/pexpert/pexpert/arm64/boot.h"

# fix 3: the watchdog pet writes through the loader's ring, never printf
python3 - "$XNU/osfmk/arm64/loader_return.c" <<'PYEOF'
import sys

path = sys.argv[1]
s = open(path).read()

old_printf = '''\t/* every pet says so: a round that dies of the ~100 s layout killer shows
\t * exactly when the feeding stopped (the last line before the silence) */
\tprintf("wdt: pet %llu\\n", (unsigned long long)loader_live.heartbeat);'''

new_putc = '''\t{
\t\t/* printf from this thread-call context never reaches the log (memory_report's
\t\t * line has never once appeared either); the loader's own ring does -- the
\t\t * buttons driver's way. A frozen round's last pet line says when feeding
\t\t * stopped, the layout killer's first hard evidence */
\t\textern void loader_log_putc(char);
\t\tchar line[32], *c = line;

\t\tsnprintf(line, sizeof(line), "wdt: pet %llu\\n", (unsigned long long)loader_live.heartbeat);
\t\twhile (*c) {
\t\t\tloader_log_putc(*c++);
\t\t}
\t}'''

if "loader_log_putc(*c++)" in s:
    print("fixes: the wdt pet already writes the loader ring")
elif old_printf in s:
    s = s.replace(old_printf, new_putc)
    open(path, "w").write(s)
    print("fixes: the wdt pet now writes the loader ring")
else:
    print("fixes: wdt pet pattern not found -- check loader_return.c", file=sys.stderr)
    sys.exit(1)

# 5. the log ring: 8 MB, not 1 MB -- the launch storm (the desktop coming up,
#    ~180 s in) wrapped the 1 MB ring and the printing path contended with
#    interrupts off, every CPU spinning per character: the frozen rounds, their
#    consoles dead mid-word. 8 MB keeps a whole round.
p = path                    # the same loader_return.c as fix 3
s = open(p).read()
if '#define LOADER_LOG_SIZE (8448 * 1024)' in s:
    print('fixes: the log ring already 8 MB')
else:
    s = s.replace('#define LOADER_LOG_SIZE (1536 * 1024)',
                  '#define LOADER_LOG_SIZE (8448 * 1024)')
    s = s.replace('#define LOADER_LOG_RING (1024 * 1024)',
                  '#define LOADER_LOG_RING (8192 * 1024)')
    open(p, 'w').write(s)
    print('fixes: the log ring is now 8 MB')
PYEOF

echo "fixes: IO submap 128 MB, host.c NULL processors skipped, boot line 4096, wdt pet through the loader ring, log ring 8 MB"
