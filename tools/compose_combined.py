#!/usr/bin/env python3
"""Compose the combined staged image (amss20 firmware + 6 native files) and
update the loader's file table (xnuloader/op13_wifi_files.h) with this phone's
native file hashes. Run after pull-firmware.sh has refreshed fw-wifi/:

    python3 devices/dodge/tools/compose_combined.py [TREE-ROOT]
"""
import os

import sys
ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else \
    os.path.expanduser("~/workspace/hakios/hakios-xnu-kernel")
PEACH = ROOT + "/fw-wifi/peach"
HDR = ROOT + "/xnuloader/op13_wifi_files.h"
OUT = ROOT + "/fw-wifi/combined20.bin"

def fnv(b):
    h = 0xcbf29ce484222325
    for x in b:
        h ^= x; h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h

files = [
    (0x800000, "tmel_peach_20.elf"),
    (0x80e000, "regdb.bin"),
    (0x814000, "bdwlan.elf"),
    (0x833000, "phy_ucode20.elf"),
    (0x87a000, "aux_ucode20.elf"),
    (0x889000, "qdss_trace_config_v2.cfg"),
]

fw = open(PEACH + "/amss20.bin", "rb").read()
total = 0x88a861
img = bytearray(total)
img[0:len(fw)] = fw

entries = []
for off, name in files:
    data = open(f"{PEACH}/{name}", "rb").read()
    if off + len(data) > total:
        raise SystemExit(f"{name} overflows image")
    img[off:off+len(data)] = data
    entries.append((off, len(data), fnv(data), name))
    print(f"{name:28s} off={off:#x} size={len(data):#x} fnv={fnv(data):#x}")

# sanity: firmware untouched
assert fnv(img[0:len(fw)]) == fnv(fw)
open(OUT, "wb").write(bytes(img))
print(f"\nwrote {OUT} ({len(img)} bytes)")

# rewrite the header table with this phone's files
lines = ["/* Local file metadata only. Preferred Android firmware remains private. */",
         "struct op13_wifi_file {uint32_t offset,size;uint64_t fnv;};",
         "static const struct op13_wifi_file op13_wifi_files[]={"]
for off, size, h, name in entries:
    lines.append(f" {{0x{off:x},0x{size:x},0x{h:x}ULL}}, /* {name} */")
lines.append("};")
open(HDR, "w").write("\n".join(lines) + "\n")
print(f"rewrote {HDR}")
