# HakiOS for the OnePlus 13 (dodge)

A [HakiOS](https://github.com/oosexgang/hakios-xnu-kernel) device repository: everything the
OnePlus 13 (dodge, Qualcomm SM8750 "sun", Snapdragon 8 Elite) needs to boot and run unmodified
iOS 16.5 — its own kernel drivers, its SoC family's layer, its UEFI platform, its firmware, and
two patches against the common repository. Nothing is flashed: the system boots over `fastboot
boot` and lives in RAM -- and what iOS keeps can live on the phone's own storage, in a file of
Android's (the data volume, below).

Built against the common repository at `main` (pinned revision in `apply-kernel-patches.sh`),
which this repository does not modify beyond what `patches/` carries -- one patch per repository:
`patches/kernel-common.patch` for the common tree, `patches/xnu.patch` for Apple's XNU tree.
`tools/hakios-device.py sync` in the common tree clones this into `devices/dodge/`.

## Status

Seen working on the phone, November 2026 (one line per area, the same words as `device.conf`):

| Area | State | Notes |
|---|---|---|
| Display | **Works** | The loader's framebuffer; the panel's own brightness over DSI (`/dev/brightness`, DCS51, the user-space agent) |
| Touch | **Works** | Synaptics S3910 on bit-banged GPIO SPI, TouchCom: multitouch, edge gestures, 125 Hz (`synaptics=1`) |
| Buttons | **Works** | Power from the PON, volume from PM8550VS GPIO6 through the SPMI write side (`buttons_pin=0x18d buttons_swap=1`) |
| Audio | **Works** | The audio DSP adopted, the AudioReach graph, both AW88230 amplifiers, true stereo; iOS's software volume (`adsp=1 sound=4`) |
| Wi-Fi | **Works** | The WCN7850 brought up in the loader over MHI, the lease adopted by the kernel, a network joined over DHCP (`wifi=1`). Either revision of the chip an OnePlus 13 carries, peach or peach_v2: the loader tells them apart by the chip's own second aperture before anything is downloaded, and downloads that revision's firmware out of the one image the host stages. Seen on a phone of each revision (2026-11-28: the network joined on both, traffic on the first) |
| Bluetooth | **Works** | Over PCIe: scan, pairing, A2DP (`bt=4 pcie_bt_en=1`) |
| Battery | **Works** | Level, health and charging state through the audio DSP's GLINK (`batt_dsp=4`) |
| GPU | **Works** | Adreno 830: hardware GL through Mesa, SpringBoard and the frosted wallpaper (`adreno8=23 gl=1`). The table of IOSurface textures in the GL library no longer runs out (the black rectangles after some use); the kernel driver and Mesa carry the fixes of the REDMI K90 port and of ios-dodge that this port had not taken; a vibrant colour matrix is laid over what is behind it (the App Library's category boxes were a flat dark grey) |
| Alert slider | **Works** | The three positions through the PMIC's accelerometer pole (`slider=1`) |
| Torch | **Works** | The flashlight from the camera driver's rails (`torch=3`) |
| CPU | Partial | Four cores by default (the proven boot); all eight have been booted (`cpus=8`) |
| Sensors | Partial | The alert slider; no others yet |
| Storage | **Partial** | The phone's storage through the loader's block service (`ufs=2`): every partition a read-only block device, and the data volume kept in Android's `/data/hakios` as iOS's writable `/private/var` (`hakios_data=1`; [docs/data-persistence.md](docs/data-persistence.md)): a file pinned and reserved, its image written to its blocks of the raw partition in plaintext, served only where a map with a guard for every extent says it is. Files' **On My iPhone** is on that volume and kept from boot to boot (HFS Plus answers the sync-root fsctls FileProvider asks of a volume, as the memory filesystem does); without the volume it lives there and is lost at power-off (the local storage provider's executable in the image, fileproviderd given the volume). No UFS driver of the kernel's own here yet |
| Camera | Partial | The video half works: the Iris VPU decodes H.264/HEVC (PAS 9, HFI Gen2, the VideoToolbox plugin); the still camera itself (the ISP pipeline, the sensors) is not brought up |
| Cellular | Missing | The modem is not started; `soc.args` carries the IPA addresses for when it is |
| Haptic | **Works** | The PMIH010x hv-haptics block over SPMI: iOS Actuator waveforms played directly (`haptic=4`), this phone's motor (7692 us, 2400 mV) |
| Thermal, network | Missing | Not started |

## How it is put together

| What | Where |
|---|---|
| The common tree: the rewrites of its drivers (the audio DSP, the Wi-Fi core, the input hub, the battery, the torch, the storage, the stubs' guards), the loader's bring-up (the WCN7850's MHI -- both revisions of the chip: the second aperture sized once BAR 0 is placed tells them apart before anything is downloaded -- the brightness protocol votes, the platform table), its block service with the volumes it maps in Android's userdata, the user space (the brightness agent, bluetool, the flashlight, the VideoToolbox plugin), the build tools, and its own files for Apple's tree (`xnu/files/`) | `patches/kernel-common.patch` |
| Apple's XNU tree: the fixes the phone's kernel needs (the 128 MB IO submap, the NULL processors skipped, the 4096-byte boot line), and HFS Plus answering FileProvider's sync-root fsctls (Files' On My iPhone on the data volume) | `patches/xnu.patch` |
| The drivers only this phone has — the Synaptics touch, the DSI brightness, the alert slider, the AudioReach graph builder, the DSP battery, Bluetooth over PCIe, the MHI lease adoption, the Adreno 830 and its GPU service, the Iris video decode — under upstream-style names, found by the tree's own sync ([docs/porting/drivers.md](https://github.com/oosexgang/hakios-xnu-kernel/blob/main/docs/porting/drivers.md)) | `drivers/` |
| The ath12k station stack, from ios-dodge's port with thanks | `wifi/`; built against [FreeBSD](https://www.freebsd.org)'s LinuxKPI, pulled from FreeBSD's own archive |
| [Mesa](https://www.mesa3d.org) 26.2.5 with Freedreno: ios-dodge's consolidated patch, with only the GPU service's name adapted at build time, and on top of it the K90 port's fixes this port had not taken (the A830 without early preambles and without the fragment texture prefetch, a linear import whose last rows are padded, no shadow of imported buffers, zeroed descriptors for unbound texture and UBO slots, the framebuffer read bound to layer 0); Mesa's tarball from [Mesa's own archive](https://archive.mesa3d.org) | `mesa-files/` |
| The SoC family's layer (the SM8750's addresses) | `soc/` |
| UEFI for the phone: a port of [Mu-Silicium](https://github.com/Project-Silicium/Mu-Silicium) | `uefi/` |
| The phone's firmware, so the CI's package works out of the box; refreshed off the phone by `pull-firmware.sh`, hash-guarded (the ODM board file and regulatory database are never overwritten by the stock ones) | `fw-audio/`, `fw-bt/`, `fw-wifi/`, `fw-gpu/`, `fw-video/` |
| The WCN7850's firmware, a set for each revision an OnePlus 13 carries (`peach/`, `peach_v2/`), and `combined.bin`: both sets in the one image the host stages, made by `tools/compose_combined.py`, which holds the loader's table of them to what it composed | `fw-wifi/` |
| Tools: the firmware composer, the board-file matcher, the IPA installer, the slider calibrator, the data volume's | `tools/` |
| The data volume — iOS keeping `/private/var` in Android's `/data` from boot to boot: the loader's mapped volume and the kernel's driver (in the patches), the phone-side tool, the host tools, and five scripts: create, boot with and delete it, and put the system image on the phone | `tools/hakios-pin/`, `tools/hakios-data.py`; `data-create.sh`, `data-boot.sh`, `data-delete.sh`, `system-create.sh`, `system-delete.sh`; [docs/data-persistence.md](docs/data-persistence.md) |

The boot is one complete set: every run boots the resident system image with `root.apfs` as its
RAM disk. The CI's package runs with one command (`launcher/run.sh`), the WCN7850's loader
bring-up included.

## The data volume (keeping what iOS makes)

By default iOS starts with an empty `/private/var` every time. The data volume is a disk of iOS's own
that lives in Android's `/data/hakios`: a file, pinned and its size reserved, whose blocks carry an
HFS Plus volume — written to the phone's raw storage in plaintext, because Android's `userdata` is
encrypted below its file system on this phone and nothing written through Android could be read
back. The loader gives the volume to the kernel as a block device and `init` mounts it on
`/private/var`, so apps' data, settings and Files' On My iPhone stay from boot to boot.

```sh
./data-create.sh [SIZE]      # once: 64G by default. The phone in Android with root; Linux with mkfs.hfsplus (hfsprogs)
./data-boot.sh [SECS]        # every boot with the data kept (900 s by default)
./data-delete.sh             # its space back for Android: everything on the volume goes
./system-create.sh           # once per build: this build's system image on the phone, the same way
./system-delete.sh           # it off again: data-boot.sh uploads the image as before
```

Each boot reads where Android keeps the volume's blocks right before leaving Android, and the
loader refuses the volume unless every extent is still there — Android's own data is never written
over, and nothing is flashed. The CI zip carries the same five scripts and tools. The design, what
was measured on a phone, and what was not: [docs/data-persistence.md](docs/data-persistence.md).

## Building

```sh
./apply-kernel-patches.sh          # the pristine upstream + our patch + this repository's layers
docker/build.sh                    # the kernel inside the build container (loader too: docker/build.sh loader)
IPSW=<iOS 16.5's ipsw> ./build.sh  # the images, the data volume's phone-side tool, the bootable package; IPSW= only the first time
```

The CI ([.github/workflows/build.yml](.github/workflows/build.yml)) does the same from a bare
runner — the pristine clone, the two patches, the same checks the tree gives (`hakios-device.py
check`, `drivers-check.py`, `boot-args.py --check`), the loader's firmware table against the
firmware (`compose_combined.py --check`), the data volume's phone-side tool, the kernel, the
loader, UEFI, the system image, and one zip in the artifact.

The patches are changed in the build trees and written afresh from them: `tools/mkpatch.sh` (the
common tree; `--xnu` for Apple's), then `./apply-kernel-patches.sh` says whether the tree is as its
patch says, and the patch is committed. The IPSW is yours (Apple's licence); firmware comes from
your phone.

## Running

From the common repository's top: `./xl enter`, `./xl ramdisk`, `./xl run`; or the one-command
`run-linux.sh` / `run-windows.bat` here; or the CI zip's `launcher/run.sh` with only
`platform-tools` beside it. `fastboot boot` only — nothing is flashed.

## Acknowledgements

- [HakiOS](https://github.com/oosexgang/hakios-xnu-kernel) -- the common repository this port is built on
- [ios-dodge](https://github.com/faridbtw/ios-dodge) -- the Mesa GLES patch, the ath12k stack, the MHI lease model
- [hackios-redmi-k90-annibale](https://github.com/iola1999/hackios-redmi-k90-annibale) -- the same-SoC sibling port (SM8750)
- [Project Silicium](https://github.com/Project-Silicium/Mu-Silicium) -- the UEFI implementation
- [Mesa](https://www.mesa3d.org) and [FreeBSD](https://www.freebsd.org) -- the GL and the LinuxKPI
