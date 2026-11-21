#!/bin/bash
# Build Mesa 26.2.5 with Freedreno (a7xx/a8xx) OSMesa for this phone's Adreno 830.
# The port layer is ios-dodge's device-verified consolidated patch (Darwin DRM on the
# kernel's HakiosGPU user client, restored OSMesa 26 frontends, Freedreno Gen8 fixes);
# the one adaptation is the GPU service name (their tree says RedwoodGPU, ours HakiosGPU).
#
#   out/libOSMesa.8.dylib   the library (GL=1 of userland/build.sh puts it in the image)
#   out/exported-gl.txt     the gl* functions it exports, for userland/mkeagl.py
#   out/gl.xml              their prototypes (the Khronos registry as Mesa has it)
set -e
# the tree: this runs from a device repository's mesa-files/ (real in the CI's tree, or through
# the symlink the local flow makes) -- one marker, checked at both levels, walking up
d=$(cd "$(dirname "$0")" && pwd)
while [ "$d" != "/" ]; do
	for t in "$d" "$d/kernel"; do
		[ -d "$t/xnu-src/xnu-8796.121.2" ] && ROOT=$t && break 2
	done
	d=$(dirname "$d")
done
[ -n "${ROOT:-}" ] && [ -d "$ROOT/xnu-src/xnu-8796.121.2" ] || { echo "mk-mesa26.sh: no kernel tree above this script" >&2; exit 1; }
B=$ROOT/xnu-deps/build/mesa
VERSION=26.2.5
SHA256=794b3d1729075dae7ebd47c7283b7ca0cdb5dec668f7f11cc0a5d82f6138a0a5
# the port layer is the consolidated GLES patch of ios-dodge's port (this repository
# carries it as it was verified on that phone, with thanks); only the Mesa tarball comes
# down, from Mesa's own archive. The one adaptation this port makes -- the GPU service's
# name -- is the sed below. The patch sits beside this script.
PORT=$(cd "$(dirname "$0")" && pwd)/mesa-26.2.5-op13.patch
[ -f "$PORT" ] || { echo "mk-mesa26.sh: no mesa-26.2.5-op13.patch beside this script" >&2; exit 1; }
mkdir -p "$B"; cd "$B"

[ -f mesa-$VERSION.tar.xz ] || \
		curl -sL --fail --retry 3 -o mesa-$VERSION.tar.xz https://archive.mesa3d.org/mesa-$VERSION.tar.xz
echo "$SHA256  mesa-$VERSION.tar.xz" | sha256sum -c --quiet

STAMP=$(cat "$PORT" | sha256sum | cut -d' ' -f1)
if [ ! -d mesa-$VERSION ] || [ "$(cat mesa-$VERSION/.op13-patch 2>/dev/null)" != "$STAMP" ]; then
	rm -rf mesa-$VERSION
	tar xJf mesa-$VERSION.tar.xz
	patch -s --batch --forward -d mesa-$VERSION -p1 < "$PORT"
	# this tree's kernel names its GPU service HakiosGPU (the user client is the same:
	# the msm selectors and the 0x7e surface request line up one to one)
	grep -rl RedwoodGPU mesa-$VERSION | while read -r f; do
		sed -i 's/RedwoodGPU/HakiosGPU/g' "$f"
	done
	echo "$STAMP" > mesa-$VERSION/.op13-patch
fi
# a private venv for Mesa's builders: Mako and a pinned Meson (26.x requires >= 1.4 and
# Ubuntu noble ships 1.3; a pinned builder is also what makes the build reproducible
# across machines -- the common repository's mk-mesa.sh does the same)
if [ ! -x venv/bin/python3 ]; then
	python3 -m venv venv
	venv/bin/pip -q install mako pyyaml packaging "meson==1.12.1"
fi
export PATH=$B/venv/bin:$PATH
cd mesa-$VERSION

# the cross file: the tree's own tools by its ROOT, the SDK wherever THEOS points
# (the container sets THEOS=/theos, the CI $HOME/theos; /theos the fallback)
THEOS=${THEOS:-/theos}
[ -d "$THEOS/sdks/iPhoneOS16.5.sdk" ] || { echo "mk-mesa26.sh: no iPhoneOS16.5.sdk under $THEOS" >&2; exit 1; }
cat > "$B/cross26.ini" <<EOF
[binaries]
c = ['$ROOT/xnu-deps/host/bin/xnu-clang', '-arch', 'arm64', '-isysroot', '$THEOS/sdks/iPhoneOS16.5.sdk']
cpp = ['$ROOT/xnu-deps/host/bin/xnu-clang++', '-arch', 'arm64', '-isysroot', '$THEOS/sdks/iPhoneOS16.5.sdk', '-fuse-cxa-atexit']
objc = ['$ROOT/xnu-deps/host/bin/xnu-clang', '-arch', 'arm64', '-isysroot', '$THEOS/sdks/iPhoneOS16.5.sdk']
ar = 'llvm-ar'
ranlib = 'llvm-ranlib'
strip = 'llvm-strip'
pkg-config = 'false'
[host_machine]
system = 'darwin'
subsystem = 'ios'
cpu_family = 'aarch64'
cpu = 'arm64'
endian = 'little'
[properties]
needs_exe_wrapper = true
EOF

OPTIONS="platforms= gallium-drivers=softpipe,freedreno freedreno-kmds=msm vulkan-drivers=
	glx=disabled egl=disabled gbm=disabled osmesa=true llvm=disabled shared-glapi=enabled
	gles1=disabled gles2=enabled opengl=true zstd=disabled xmlconfig=disabled expat=disabled
	shader-cache=disabled valgrind=disabled libunwind=disabled glvnd=disabled video-codecs=
	tools= gallium-vdpau=disabled gallium-va=disabled gallium-xa=disabled lmsensors=disabled
	build-tests=false"
SUPPORTED=$(python3 - <<'PY'
import re
print('\n'.join(re.findall(r"option\(\s*'([^']+)'", open('meson.options').read())))
PY
)
FILTERED=
for o in $OPTIONS; do
	echo "$SUPPORTED" | grep -qx "${o%%=*}" && FILTERED="$FILTERED $o"
done

if [ ! -d "$B/build-26" ]; then
	meson setup "$B/build-26" . --cross-file "$B/cross26.ini" --wrap-mode=nofallback \
		--prefix=/redwood --libdir=lib --buildtype=release -Db_ndebug=true \
		$(for o in $FILTERED; do printf ' -D%s' "$o"; done)
fi
ninja -C "$B/build-26" -j"$(nproc)" src/gallium/targets/osmesa/libOSMesa.8.dylib

cd "$B"
mkdir -p out26
cp "$B/build-26/src/gallium/targets/osmesa/libOSMesa.8.dylib" out26/
# meson links the dylib with install name @rpath/...; the image loads it by its absolute path
$ROOT/xnu-deps/host/bin/install_name_tool -id /redwood/lib/libOSMesa.8.dylib out26/libOSMesa.8.dylib
llvm-nm -gU out26/libOSMesa.8.dylib | awk '$3 ~ /^_gl/ { print substr($3, 2) }' > out26/exported-gl.txt
cp mesa-$VERSION/src/mesa/glapi/glapi/registry/gl.xml out26/gl.xml
echo "mk-mesa26: out26/libOSMesa.8.dylib $(stat -c%s out26/libOSMesa.8.dylib) bytes," \
	"$(wc -l < out26/exported-gl.txt) gl* functions"
# install as the active out/ (whatever earlier output there was is kept beside it)
if [ ! -d out25 ] && [ -d out ]; then mv out out25; fi
rm -rf out && mv out26 out
cat "$PORT" | sha256sum > out/built-from
