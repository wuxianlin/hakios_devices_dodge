#!/usr/bin/env python3
"""hakios_adblink.py: the adb link to the phone on a virtual machine -- what made it drop under load, and the cure.

A phone passed through to a VMware guest hangs on the guest's virtual USB 2.0 controller (ehci-pci), and
that controller's DMA goes through the kernel's bounce buffers (swiotlb). A bounce buffer is at most
IO_TLB_SEGSIZE slots (kernel/dma/swiotlb.c: swiotlb_find_slots returns -1 above that, however empty the pool
is, and the warning still says "full"): 128 slots of 2 KiB, 256 KiB. A USB transfer of more than that is never
mapped on such a guest; the kernel logs

    ehci-pci 0000:02:03.0: swiotlb buffer is full (sz: 455021 bytes), total 32768 (slots), used 19 (slots)

and about half a second later the phone is disconnected. adb's packets go up to a megabyte (MAX_PAYLOAD), and
its libusb backend, the default on Linux, hands each to the controller whole: under a sustained transfer the
link dropped every ten or twenty seconds, and a 2.4 GB image never arrived. The native backend
(ADB_LIBUSB=0) writes a packet whole once and, when that is refused, again in 16 KiB pieces
(client/usb_linux.cpp, usb_write) -- so files pushed with it go through whole (three of 1 GiB at 32 MB/s,
measured on one such guest, Ubuntu with kernel 7.0, where with libusb one of 1 GiB failed after three seconds);
a read has no such second try. What is left of the fault, an image streamed over the standard input of "adb
shell" with a slow receiver behind it, tools/hakios-send.py avoids: an image file is pushed as a file.

So the tools look for that line in the kernel's log, and when it is there the adb server is started anew with
ADB_LIBUSB=0 -- a process of the computer's, closed and opened: nothing is reset on the phone, which stays
attached the whole time. On a computer whose kernel never said it, nothing is done; HAKIOS_KEEP_ADB_SERVER=1
forbids the restart (the advice is printed instead).
"""
import os
import subprocess
import sys
import time

SYMPTOM = b'swiotlb buffer is full'
NATIVE = b'ADB_LIBUSB=0'


def kernel_says_so():
    """has this computer's kernel logged the fault since it booted? False where its log cannot be read"""
    if not sys.platform.startswith('linux'):
        return False
    try:
        r = subprocess.run(['journalctl', '-k', '-q', '--no-pager'], capture_output=True, timeout=60)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return SYMPTOM in r.stdout


def server_pid():
    """the pid of the adb server that is running (adb ... fork-server server ...), or None when none is"""
    try:
        pids = os.listdir('/proc')
    except OSError:
        return None
    for pid in pids:
        if not pid.isdigit():
            continue
        try:
            with open(f'/proc/{pid}/cmdline', 'rb') as f:
                args = f.read().split(b'\0')
        except OSError:
            continue
        if b'fork-server' in args and b'server' in args:
            return pid
    return None


def server_native(pid=None):
    """was the adb server started with ADB_LIBUSB=0? None when there is none, or it cannot be told"""
    pid = pid or server_pid()
    if pid is None:
        return None
    try:
        with open(f'/proc/{pid}/environ', 'rb') as f:
            env = f.read().split(b'\0')
    except OSError:
        return None
    return NATIVE in env


def start_native(adb):
    """the adb server anew with its native backend: the one running is stopped first (nothing else of the
    computer's is touched). True when a server is running with ADB_LIBUSB=0 afterwards"""
    pid = server_pid()
    if pid is not None:
        try:
            subprocess.run([adb, 'kill-server'], capture_output=True, timeout=60)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        for _ in range(15):
            if server_pid() != pid:
                break
            time.sleep(0.2)
    env = dict(os.environ, ADB_LIBUSB='0')
    try:
        subprocess.run([adb, 'start-server'], capture_output=True, timeout=120, env=env)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    pid = server_pid()
    if pid is not None and server_native(pid) is False:
        return False                     # a server came up, but not as asked (a wrapper, an old adb): said by the caller
    # the phone was never detached; the new server finds it in a moment -- it is given ten, as after an attach
    time.sleep(10)
    return True


class Link:
    """the link as this process finds it: asked once, cured once"""

    def __init__(self, adb='adb', say=None):
        self.adb = adb
        self.say = say or (lambda what: None)
        self.looked = None                # has the kernel logged the fault?
        self.cured = False
        self.keep = os.environ.get('HAKIOS_KEEP_ADB_SERVER', '').strip() == '1'

    def at_start(self):
        """before the first byte goes to the phone: the server made native when this is a computer where the
        link drops. True when a cure was wanted and is done; None when there was nothing to do"""
        self.looked = kernel_says_so()
        if not self.looked:
            return None
        native = server_native()
        if native:
            return None
        return self._cure()

    def after_a_drop(self):
        """a piece did not go through: when the kernel has just said why, and the server is not native, it is
        made so now -- in the same run, so that the image arrives. True when cured"""
        if self.cured or self.keep:
            return False
        if self.looked is None:
            self.looked = kernel_says_so()
        if not self.looked or server_native():
            return False
        return self._cure()

    def _cure(self):
        why = ('this computer\'s kernel has said "swiotlb buffer is full" for its USB controller: there the adb link '
               'drops under load with adb\'s libusb backend (a VMware guest\'s virtual EHCI)')
        if self.keep:
            self.say(f'hakios: {why}; HAKIOS_KEEP_ADB_SERVER=1 keeps the adb server as it is -- an adb server '
                     f'started with ADB_LIBUSB=0 does not drop there')
            return False
        self.say(f'hakios: {why}: the adb server is started anew with ADB_LIBUSB=0 (the phone is not touched)')
        if not start_native(self.adb):
            self.say('hakios: the adb server did not come up with ADB_LIBUSB=0: it is as it was')
            return False
        self.cured = True
        return True
