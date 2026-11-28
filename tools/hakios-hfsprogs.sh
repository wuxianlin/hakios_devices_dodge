#!/bin/sh
# hakios-hfsprogs.sh: mkfs.hfsplus and fsck.hfsplus in the build container, built from hfsprogs' own
# sources (Apple's diskdev_cmds, as Debian and Ubuntu package them) -- for tools/hakios-datavol.py,
# iOS's data volume being made with mkfs.hfsplus (tools/mkdatavol.py of the common tree says why).
#
# Runs in the build container, as root, from its /work:
#   docker exec -u 0 hakios-ctr devices/dodge/tools/hakios-hfsprogs.sh
#
# The sources come from the Ubuntu archive the container's own apt is set to; the packages are the
# archive's too. What it makes goes to /usr/local/bin: in the container, not in its image -- so this
# runs again when the container is made anew (tools/hakios-data.py create sees to it).
set -e
export DEBIAN_FRONTEND=noninteractive LC_ALL=C

[ -x /usr/local/bin/mkfs.hfsplus ] && [ -x /usr/local/bin/fsck.hfsplus ] && {
	echo "hakios-hfsprogs: already made"
	exit 0
}

apt-get update -q
apt-get install -y -q --no-install-recommends dpkg-dev debhelper quilt dh-exec libssl-dev libbsd-dev fakeroot curl

archive=$(grep -h -o -m1 'http[^ ]*/ubuntu' /etc/apt/sources.list.d/ubuntu.sources 2>/dev/null | head -1)
[ -n "$archive" ] || archive=http://archive.ubuntu.com/ubuntu
pool=$archive/pool/universe/h/hfsprogs
rm -rf /opt/hfsprogs
mkdir -p /opt/hfsprogs
cd /opt/hfsprogs
index=$(curl -fsSL "$pool/")
orig=$(printf '%s\n' "$index" | grep -o 'hfsprogs_540\.1\.linux[0-9]*\.orig\.tar\.gz' | sort -V | tail -1)
debian=$(printf '%s\n' "$index" | grep -o 'hfsprogs_540\.1\.linux[0-9]*-[0-9a-z.~]*\.debian\.tar\.xz' | sort -V | tail -1)
[ -n "$orig" ] && [ -n "$debian" ] || {
	echo "hakios-hfsprogs: no hfsprogs sources under $pool" >&2
	exit 1
}
curl -fsS -O "$pool/$orig"
curl -fsS -O "$pool/$debian"
tar xf "$orig"
cd diskdev_cmds-*/
tar xf "../$debian"
# (-d: the build dependencies are what was installed above, not what dpkg thinks a builder has)
dpkg-buildpackage -us -uc -b -nc -d > build.log 2>&1 || {
	echo "hakios-hfsprogs: the build failed:" >&2
	grep -n -i -B2 -A6 'error' build.log | tail -40 >&2
	exit 1
}
install -m 755 newfs_hfs.tproj/newfs_hfs /usr/local/bin/mkfs.hfsplus
install -m 755 fsck_hfs.tproj/fsck_hfs /usr/local/bin/fsck.hfsplus
echo "hakios-hfsprogs: $orig, $debian: /usr/local/bin/mkfs.hfsplus, /usr/local/bin/fsck.hfsplus"
