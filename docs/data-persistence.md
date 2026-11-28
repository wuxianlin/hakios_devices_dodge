# The data volume: iOS keeping `/private/var` in Android's `/data` from boot to boot

> Status (2026-11-28): **works, and was measured on a phone** (§7: a PJZ110 of the firmware
> `PJZ110_16.0.10.501(CN01)`, Android 16, with root). iOS mounts `/private/var` on an HFS Plus volume that lives in
> the blocks of a file of Android's own, writes what it likes there, and finds it at the next boot -- Files' *On My
> iPhone* with it. The system image goes on the phone the same way, and a boot then reads it from the phone in a
> second, where uploading its 2.4 GB over USB took a minute.
>
> The design is the newer port's: its zip for this phone carries it, and its own `docs/data-persistence.md` measures
> it on two phones of this model (2026-11-24 and 2026-11-25). The loader's and the kernel's parts of it are in this
> tree as that port wrote them; the phone-side tool and the sender are its, word for word; the front end and the
> launcher carry what belongs to other features left out, and one fault of its own mended (§6). What was measured of
> all that here is in §7, and what was not in §8.

## 0. In short

1. Without the volume iOS keeps nothing: the system runs from RAM, and `/private/var` is a memory filesystem -- empty
   at every boot.
2. Nothing written through Android's file system can be read back from outside it on this phone. `userdata` is
   encrypted **below** its file system (metadata encryption), so every byte Android writes is ciphertext on the
   flash; an image pushed into `/data` is no image to the loader.
3. What works is to let f2fs hold blocks, and nothing more. A file is made in `/data/hakios`, pinned (f2fs keeps a
   pinned file's blocks where they are) and given its size in advance; the image is written onto **those blocks of
   the raw partition**, in plaintext, by the host. The loader serves them as a block device, the kernel's driver
   gives it a name, and `init` mounts it on `/private/var`.
4. Writes stay in the volume's own blocks by four gates (§3): only that one entry of the loader's block service takes
   a write; the map of the blocks is taken afresh from Android before every boot; the first block holds a marker;
   and the first and the last block of every extent hold what their guard says -- or the loader serves nothing of it.
5. Five scripts: `data-create.sh`, `data-boot.sh`, `data-delete.sh`, `system-create.sh`, `system-delete.sh`.
6. A boot without the volume (`run.sh`, the CI's zip) is as it was: no `hakios_data=1`, no volume published,
   `/private/var` the memory filesystem.

## 1. The storage of this phone, as found here

Read-only, on the phone of §7 (`adb shell`, with root; and the loader's `oem ufs-survey`, `oem ufs-read`):

| | |
|---|---|
| `ro.crypto.metadata.enabled`, `ro.crypto.type` | `true`, `file` |
| `/data` | f2fs on `/dev/block/dm-78`: a device-mapper device, not the partition; Android's kernel 6.6.118 |
| the fstab's `/data` line | `metadata_encryption=aes-256-xts:wrappedkey_v0`, `fileencryption=aes-256-xts:aes-256-cts:v2+inlinecrypt_optimized+wrappedkey_v0`, `inlinecrypt` |
| `userdata` | `/dev/block/sda15`, LBA 4117956..249145338 of 249145344: 934.7 GiB of a 950.4 GiB unit |
| blocks | 4096 bytes, in the six logical units; every GPT's header and table CRC right |
| the partition as the loader reads it | byte for byte what Android's kernel reads of the raw device, and no f2fs super block: at 1024, where its magic would be, ciphertext |

Two consequences, both the newer port's and both seen again here:

- an image that Android writes is nobody else's to read (and the keys are wrapped: the kernel never holds them in
  the clear);
- a write that goes around the file system, to the raw partition, lands in plaintext -- which the loader and iOS
  read, and Android never does.

**Where the file is made matters.** `hakios-pin` refuses a file whose extents carry any FIEMAP flag but *last* and
*merged*: shared, unwritten, encrypted or encoded. On this phone a file made under `/data/adb` (the device-encrypted
storage, where Magisk keeps its own) or `/data/local/tmp` is a file of an encrypted directory: FIEMAP marks its
extents `DATA_ENCRYPTED | ENCODED` (`0x88`), and the tool will have nothing to do with it -- so no volume can be made
there. A directory of the top level of `/data` carries no encryption policy (measured with a probe file in
`/data/hakios`: last and merged only), and that is where the volumes are: `/data/hakios/NAME`.

## 2. The way of it

```
the computer                          Android (root)                         the loader (UEFI, kept up)        XNU / iOS
data-create.sh [SIZE]
  hakios-pin create / pin / alloc  -->  /data/hakios/data: empty, pinned, SIZE reserved
  hakios-pin zeroblocks            -->  zeros onto its blocks of the raw partition (each piece read back)
  an empty HFS Plus volume (mkfs.hfsplus, on the computer or in the build container)
  hakios-send.py --raw             -->  the chunks that hold something, pushed, summed on the phone,
                                        put onto those blocks by hakios-pin; each piece read back
  hakios-pin map / guard / mark    -->  extents + guards + marker  -->  data.map (for the record)

data-boot.sh [SECS]
  hakios-pin map / guard           -->  the map as Android holds it now, a moment before the phone leaves
  adb reboot bootloader; fastboot boot boot.img  -->                    the loader up
  oem hakios-map + a download                                            parsed; checked against userdata as UEFI lists it;
                                                                         the marker and every guard read again
  oem ramdisk + the upload of system.apfs  (oem hakios-resident instead: the image is on the phone)
  oem run (ufs=2 hakios_data=1 var_hfs=ufs_hakios_data)               -->  hakios_data published, the one entry
                                                                              that takes writes   -->  /dev/ufs_hakios_data
                                                                                                     init mounts it on /private/var
```

What each layer has:

| Layer | What | Where |
|---|---|---|
| the loader | an entry whose blocks are not together (`BLOCK_PART_EXTENTS`); the map taken over USB and checked; the marker and the guards; the commands of §6; its log 256 KiB (at 32, the survey alone filled it) | `xnuloader/loader.c`, in `patches/kernel-common.patch` |
| the kernel's driver | `CONFIG_HAKIOS_UFS=y`; with `hakios_data=1` the entry the loader offers is the data volume: `/dev/ufs_hakios_data`, writable from its first block; every other partition a block device that refuses writes; what the driver says goes to the loader's log too, and the run's account is said at the end | `drivers/storage/hakios.ufs.c`, `hakios.ufshc.c`, `configs/dodge.config` |
| HFS Plus | FileProvider's sync-root fsctls, which APFS answers and HFS did not: the mark is an attribute of the directory (§7, 15) | `bsd/hfs/hfs_readwrite.c`, in `patches/xnu.patch` |
| user space | nothing: `var_hfs=` is `init`'s own (`mount_data_volume()`) | -- |
| the phone side | `hakios-pin`: a static ARM64 program with the kernel's ABI and nothing from libc | `tools/hakios-pin/` |
| the computer side | `hakios-data.py` (the front end), `hakios-send.py`, `hakios-volread.py`, `hakios_adblink.py`, `hakios-hfsprogs.sh`; the launcher's `map`, `DATA=`, `SYS=` | `tools/`, `launcher/hackios.py` |

## 3. Only its own blocks are written

A pin is not a guarantee. f2fs's background GC leaves a pinned file's blocks alone, but a foreground GC, with the
storage nearly full, moves them a section at a time -- the first block may stay while others move. So the volume is
served only where a map says it is, and a map is believed only when the blocks still hold what they held when it was
taken:

| Gate | What it does | Against |
|---|---|---|
| 1 | of the loader's block service, the mapped volume is the only entry a write reaches; the kernel's other devices answer `EROFS` | a write anywhere else |
| 2 | `data-boot.sh` takes the map from Android each time, and only then leaves Android | a map that is out of date |
| 3 | 16 bytes at offset 2048 of the first block (past the HFS Plus volume header, where nothing of the file system's ever comes): a map that names a marker is served only if the block holds it | taking other blocks for the volume |
| 4 | the FNV-1a 64 of the first and of the last block of every extent, read again before anything is served; one that differs and the whole volume is not served, not even read | blocks moved between the map and Android stopping |

The map itself is checked as it comes: extents in the order of the volume, each after the last, none empty or
overlapping or outside `userdata`; a `guard` line only for an extent the map has. On the phone side, `hakios-pin`
pins only an empty file; checks every extent against `FIBMAP` as well as `FIEMAP`, and that no extent lies below
f2fs's `main_blkaddr`; writes nothing to a file that has lost blocks; and reads every piece back and compares it.

## 4. The volume

HFS Plus, as the port's own data volume is made (`tools/mkdatavol.py` of the common tree): case-sensitive, journaled,
blocks of 4096, `root:root` and 0755 at its root -- with another owner Darwin takes it for a volume to migrate, and
stays at the progress bar. `mkfs.hfsplus` makes it on the computer (Linux's `hfsprogs`), or in the build container,
where `tools/hakios-hfsprogs.sh` builds it from the sources the first time it is wanted.

## 5. Use

The phone in Android, USB debugging on, with root for `adb shell su`; `adb` and `fastboot` in `PATH`. Linux, or
anything else that has `mkfs.hfsplus` for `create`.

| | | |
|---|---|---|
| `./data-create.sh [SIZE]` | make the volume: `64G` by default, `1G` and `512M` also; minutes | once |
| `./data-boot.sh [SECS]` | boot iOS with it; the phone is taken back to Android first, wherever it was | every boot |
| `./data-delete.sh [-y]` | unpinned and deleted: everything iOS kept on it goes | when its space is wanted |
| `./system-create.sh [SYSTEM.APFS]` | this build's system image on the phone, the same way | once per build |
| `./system-delete.sh [-y]` | it off again: `data-boot.sh` uploads as before | |
| `python3 tools/hakios-data.py list` | the volumes there are, and whether Android still holds each | |
| `python3 tools/hakios-data.py look [--ls /PATH &#124; --cat /PATH --sha256 &#124; --fsck]` | what a volume holds, read from the raw storage without iOS | |

`NAME=` is the volume's name in `/data/hakios` (`data` by default). For `boot`, `CMDLINE=` and `STAY=` are the
launcher's, and `NOWIFI=1` starts without the Wi-Fi bring-up; `HAKIOS_NOSYS=1` uploads the system image even when the
phone has one.

The same five scripts and tools are in the zip the CI makes, beside `run.sh`.

**The file is not to be written, truncated or deleted through Android** once it is made -- by a file manager with
root, say: f2fs would hand its blocks out again. A factory reset takes the volume; an OTA update does not. The
image is in plaintext on the flash, unlike Android's own data: whoever can read the raw blocks (root, an unlocked
bootloader, this loader) can read what iOS kept. Every boot starts from Android: the newer port found that a second
kernel in one loader session dies at once (not tried here), which is a limit of the loader and not of the volume.

## 6. The map, and what the loader says of it

The map is text, a line an item (`hakios-data.py`, `fresh_map()`):

```
extent 0 214528 2048          the volume's 2048 blocks from its block 0 on, at userdata's block 214528
guard 0 0x0123456789abcdef-fedcba9876543210      the FNV-1a 64 of the extent's first block, then of its last
marker 0x26defb9db66976a692026f1cdd200521        the 16 bytes at offset 2048 of the first block
readonly                     the system image's map only: no marker, and never a write
bytes 2506993664             the system image's map only: the image's bytes (its blocks are rounded up)
```

| Asked | Answered |
|---|---|
| `fastboot oem hakios-map`, then `fastboot stage MAP` | `OKAY`, or `FAIL map refused` (`oem log` says why) |
| `fastboot getvar hakios-map` | `75 extents,262144 blocks`, or `none` |
| `fastboot oem hakios-info` | looks again; in the log (`oem log`) |
| `fastboot getvar hakios-volume` | `whole: 75 extents where the map says`; or `moved: extent 0 is not what the map says`, `its first block does not hold the marker`, `no map` |
| `fastboot oem hakios-sysmap`, `getvar hakios-sysmap`, `getvar hakios-system` | the system image's |
| `fastboot oem hakios-resident` | the system image from the phone into the RAM disk's place |
| `fastboot oem ufs-survey`, `oem ufs-read NAME LBA BLOCKS` | read only: the units and their GPTs; 1 to 16 blocks of a partition, or of a mapped volume by its name |

The system image is known for what it is by its SHA-256: `system.map` keeps the sum of the image as `system-create.sh`
found it beside the scripts, and `data-boot.sh` compares it with the `system.apfs` it has at every boot -- not the
same image, and it is uploaded as before.

What is not the newer port's here: `hakios-data.py` takes no `--music`/`--video` (its player's) and does not choose
the WCN7850's image by the chip's revision (this repository's launcher stages the one image that holds both);
`hakios-volread.py`'s dates were 35 years late (it took HFS Plus's count to start in 2001, not 1904); and where the
build container has no `mkfs.hfsplus` yet, `create` has it built there, as `hakios-hfsprogs.sh`'s own comment
promised; and `tools/hakios-pin/build.sh` ended with success for a source clang had refused, the commands
running in a shell of their own where its `set -e` did not reach, and the program of the build before left standing
to be shipped. The launcher's handing of `ANDROID_SERIAL` to fastboot is written so as not to call itself.

## 7. Measured here

One phone, 2026-11-28: PJZ110, `PJZ110_16.0.10.501(CN01)`, Android 16 (kernel 6.6.118), Magisk for root, `/data`
with 890 GiB free. `t1` is a volume of 1G made for the tests and taken out after them.

| # | What | Result |
|---|---|---|
| 1 | the loader's survey (`oem ufs-survey`) | 6 units, 139 partitions, every GPT CRC right; `userdata` LBA 4117956..249145338 |
| 2 | `oem ufs-read userdata 0 16` beside `dd` of the raw device in Android | 65536 bytes, the same |
| 3 | `hakios-pin` on a pinned file of another day: `map` with f2fs's `main_blkaddr` | 8 extents, each agreed by `FIBMAP`; `FIRST` set above them, refused |
| 4 | a file under `/data/adb`: `map` | `an extent with flags 0x1088`: refused, and nothing written |
| 5 | `NAME=t1 ./data-create.sh 1G` | 16 s: 262144 blocks pinned, zeroed and read back; 256 MiB of the HFS Plus volume put on in 2 chunks; 75 extents |
| 6 | `oem hakios-map` with `t1.map`, `oem hakios-info` | `whole, where the map says: the marker in its first block, every extent at its guard` |
| 7 | three maps that were not the volume's: a guard changed; the marker changed; two extents swapped | `moved: extent 0 is not what the map says` (the log: its first block sums to 58bf1646… there and to 58ff1646… in the map); `its first block does not hold the marker`; `map refused`, `no map` |
| 8 | boot 1 on `t1`, 180 s (the launcher's `DATA=`) | `/private/var: HFS Plus from /dev/ufs_hakios_data, kept between boots`; 464 reads (2.4 MB), 19725 writes (574 MB), 0 errors, 0 refused, 0 failed; `/dev/ufs_*` for the 139 partitions |
| 9 | the volume read from the raw blocks in Android (`look`) | 373 MiB in use, 6851 folders, 3065 files; the root is an iPhone's `/private/var` (33 entries); `fsck.hfsplus -n`: *The volume Data appears to be OK* |
| 10 | boot 2, 180 s, the map taken afresh (`remap`: 75 extents, none moved) | the same volume; 3910 reads (86 MB), 5089 writes (141 MB), 0 errors; fsck again OK |
| 11 | every file of the volume after boot 1 against after boot 2 (two images of it, hashed) | of 3016 regular files: 2261 the same bytes (75%), 486 rewritten by iOS (16%), 269 gone (9%: 118 `.tmp`, PosterBoard's caches), 205 new |
| 12 | `NAME=t1 ./data-boot.sh 150` as it ships, from the root of this clone | the map taken, the image uploaded (60 s), the volume whole, iOS 150 s: 4424 reads (97 MB), 3599 writes (106 MB), 0 errors; the account printed at the end |
| 13 | `./system-create.sh` | 2 min: 615576 blocks (2405 MiB) of `system.apfs` onto the phone, the SHA-256 of the raw blocks the image's own (`5a3e9e53…`); 46 extents |
| 14 | `NAME=t1 ./data-boot.sh 120` with the image on the phone | *the system image read from the phone's storage in 1 s*; no upload; iOS 120 s on the volume: 4481 reads, 2852 writes, 0 errors |
| 15 | Files' *On My iPhone* on the volume | boot 5: a test tool put `hello-from-hakios.txt` (65 bytes) into the domain's storage at boot, and the file is in the volume ever after (`look --ls`). Boot 6, nothing put in, the kernel as it was: *Folder is Empty*, 47, 77 and 108 s after the extension came up -- HFS answered none of FileProvider's sync-root fsctls. Boot 7, HFS answering them (`patches/xnu.patch`): *hello-from-hakios, 10:04 AM, 65 bytes, 1 item* |
| 16 | the zip's own layout: `NAME=t1 ./data-boot.sh 120` from the folder `./build.sh` makes (everything beside the scripts, `hakios-pin` out of its `tools/`) | the map taken, the image uploaded (61 s: the folder has no `system.map`), the volume whole; 5009 reads (114 MB), 3715 writes (104 MB), 0 errors |
| 17 | the same package booted as it ships, with the default jobs and no volume | the GPU up, the network joined 9.4 s after the kernel began on the radio, the home screen whole at 100 s and at 170 s, 1400 frames at 4329 us, 0 GL errors; the driver said nothing. Once more as `./build.sh` last made it (its loader and boot image made anew): the home screen whole at 150 s, 1000 frames at 5461 us, 0 GL errors |

Eight boots on the volume in all (7 through this clone, 1 from the package). No fault, no SMMU fault, no GPU
timeout, 0 GL errors in any run. The kernel as
built for this (`CONFIG_HAKIOS_UFS=y`) ran a boot without `ufs=` on its command line first: as before, and the
driver said nothing.

## 8. What is not, and the risks

| | |
|---|---|
| a foreground GC moving blocks | seen by the guards: nothing is served, iOS starts with an empty `/private/var`, `data-boot.sh` says so before the run |
| the plaintext | said in §5 |
| speed | every read and write goes through the loader, on one core, by UEFI's driver: 574 MB written in 180 s here without trouble; a large copy will be slow |
| what was not tried here | a foreground GC moving blocks for real; a factory reset or an OTA; a volume of the default 64G; power lost in the middle of a write (the journal's to answer for that); a second kernel in one loader session; a phone of another firmware |
| the kernel's own UFS driver | `ufs_native=1` knows nothing of a mapped entry: reads and writes of the volume are the loader's |
| leftover | the newer port's own tests left three pinned files in `/data/hakios` of this phone (`t1.bin`, `t2.bin`, `t3`, 96 MiB in all, markers of their own): not this repository's to take out |
