# HakiOS for the OnePlus 13 (dodge)

A [HakiOS](https://github.com/oosexgang/hakios-xnu-kernel) device repository: everything the
OnePlus 13 (dodge, Qualcomm SM8750 "sun", Snapdragon 8 Elite) needs to boot and run unmodified
iOS 16.5 — its own kernel drivers, its SoC family's layer, its UEFI platform, its firmware, and
two patches against the common repository. Nothing is flashed: the system boots over `fastboot
boot` and lives in RAM.

Built against the common repository at `main` (pinned revision in `apply-kernel-patches.sh`),
which this repository does not modify beyond what `patches/` carries. `tools/hakios-device.py
sync` in the common tree clones this into `devices/dodge/`.

## Status

Seen working on the phone, November 2026 (one line per area, the same words as `device.conf`):

| Area | State | Notes |
|---|---|---|
| Display | **Works** | The loader's framebuffer; the panel's own brightness over DSI (`/dev/brightness`, DCS51, the user-space agent) |
| Touch | **Works** | Synaptics S3910 on bit-banged GPIO SPI, TouchCom: multitouch, edge gestures, 125 Hz (`synaptics=1`) |
| Buttons | **Works** | Power from the PON, volume from PM8550VS GPIO6 through the SPMI write side (`buttons_pin=0x18d buttons_swap=1`) |
| Audio | **Works** | The audio DSP adopted, the AudioReach graph, both AW88230 amplifiers, true stereo; iOS's software volume (`adsp=1 sound=4`) |
| Wi-Fi | **Works** | The WCN7850 brought up in the loader over MHI, the lease adopted by the kernel, a network joined over DHCP (`wifi=1`) |
| Bluetooth | **Works** | Over PCIe: scan, pairing, A2DP (`bt=4 pcie_bt_en=1`) |
| Battery | **Works** | Level, health and charging state through the audio DSP's GLINK (`batt_dsp=4`) |
| GPU | **Works** | Adreno 830: hardware GL through Mesa, SpringBoard and the frosted wallpaper (`adreno8=23 gl=1`) |
| Alert slider | **Works** | The three positions through the PMIC's accelerometer pole (`slider=1`) |
| Torch | **Works** | The flashlight from the camera driver's rails (`torch=3`) |
| CPU | Partial | Four cores by default (the proven boot); all eight have been booted (`cpus=8`) |
| Sensors | Partial | The alert slider; no others yet |
| Storage | Missing | UFS not brought up; the system runs from RAM |
| Camera | Partial | The video half works: the Iris VPU decodes H.264/HEVC (PAS 9, HFI Gen2, the VideoToolbox plugin); the still camera itself (the ISP pipeline, the sensors) is not brought up |
| Cellular | Missing | The modem is not started; `soc.args` carries the IPA addresses for when it is |
| Haptic | **Works** | The PMIH010x hv-haptics block over SPMI: iOS Actuator waveforms played directly (`haptic=4`), this phone's motor (7692 us, 2400 mV) |
| Thermal, network | Missing | Not started |

## How it is put together

| What | Where |
|---|---|
| The common tree's rewrites (the audio DSP, the Wi-Fi core, the input hub, the battery, the torch, the stubs' guards, the XNU overlay files) | `patches/xnu-dodge.patch` |
| The loader's bring-up (the WCN7850's MHI, the brightness protocol votes, the platform table), the user space (the brightness agent, bluetool, the flashlight, the VideoToolbox plugin) and the build tools | `patches/kernel-common.patch` |
| The drivers only this phone has — the Synaptics touch, the DSI brightness, the alert slider, the AudioReach graph builder, the DSP battery, Bluetooth over PCIe, the MHI lease adoption, the Adreno 830 and its GPU service, the Iris video decode — under upstream-style names, found by the tree's own sync ([docs/porting/drivers.md](https://github.com/oosexgang/hakios-xnu-kernel/blob/main/docs/porting/drivers.md)) | `drivers/` |
| The ath12k station stack, from ios-dodge's port with thanks | `wifi/`; built against [FreeBSD](https://www.freebsd.org)'s LinuxKPI, pulled from FreeBSD's own archive |
| [Mesa](https://www.mesa3d.org) 26.2.5 with Freedreno: ios-dodge's consolidated patch, with only the GPU service's name adapted at build time; Mesa's tarball from [Mesa's own archive](https://archive.mesa3d.org) | `mesa-files/` |
| The SoC family's layer (the SM8750's addresses) | `soc/` |
| UEFI for the phone: a port of [Mu-Silicium](https://github.com/Project-Silicium/Mu-Silicium) | `uefi/` |
| The phone's firmware, so the CI's package works out of the box; refreshed off the phone by `pull-firmware.sh`, hash-guarded (the ODM board file and regulatory database are never overwritten by the stock ones) | `fw-audio/`, `fw-bt/`, `fw-wifi/`, `fw-gpu/`, `fw-video/` |
| Tools: the firmware composer, the board-file matcher, the IPA installer, the slider calibrator | `tools/` |

The boot is one complete set: every run boots the resident system image with `root.apfs` as its
RAM disk. The CI's package runs with one command (`launcher/run.sh`), the WCN7850's loader
bring-up included.

## Building

```sh
./apply-kernel-patches.sh          # the pristine upstream + the two patches + this repository's layers
docker/build.sh                    # the kernel inside the build container (loader too: docker/build.sh loader)
IPSW=<iOS 16.5's ipsw> ./build.sh  # the images + the bootable package; IPSW= only the first time
```

The CI ([.github/workflows/build.yml](.github/workflows/build.yml)) does the same from a bare
runner — the pristine clone, the two patches, the same checks the tree gives (`hakios-device.py
check`, `drivers-check.py`, `boot-args.py --check`), the kernel, the loader, UEFI, the system
image, and one zip in the artifact. The IPSW is yours (Apple's licence); firmware comes from
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
