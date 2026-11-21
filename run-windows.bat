@echo off
REM devices/dodge/run-windows.bat: boot the OnePlus 13 into HakiOS from Windows (RAM boot).
REM Needs adb.exe and fastboot.exe on PATH, and the xl tooling under WSL or a win32 build.
REM The cycle matches run-linux.sh; the Wi-Fi bring-up runs through the same loader.

set SECS=420
if not "%1"=="" set SECS=%1

echo == 1. the phone recovers in Android
adb wait-for-device
timeout /t 75 /nobreak >nul

echo == 2. the bootloader and the loader
adb reboot bootloader
fastboot wait-for-devices
dodge-xl enter
xl stop

echo == 3. the images
xl ramdisk userland\system.apfs

echo == 4. the run
set BASE=hv_wdt_pet=1 cpus=4 bootcpu=5 adreno8=23 adreno8_keep=1 adreno8_return=0 adreno8_secure_preflight=4 metal=1 ca_metallib=/redwood/share/metallib/QuartzCore-default.metallib fb_hz=120 gpu_recover=1 synaptics=1 buttons_pin=0x18d buttons_swap=1 buttons=1 torch=3 bt=4 pcie_bt_en=1 bt_nvm_max=9477 gl=1 batt_dsp=4 usb_live=1 batt_dsp_wait=20 adsp=1 adsp_adopt=1 adsp_smem=0x81d00000 adsp_smemwhole=1 adsp_keepvotes=1 adsp_gicmask=1 adsp_noipcc=1 adsp_bw=1 adsp_loud=1 adsp_mem=0x9ee00000 adsp_size=0x5100000 lowdma=0x00000000cbfd9000 lowdma_mb=64 sound=4 sound_smmu=1 slider=1 slider_power=1 slider_cal=6857,12169,-1267,-155,17663,-2152,-6765,12759,-1388 sound_pins=1 sound_ddrbuf=1
set XL_RAMDISK=userland\root.apfs
set XL_SECS=%SECS%
xl run 0
