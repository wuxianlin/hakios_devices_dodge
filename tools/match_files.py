#!/usr/bin/env python3
import os, sys

def fnv(b):
    h = 0xcbf29ce484222325
    for x in b:
        h ^= x
        h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h

table = [
    (0x800000, 0xd7a4, 0xa8ce60f4c4473602, "tmel_peach_20.elf"),
    (0x80e000, 0x5ed6, 0x29ea702baa663f4b, "regdb.bin"),
    (0x814000, 0x1e990, 0x018b507fa89db11, "bdwlan.elf"),
    (0x833000, 0x47000, 0xf0ad29b147a1e7b1, "phy_ucode20.elf"),
    (0x87a000, 0x0f000, 0x8a9a6485c402114d, "aux_ucode20.elf"),
    (0x889000, 0x1861, 0xd51e00b6b794c0de, "qdss_trace_config_v2.cfg"),
]

import shutil
args = [a for a in sys.argv[1:] if not a.startswith("-")]
apply_to = None
if "--apply" in sys.argv:
    apply_to = sys.argv[sys.argv.index("--apply") + 1]
d = args[0] if args else os.path.expanduser("~/workspace/hakios/hakios-xnu-kernel/fw-wifi/kiwi")
found = {}
for name in sorted(os.listdir(d)):
    p = os.path.join(d, name)
    if not os.path.isfile(p) or name.startswith("bdwlan.e") and not name.endswith((".elf", ".bin")):
        continue
    sz = os.path.getsize(p)
    h = fnv(open(p, "rb").read())
    for off, size, want, label in table:
        if h == want and sz == size:
            found[label] = p
            print(f"MATCH {label:28s} <- {name} (size {sz:#x})")
            if apply_to:
                os.makedirs(apply_to, exist_ok=True)
                shutil.copy2(p, os.path.join(apply_to, label))
    # also report near-size candidates for missing entries

print("\nmissing:")
for off, size, want, label in table:
    if label not in found:
        print(f"  {label:28s} size {size:#x}")
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if os.path.isfile(p) and os.path.getsize(p) == size:
                print(f"    size-candidate: {name} (fnv {fnv(open(p,'rb').read()):#x})")
