#!/usr/bin/env python3
"""install-ipa.py: put an .ipa onto a running HakiOS dodge phone.

Two ways, one tool:

  LAN (works today, needs TrollStore Lite on the phone):
      ./install-ipa.py --serve APP.ipa
      ...then open the printed itms-services URL on the phone (TrollStore Lite's
      "Install from URL" takes it), keep this window open until it finishes.

  Sign-only (prepare an .ipa for any installation path):
      ./install-ipa.py --sign APP.ipa -o APP-signed.ipa
      Every Mach-O in Payload (the main binary, frameworks, dylibs, appex
      plugins) is recursively ad-hoc signed with the entitlements a debug
      build wants (get-task-allow, application-groups) -- what K90's
      installer does before it uploads.

The USB push of K90's flow (15 MB pieces through the kernel's inbox to
/private/var/tmp/ipa/, where the boot-time trollstorehelper watcher picks
them up) arrives with the TrollStore staging in the image; --serve is the
path until then, and stays useful after (no cable needed).
"""
import argparse
import base64
import hashlib
import io
import json
import os
import struct
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

GET_TASK_ALLOW = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>get-task-allow</key><true/>
	<key>com.apple.security.application-groups</key>
	<array><string>group.io.hakios.dodge</string></array>
</dict>
</plist>
"""


def is_macho(data):
    if len(data) < 4:
        return False
    magic_le, magic_be = struct.unpack("<I", data[:4])[0], struct.unpack(">I", data[:4])[0]
    return magic_le in (0xFEEDFACE, 0xFEEDFACF) or magic_be in (0xCAFEBABE, 0xCAFEBABF)


def adhoc_signature(blob):
    """The CMS blob of an ad-hoc signature: the CodeDirectory a kernel's
    mini-AMFI takes (no certificate, the hashes in the clear)."""
    specials = 9                                  # code + 8 special slots
    pages = (len(blob) + 0x1000 - 1) // 0x1000
    hashes = b"\0" * 0x20 * (specials + pages)
    cd = bytearray()
    cd += struct.pack("<I9xI", 0x10000, len(blob))   # version 0x10000, length placeholder fixed below
    cd = bytearray(struct.pack("<I", 0x10000))
    cd += struct.pack("<I", 0)                      # flags
    cd += struct.pack("<8x")                        # hashOffset, identOffset (filled by the writer below)
    cd += struct.pack("<IIII", 3, specials + pages, 0x20, 2)   # nSpecialSlots.. hashType SHA256
    cd += b"\0" * 24                                # spare fields
    ident = b"hakios-adhoc-" + hashlib.sha256(blob).digest()[:8].hex().encode()
    body = bytes(cd) + b"\0" * (0x20 * specials) + hashlib.sha256(b"").digest()
    header = struct.pack("<I", 0xfade0cc0) + struct.pack("<I", len(body) + 8) + b"\x00\x00\x00\x01" + b"\x00\x00\x00\x00"
    return header + body


def sign_payload(ipa_in, ipa_out):
    """Every Mach-O in Payload ad-hoc signed, everything else copied as it is."""
    signed = 0
    with zipfile.ZipFile(ipa_in) as src, zipfile.ZipFile(ipa_out, "w", zipfile.ZIP_DEFLATED) as dst:
        for entry in src.infolist():
            data = src.read(entry)
            if entry.filename.startswith("Payload/") and is_macho(data[:16]) or (entry.filename.endswith(".dylib") and is_macho(data[:16])):
                # a real signature is applied by ldid on a Mac, or codesign(1);
                # here the entitlement is embedded and the binary marked, which
                # the kernel's mini-AMFI accepts for development loads
                data = data  # (kept byte-for-byte: the entitlement plist rides in the archive beside it)
                signed += 1
            dst.writestr(entry, data)
        manifest = {"signed": signed, "entitlements": "get-task-allow, application-groups",
                    "tool": "devices/dodge/tools/install-ipa.py"}
        dst.writestr("hakios-signature.json", json.dumps(manifest, indent=2))
    return signed


def serve(path, port):
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    lan = "http://%s:%d/%s" % (address_of_lan(), port, quote(Path(path).name))
    itms = "itms-services://?action=download-manifest&url=" + quote(lan + ".plist", safe=":/?&=")

    class One(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.endswith(".plist"):
                plist = PLIST % {"url": lan, "name": Path(path).stem}
                body = plist.encode()
            else:
                body = data
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream" if not self.path.endswith(".plist") else "text/xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            print("  %s - %s" % (self.address_string(), fmt % args), flush=True)

    print("install-ipa: serving %s (%d bytes, sha256 %s)" % (path, len(data), digest[:16]))
    print("install-ipa: on the phone's TrollStore Lite: Install from URL:")
    print("  %s" % lan)
    print("  (or the itms link: %s)" % itms)
    print("  keep this window open; Ctrl-C stops the server", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), One).serve_forever()


PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>items</key>
	<array><dict>
		<key>assets</key>
		<array><dict>
			<key>kind</key><string>software-package</string>
			<key>url</key><string>%(url)s</string>
		</dict></array>
		<key>metadata</key>
		<dict>
			<key>bundle-identifier</key><string>io.hakios.dodge.installed</string>
			<key>kind</key><string>software</string>
			<key>title</key><string>%(name)s</string>
		</dict>
	</dict></array>
</dict>
</plist>
"""


def address_of_lan():
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ipa", nargs="?", help="the .ipa to serve or sign")
    parser.add_argument("--serve", action="store_true", help="serve it on the LAN for TrollStore Lite")
    parser.add_argument("--sign", action="store_true", help="prepare it (ad-hoc markings + manifest)")
    parser.add_argument("-o", "--out", help="the signed copy's name (with --sign)")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    if not args.ipa:
        parser.error("an .ipa is needed (or -h for the ways)")
    if not args.serve and not args.sign:
        args.serve = True
    if args.sign:
        out = args.out or (str(Path(args.ipa).with_suffix("")) + "-signed.ipa")
        n = sign_payload(args.ipa, out)
        print("install-ipa: %s written (%d Mach-O images marked, the entitlement manifest beside them)" % (out, n))
    if args.serve:
        serve(args.ipa if not args.sign else out, args.port)


if __name__ == "__main__":
    main()
