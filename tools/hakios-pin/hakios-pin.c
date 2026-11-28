/*
 * hakios-pin: a file in Android's /data whose blocks HakiOS reads and writes on the phone's raw storage.
 *
 * Why (docs/data-persistence.md): on a phone whose userdata is encrypted below the
 * file system (dm-default-key, "metadata encryption") every byte Android writes lands on the flash
 * as ciphertext -- an unencrypted file (FBE is per file) included. So the image is not written
 * through Android at all: the file only reserves the blocks (f2fs keeps a pinned file's blocks
 * where they are), and the image goes onto those blocks of the raw device, where the kernel's
 * encryption never comes between. Android's own precedent for the reverse direction is uncrypt
 * (bootable/recovery/uncrypt/uncrypt.cpp): it reads a package through the file system and writes
 * its bytes to the same blocks of the raw device.
 *
 *   create   FILE                an empty file
 *   pin      FILE                before the file has blocks: f2fs refuses to pin a file that has.
 *                                No block of a pinned file is moved by the background GC
 *   unpin    FILE
 *   alloc    FILE BYTES          fallocate: the blocks, reserved (what they hold is not defined)
 *   put      FILE BYTES          what comes on standard input, through Android (a test)
 *   putraw   FILE RAW BYTES      what comes on standard input, onto the file's blocks of RAW;
 *                                every piece is read back and compared
 *   putblocks FILE RAW F N       what comes on standard input, N blocks onto the volume's blocks F
 *                                on: the same, a piece of a run at a time (so a link that drops
 *                                under a long transfer is survived)
 *   zeroraw  FILE RAW            zeros onto the file's blocks of RAW
 *   zeroblocks FILE RAW F N      the same, N blocks of the volume's from F on
 *   getblocks FILE RAW F N       N blocks of the volume from F on, as the raw device holds them, on
 *                                standard output (for the host to look at the volume from there)
 *   mark     FILE RAW            the marker in the volume's first block, as the loader reads it
 *   guard    FILE RAW [FIRST]    for each extent of the file, the FNV-1a 64 of its first block and
 *                                then of its last, as RAW holds them ("guard I 0x..."): what the
 *                                loader looks for again before it serves a write
 *   new      FILE BLOCKS NONCE   create, pin, alloc, then a pattern through Android (a test)
 *   rewrite  FILE RAW            the file as Android reads it, onto its blocks of RAW -- once:
 *                                afterwards Android reads the file as garbage
 *   map      FILE RAW [FIRST]    the file's blocks as extents, each one checked against FIBMAP and
 *                                against the device; FIRST is the first block that may hold file
 *                                data (f2fs's main_blkaddr): below it is the file system's own
 *   check    FILE RAW NONCE      reads only: the raw blocks beside the pattern
 *   hash     BLOCKS NONCE        FNV-1a 64 of the pattern
 *   hashraw  FILE RAW            FNV-1a 64 and SHA-256 of the file's blocks as RAW holds them (the
 *                                host compares the SHA-256 with its own of the image: sha256sum)
 *
 * NONCE is the key of the pattern: up to 16 hex digits. The marker is 16 bytes at byte 2048 of the
 * volume's first block: past HFS Plus's volume header and before its allocation file, where the file
 * system never comes -- and what the loader looks for before it serves a write (xnuloader/loader.c,
 * HAKIOS_MARKER_AT). The marker says "this is the volume"; the guards say "these blocks are still
 * where they were". A pin is not a guarantee, and a foreground GC moves a file a section at a time --
 * its first block may stay while others move, which the marker alone could not tell. So the host
 * makes the map and the guards afresh from Android each time, a moment before the phone leaves
 * Android (tools/hakios-data.py boot), and the loader serves a write only when the first and the
 * last block of every extent still hold what their guards say.
 *
 * The file must not be written, truncated or deleted from then on: f2fs would hand its blocks out
 * again. And a pin is not a guarantee -- a foreground GC, with the storage nearly full, may still
 * move blocks -- so whoever reads them checks what they hold first.
 *
 * Needs root: FIBMAP, the pin ioctls and the raw device are the kernel's alone to allow.
 *
 * Android has no headers or libraries to build against, so this is one file with the kernel's ABI
 * in it and nothing from libc: build.sh makes a static ARM64 program of it.
 */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;

#define BS 4096u
#define MARKER_AT 2048u                       /* in the volume's first block */
#define PIECE (1u << 20)                      /* one read or write: 256 blocks */
#define PER_PIECE (PIECE / BS)

/* ---- the kernel's ABI ---- */
#define SYS_IOCTL 29
#define SYS_FALLOCATE 47
#define SYS_OPENAT 56
#define SYS_CLOSE 57
#define SYS_LSEEK 62
#define SYS_READ 63
#define SYS_WRITE 64
#define SYS_PREAD64 67
#define SYS_PWRITE64 68
#define SYS_FSYNC 82
#define SYS_EXIT 93

#define AT_FDCWD (-100L)
#define O_RDONLY 0L
#define O_WRONLY 1L
#define O_RDWR 2L
#define O_CREAT 0100L
#define O_EXCL 0200L
#define O_DIRECT 0200000L
#define SEEK_END 2
#define EAGAIN 11

#define IO_FIBMAP 0x0001
#define IO_FIGETBSZ 0x0002
#define IO_FS_GETFLAGS 0x80086601UL          /* _IOR('f', 1, long) */
#define IO_BLKGETSIZE64 0x80081272UL         /* _IOR(0x12, 114, u64) */
#define IO_FIEMAP 0xC020660BUL               /* _IOWR('f', 11, struct fiemap) */
#define IO_F2FS_SET_PIN 0x4004F50DUL         /* _IOW(0xf5, 13, u32) */
#define IO_F2FS_GET_PIN 0x8004F50EUL         /* _IOR(0xf5, 14, u32) */
#define FS_NOCOW_FL 0x00800000u              /* what f2fs shows a pinned file as */

#define FE_LAST 0x00000001u                  /* the last extent of the file */
#define FE_MERGED 0x00001000u                /* f2fs sets it whenever it joined map results */
#define FE_OK (FE_LAST | FE_MERGED)

struct fiemap_extent {
	u64 logical, physical, length, r1, r2;
	u32 flags, r3, r4, r5;
};
struct fiemap {
	u64 start, length;
	u32 flags, mapped, count, reserved;
	struct fiemap_extent e[256];
};

static long
sysc(long n, long a, long b, long c, long d, long e, long f)
{
	register long x8 asm("x8") = n;
	register long x0 asm("x0") = a;
	register long x1 asm("x1") = b;
	register long x2 asm("x2") = c;
	register long x3 asm("x3") = d;
	register long x4 asm("x4") = e;
	register long x5 asm("x5") = f;

	asm volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2), "r"(x3), "r"(x4), "r"(x5)
		     : "memory", "cc");
	return x0;
}

/* ---- what libc would have given ---- */
void *memcpy(void *d, const void *s, unsigned long n)
{
	u8 *a = d;
	const u8 *b = s;

	while (n--) {
		*a++ = *b++;
	}
	return d;
}
void *memset(void *d, int c, unsigned long n)
{
	u8 *a = d;

	while (n--) {
		*a++ = (u8)c;
	}
	return d;
}
static int
memeq(const void *d, const void *s, u64 n)
{
	const u8 *a = d, *b = s;

	while (n--) {
		if (*a++ != *b++) {
			return 0;
		}
	}
	return 1;
}

static void
put(const char *s, u64 n)
{
	while (n) {
		long r = sysc(SYS_WRITE, 1, (long)s, (long)n, 0, 0, 0);

		if (r <= 0) {
			sysc(SYS_EXIT, 3, 0, 0, 0, 0, 0);
		}
		s += r;
		n -= (u64)r;
	}
}
static void
ps(const char *s)
{
	u64 n = 0;

	while (s[n]) {
		n++;
	}
	put(s, n);
}
static void
pd(u64 v)
{
	char b[24];
	int n = 0;

	do {
		b[n++] = (char)('0' + v % 10);
		v /= 10;
	} while (v);
	while (n) {
		put(&b[--n], 1);
	}
}
static void
px(u64 v)
{
	char b[16];
	int n;

	for (n = 0; n < 16; n++) {
		b[15 - n] = "0123456789abcdef"[v & 15];
		v >>= 4;
	}
	put(b, 16);
}

__attribute__((noreturn)) static void
leave(int code, const char *what, long err)
{
	if (what) {
		ps("hakios-pin: ");
		ps(what);
		if (err) {
			ps(": error ");
			pd((u64)(err < 0 ? -err : err));
		}
		put("\n", 1);
	}
	sysc(SYS_EXIT, code, 0, 0, 0, 0, 0);
	for (;;) {
	}
}
#define fail(what, err) leave(2, what, (long)(err))
#define refuse(what) leave(2, what, 0)

__attribute__((noreturn)) static void
usage(void)
{
	ps("usage: hakios-pin create FILE | pin FILE | unpin FILE | alloc FILE BYTES | put FILE BYTES |"
	   " putraw FILE RAW BYTES | putblocks FILE RAW FIRST COUNT | getblocks FILE RAW FIRST COUNT |"
	   " zeroraw FILE RAW | zeroblocks FILE RAW FIRST COUNT | mark FILE RAW | guard FILE RAW [FIRST] |"
	   " new FILE BLOCKS NONCE | rewrite FILE RAW | map FILE RAW [FIRST] | check FILE RAW NONCE |"
	   " hash BLOCKS NONCE | hashraw FILE RAW\n");
	leave(1, 0, 0);
}

/* ---- files and the device ---- */
static u64 dev_bytes;
static u64 file_bytes;
static const char *raw_arg;

static int
open_path(const char *path, long flags)
{
	long fd = sysc(SYS_OPENAT, AT_FDCWD, (long)path, flags, 0600, 0, 0);

	if (fd < 0) {
		fail(path, fd);
	}
	return (int)fd;
}
static int
create_file(const char *path)
{
	return open_path(path, O_WRONLY | O_CREAT | O_EXCL);
}
/* the file, with its size known and a whole number of blocks */
static int
open_file(const char *path, long flags)
{
	int fd = open_path(path, flags);
	u32 block = 0;
	long r = sysc(SYS_IOCTL, fd, IO_FIGETBSZ, (long)&block, 0, 0, 0);

	if (r < 0) {
		fail("FIGETBSZ", r);
	}
	if (block != BS) {
		refuse("the file system's block is not 4096 bytes");
	}
	r = sysc(SYS_LSEEK, fd, 0, SEEK_END, 0, 0, 0);
	if (r <= 0) {
		fail("the file's size (is it empty?)", r < 0 ? r : -2);
	}
	file_bytes = (u64)r;
	if (file_bytes % BS) {
		refuse("the file's size is not a whole number of blocks");
	}
	return fd;
}
static int
open_raw(const char *path, int for_writing)
{
	int fd = open_path(path, (for_writing ? O_RDWR : O_RDONLY) | O_DIRECT);
	long r;

	dev_bytes = 0;
	r = sysc(SYS_IOCTL, fd, IO_BLKGETSIZE64, (long)&dev_bytes, 0, 0, 0);
	if (r < 0 || dev_bytes == 0) {
		fail("BLKGETSIZE64 on the raw device", r < 0 ? r : -2);
	}
	return fd;
}
static void
read_at(int fd, void *buf, u64 n, u64 at, const char *what)
{
	u8 *p = buf;

	while (n) {
		long r = sysc(SYS_PREAD64, fd, (long)p, (long)n, (long)at, 0, 0);

		if (r <= 0) {
			fail(what, r < 0 ? r : -2);
		}
		p += r;
		at += (u64)r;
		n -= (u64)r;
	}
}
static void
write_at(int fd, const void *buf, u64 n, u64 at, const char *what)
{
	const u8 *p = buf;

	while (n) {
		long r = sysc(SYS_PWRITE64, fd, (long)p, (long)n, (long)at, 0, 0);

		if (r <= 0) {
			fail(what, r < 0 ? r : -2);
		}
		p += r;
		at += (u64)r;
		n -= (u64)r;
	}
}
static void
sync_fd(int fd, const char *what)
{
	long r = sysc(SYS_FSYNC, fd, 0, 0, 0, 0, 0);

	if (r < 0) {
		fail(what, r);
	}
}
static u64
piece_of(u64 left)
{
	return left < PIECE ? left : PIECE;
}

/* ---- the pin ---- */
static int is_pinned;

/* what the kernel says of the file: pinned (f2fs shows it as NOCOW), and how many of its blocks a
 * foreground GC has moved all the same */
static u32
pin_state(int fd)
{
	u32 flags = 0, moved = 0;
	long r = sysc(SYS_IOCTL, fd, IO_FS_GETFLAGS, (long)&flags, 0, 0, 0);

	if (r < 0) {
		fail("FS_IOC_GETFLAGS", r);
	}
	r = sysc(SYS_IOCTL, fd, IO_F2FS_GET_PIN, (long)&moved, 0, 0, 0);
	if (r < 0) {
		fail("F2FS_IOC_GET_PIN_FILE", r);
	}
	is_pinned = (flags & FS_NOCOW_FL) != 0;
	return moved;
}

/* F2FS_IOC_SET_PIN_FILE, then what the kernel says of the file now */
static void
pin_file(int fd, const char *file, int pin)
{
	u32 set = pin ? 1u : 0u, moved;
	long r;

	r = sysc(SYS_IOCTL, fd, IO_F2FS_SET_PIN, (long)&set, 0, 0, 0);
	if (r < 0) {
		if (pin && (r == -5 || r == -27)) {
			ps("hakios-pin: ");
			ps(file);
			refuse(": f2fs will not pin this file (has it blocks already? pin it while it is empty)");
		}
		fail("F2FS_IOC_SET_PIN_FILE (an f2fs file and root are needed)", r);
	}
	moved = pin_state(fd);
	ps("hakios-pin: ");
	ps(file);
	ps(": pinned ");
	ps(is_pinned ? "yes" : "no");
	ps(", blocks moved ");
	pd(moved);
	put("\n", 1);
	if (pin && !is_pinned) {
		refuse("the file did not take the pin");
	}
	if (pin && moved) {
		refuse("blocks of the file were moved: its extents are not fixed");
	}
}

/* fallocate, in pieces if the file system answers EAGAIN after part of the work */
static void
alloc_file(int fd, const char *file, u64 bytes)
{
	int tries;

	if (bytes == 0 || bytes % BS) {
		refuse("BYTES is a whole number of 4096-byte blocks, and not 0");
	}
	for (tries = 0; tries < 10000; tries++) {
		long r = sysc(SYS_FALLOCATE, fd, 0, 0, (long)bytes, 0, 0);

		if (r == 0) {
			break;
		}
		if (r != -EAGAIN) {
			ps("hakios-pin: ");
			ps(file);
			ps(": fallocate: error ");
			pd((u64)-r);
			put("\n", 1);
			if (r == -5 || r == -27) {
				refuse("a pinned file is written only into blocks it already has: alloc it first");
			}
			leave(2, 0, 0);
		}
	}
	if (tries == 10000) {
		fail("fallocate kept answering EAGAIN", -EAGAIN);
	}
	sync_fd(fd, "fsync the file");
}

/* ---- the pattern: block i is a stream keyed by NONCE and i ---- */
static u64 nonce;

static u64
splitmix(u64 *x)
{
	u64 z;

	*x += 0x9e3779b97f4a7c15ULL;
	z = *x;
	z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
	z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
	return z ^ (z >> 31);
}
static void
pattern_block(u8 *b, u64 i)
{
	u64 x = nonce ^ (i * 0x9e3779b97f4a7c15ULL);
	int k;

	for (k = 0; k < (int)(BS / 8); k++) {
		((u64 *)b)[k] = splitmix(&x);
	}
}
static u64
fnv(u64 h, const u8 *b, u64 n)
{
	while (n--) {
		h ^= *b++;
		h *= 0x100000001b3ULL;
	}
	return h;
}

/* SHA-256, for what the host can check with sha256sum */
struct sha {
	u32 h[8];
	u64 bytes;
	u32 n;
	u8 buf[64];
};
static const u32 sha_k[64] = {
	0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
	0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
	0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
	0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
	0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
	0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
	0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
	0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
};
static u32
rotr(u32 x, int n)
{
	return (x >> n) | (x << (32 - n));
}
static void
sha_init(struct sha *s)
{
	static const u32 h0[8] = { 0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c,
				  0x1f83d9ab, 0x5be0cd19 };

	memcpy(s->h, h0, sizeof(h0));
	s->bytes = 0;
	s->n = 0;
}
static void
sha_block(struct sha *s, const u8 *p)
{
	u32 w[64], a, b, c, d, e, f, g, h;
	int i;

	for (i = 0; i < 16; i++) {
		w[i] = ((u32)p[i * 4] << 24) | ((u32)p[i * 4 + 1] << 16) | ((u32)p[i * 4 + 2] << 8) | p[i * 4 + 3];
	}
	for (; i < 64; i++) {
		u32 s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
		u32 s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);

		w[i] = w[i - 16] + s0 + w[i - 7] + s1;
	}
	a = s->h[0], b = s->h[1], c = s->h[2], d = s->h[3];
	e = s->h[4], f = s->h[5], g = s->h[6], h = s->h[7];
	for (i = 0; i < 64; i++) {
		u32 s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25), ch = (e & f) ^ (~e & g);
		u32 t1 = h + s1 + ch + sha_k[i] + w[i];
		u32 s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22), maj = (a & b) ^ (a & c) ^ (b & c);
		u32 t2 = s0 + maj;

		h = g, g = f, f = e, e = d + t1, d = c, c = b, b = a, a = t1 + t2;
	}
	s->h[0] += a, s->h[1] += b, s->h[2] += c, s->h[3] += d;
	s->h[4] += e, s->h[5] += f, s->h[6] += g, s->h[7] += h;
}
static void
sha_update(struct sha *s, const u8 *p, u64 n)
{
	s->bytes += n;
	while (n) {
		u32 k = 64 - s->n < n ? 64 - s->n : (u32)n;

		memcpy(s->buf + s->n, p, k);
		s->n += k, p += k, n -= k;
		if (s->n == 64) {
			sha_block(s, s->buf);
			s->n = 0;
		}
	}
}
static void
sha_final(struct sha *s, u8 out[32])
{
	u64 bits = s->bytes * 8;
	u8 pad[72];
	u64 n = s->n < 56 ? 56 - s->n : 120 - s->n;
	int i;

	memset(pad, 0, (unsigned long)n);
	pad[0] = 0x80;
	sha_update(s, pad, n);
	for (i = 0; i < 8; i++) {
		pad[i] = (u8)(bits >> (56 - 8 * i));
	}
	sha_update(s, pad, 8);
	for (i = 0; i < 8; i++) {
		out[i * 4] = (u8)(s->h[i] >> 24);
		out[i * 4 + 1] = (u8)(s->h[i] >> 16);
		out[i * 4 + 2] = (u8)(s->h[i] >> 8);
		out[i * 4 + 3] = (u8)s->h[i];
	}
}

/* ---- the file's blocks ---- */
struct extent {
	u64 lblk, pblk, n;                         /* in blocks of 4096 */
};
#define MAX_EXT 65536
static struct extent ext[MAX_EXT];
static u64 n_ext;

static struct fiemap fm;
static u8 piece[PIECE] __attribute__((aligned(4096)));
static u8 back[PIECE] __attribute__((aligned(4096)));

/* FIEMAP, into ext[]: the whole file, block by block, with nothing between the extents */
static void
collect(int fd)
{
	u64 off = 0, last = 0, len;

	n_ext = 0;
	do {
		u32 k;

		memset(&fm, 0, sizeof(fm));
		fm.start = off;
		fm.length = ~0ULL;
		fm.count = 256;
		if (sysc(SYS_IOCTL, fd, IO_FIEMAP, (long)&fm, 0, 0, 0) < 0) {
			fail("FIEMAP", -5);
		}
		if (fm.mapped == 0 || fm.mapped > 256) {
			refuse("FIEMAP returned nothing for part of the file");
		}
		for (k = 0; k < fm.mapped; k++) {
			const struct fiemap_extent *e = &fm.e[k];

			if (e->flags & ~FE_OK) {
				ps("hakios-pin: an extent with flags 0x");
				px(e->flags);
				refuse(" (shared, unwritten, encrypted or encoded): refused");
			}
			if ((e->physical | e->length) % BS) {
				refuse("an extent not in whole blocks");
			}
			if (e->logical != off) {
				refuse("a hole, or extents out of order");
			}
			/* f2fs allocates a file whose size is not a multiple of its own unit up to that unit's end: the
			 * blocks past the file's end are the file's own (given to nobody else), and FIEMAP reports them in
			 * the last extent. They end the volume here: the file's last block is. */
			len = e->length;
			if (off + len > file_bytes) {
				if (off >= file_bytes || !(e->flags & FE_LAST) || (file_bytes - off) % BS) {
					refuse("an extent past the end of the file, and not its last");
				}
				len = file_bytes - off;
			}
			if (n_ext && ext[n_ext - 1].pblk + ext[n_ext - 1].n == e->physical / BS) {
				ext[n_ext - 1].n += len / BS;
			} else {
				if (n_ext == MAX_EXT) {
					refuse("more extents than this program holds");
				}
				ext[n_ext].lblk = off / BS;
				ext[n_ext].pblk = e->physical / BS;
				ext[n_ext].n = len / BS;
				n_ext++;
			}
			off += len;
			last = e->flags & FE_LAST;
		}
	} while (!last);

	/* every block of the file is there, and nothing past its last block */
	if (off != file_bytes) {
		refuse("the extents do not cover the file exactly");
	}
}

/* The extents are where file data may be, and a second ioctl says the same of them. */
static void
check_extents(int fd, u64 first)
{
	u64 i, total = 0;

	if (n_ext == 0) {
		refuse("the file has no extents (alloc it first)");
	}
	for (i = 0; i < n_ext; i++) {
		u64 j, l, last_lblk = ext[i].lblk + ext[i].n - 1;

		if (ext[i].n == 0) {
			refuse("an empty extent");
		}
		if (ext[i].pblk < first) {
			refuse("an extent in the file system's own area of the device: refused");
		}
		if (ext[i].pblk + ext[i].n > dev_bytes / BS) {
			refuse("an extent past the end of the device: refused");
		}
		for (j = 0; j < i; j++) {
			if (ext[i].pblk < ext[j].pblk + ext[j].n && ext[j].pblk < ext[i].pblk + ext[i].n) {
				refuse("two extents on the same blocks: refused");
			}
		}
		total += ext[i].n;
		/* FIBMAP names a block in the file system's blocks: the unit FIEMAP was read in */
		for (l = ext[i].lblk;; l += 127) {
			u32 b = (u32)(l > last_lblk ? last_lblk : l);
			u64 want = ext[i].pblk + (l > last_lblk ? ext[i].n - 1 : l - ext[i].lblk);

			if (sysc(SYS_IOCTL, fd, IO_FIBMAP, (long)&b, 0, 0, 0) < 0) {
				fail("FIBMAP (root is needed)", -5);
			}
			if ((u64)b != want) {
				refuse("FIBMAP and FIEMAP disagree about a block");
			}
			if (l > last_lblk) {
				break;
			}
		}
	}
	if (total * BS != file_bytes) {
		refuse("the extents hold a different number of blocks than the file");
	}
}

static void
print_extents(void)
{
	u64 i;

	for (i = 0; i < n_ext; i++) {
		ps("extent ");
		pd(ext[i].lblk);
		put(" ", 1);
		pd(ext[i].pblk);
		put(" ", 1);
		pd(ext[i].n);
		put("\n", 1);
	}
	ps("extents ");
	pd(n_ext);
	ps(" blocks ");
	pd(file_bytes / BS);
	ps(" file-bytes ");
	pd(file_bytes);
	ps(" device-bytes ");
	pd(dev_bytes);
	ps(is_pinned ? " pinned" : " not pinned");
	put("\n", 1);
}

/* the file and the raw device, the extents found and checked; they stay open for the caller */
static int g_fd, g_raw;

static void
both(const char *file, int raw_for_writing, int file_direct, u64 first)
{
	g_fd = open_file(file, file_direct ? O_RDONLY | O_DIRECT : O_RDONLY);
	g_raw = open_raw(raw_arg, raw_for_writing);
	collect(g_fd);
	check_extents(g_fd, first);
	if (pin_state(g_fd) && raw_for_writing) {
		refuse("a foreground GC has moved blocks of this file: nothing is written; make its map again");
	}
}

/* ---- arguments ---- */
static int
streq(const char *a, const char *b)
{
	while (*a && *a == *b) {
		a++;
		b++;
	}
	return *a == *b;
}
static u64
number(const char *s, const char *what)
{
	u64 v = 0;
	int any = 0;

	while (*s >= '0' && *s <= '9') {
		v = v * 10 + (u64)(*s - '0');
		s++;
		any = 1;
	}
	if (!any || *s) {
		refuse(what);
	}
	return v;
}
static u64
hexnonce(const char *s)
{
	u64 v = 0;
	int n = 0;

	while (n < 16) {
		char c = s[n];
		int d;

		if (c >= '0' && c <= '9') {
			d = c - '0';
		} else if (c >= 'a' && c <= 'f') {
			d = c - 'a' + 10;
		} else if (c >= 'A' && c <= 'F') {
			d = c - 'A' + 10;
		} else {
			break;
		}
		v = v * 16 + (u64)d;
		n++;
	}
	if (n == 0 || s[n]) {
		refuse("NONCE is up to 16 hex digits");
	}
	return v;
}

/* ---- a piece at a time, over the file's blocks in the file's own order ---- */
static u64 at_i, at_l;                      /* how far through ext[] the walk is */

/* the next piece: its bytes (0 at the end), where it is on the raw device, its first file block */
static u64
next_piece(u64 *at, u64 *lblk)
{
	u64 n;

	if (at_i == n_ext) {
		return 0;
	}
	n = ext[at_i].n - at_l < PER_PIECE ? ext[at_i].n - at_l : PER_PIECE;
	*at = (ext[at_i].pblk + at_l) * BS;
	*lblk = ext[at_i].lblk + at_l;
	at_l += n;
	if (at_l == ext[at_i].n) {
		at_i++;
		at_l = 0;
	}
	return n * BS;
}

/* the extent the volume's block lblk is in (the extents cover the volume without a gap) */
static u32
extent_of(u64 lblk)
{
	u32 lo = 0, hi = n_ext - 1;

	while (lo < hi) {
		u32 mid = (lo + hi) / 2;

		if (lblk < ext[mid].lblk + ext[mid].n) {
			hi = mid;
		} else {
			lo = mid + 1;
		}
	}
	return lo;
}

static void
progress(const char *what, u64 done, u64 total)
{
	if ((done & ((1u << 30) - 1)) == 0 || done == total) {
		ps("hakios-pin: ");
		ps(what);
		put(" ", 1);
		pd(done >> 20);
		ps(" of ");
		pd(total >> 20);
		ps(" MiB\n");
	}
}
static void
read_exact(int fd, u8 *b, u64 n, const char *what)
{
	u64 got = 0;

	while (got < n) {
		long r = sysc(SYS_READ, fd, (long)(b + got), (long)(n - got), 0, 0, 0);

		if (r < 0) {
			fail(what, r);
		}
		if (r == 0) {
			fail(what, -2);                /* it ended early */
		}
		got += (u64)r;
	}
}
/* one piece onto the raw device, then back from it */
static void
put_piece(int raw, const u8 *b, u64 n, u64 at)
{
	write_at(raw, b, n, at, "write the raw device");
	read_at(raw, back, n, at, "read the raw device back");
	if (!memeq(b, back, n)) {
		ps("hakios-pin: the raw device did not keep what it was given at block ");
		pd(at / BS);
		refuse(": stopped there");
	}
}

/* COUNT blocks of standard input onto the volume's blocks FIRST on, and back from the raw device */
static void
put_run(int raw, u64 first, u64 count)
{
	u64 done = 0;

	while (done < count) {
		u32 i = extent_of(first + done);
		u64 in = ext[i].lblk + ext[i].n - (first + done), n = count - done < in ? count - done : in;
		u64 at = (ext[i].pblk + (first + done - ext[i].lblk)) * BS;

		if (n > PER_PIECE) {
			n = PER_PIECE;
		}
		read_exact(0, piece, n * BS, "standard input");
		put_piece(raw, piece, n * BS, at);
		done += n;
		progress("put", done, count);
	}
}

int
cmain(u64 *sp)
{
	int argc = (int)sp[0];
	char **argv = (char **)(sp + 1);
	const char *cmd, *file;
	int fd, raw;

	if (argc < 3) {
		usage();
	}
	cmd = argv[1];
	file = argv[2];

	if (streq(cmd, "hash")) {
		u64 blocks, i, h = 0xcbf29ce484222325ULL;

		if (argc != 4) {
			usage();
		}
		blocks = number(argv[2], "BLOCKS");
		nonce = hexnonce(argv[3]);
		for (i = 0; i < blocks; i++) {
			pattern_block(piece, i);
			h = fnv(h, piece, BS);
		}
		ps("fnv1a64 ");
		px(h);
		put("\n", 1);
		leave(0, 0, 0);
	}

	if (streq(cmd, "create")) {
		if (argc != 3) {
			usage();
		}
		fd = create_file(file);
		sync_fd(fd, "fsync the file");
		leave(0, 0, 0);
	}

	if (streq(cmd, "pin") || streq(cmd, "unpin")) {
		if (argc != 3) {
			usage();
		}
		fd = open_path(file, O_RDONLY);
		pin_file(fd, file, cmd[0] == 'p');
		sync_fd(fd, "fsync the file");
		leave(0, 0, 0);
	}

	if (streq(cmd, "alloc")) {
		u64 bytes;

		if (argc != 4) {
			usage();
		}
		bytes = number(argv[3], "BYTES");
		fd = open_path(file, O_WRONLY);
		alloc_file(fd, file, bytes);
		ps("hakios-pin: ");
		ps(file);
		ps(": ");
		pd(bytes / BS);
		ps(" blocks reserved\n");
		leave(0, 0, 0);
	}

	/* the file and the raw device, the extents found and checked; they stay open */
	fd = raw = -1;
	if (streq(cmd, "map") || streq(cmd, "check") || streq(cmd, "hashraw")) {
		u64 first = 0, same = 0, differ = 0, h = 0xcbf29ce484222325ULL, at, lblk, m, done = 0;
		struct sha sh;
		u8 digest[32];

		sha_init(&sh);

		if (streq(cmd, "map") ? (argc < 4 || argc > 5) : argc != (streq(cmd, "check") ? 5 : 4)) {
			usage();
		}
		raw_arg = argv[3];
		if (argc == 5) {
			if (streq(cmd, "check")) {
				nonce = hexnonce(argv[4]);
			} else {
				first = number(argv[4], "FIRST");
			}
		}
		both(file, 0, 0, first);
		fd = g_fd;
		raw = g_raw;
		if (streq(cmd, "map")) {
			print_extents();
			leave(0, 0, 0);
		}
		while ((m = next_piece(&at, &lblk)) != 0) {
			u64 k;

			read_at(raw, piece, m, at, "read the raw device");
			if (streq(cmd, "hashraw")) {
				h = fnv(h, piece, m);
				sha_update(&sh, piece, m);
				done += m;
				progress("hashed", done, file_bytes);
			} else {
				for (k = 0; k < m / BS; k++) {
					pattern_block(back, lblk + k);
					if (memeq(piece + k * BS, back, BS)) {
						same++;
					} else {
						differ++;
					}
				}
			}
		}
		if (streq(cmd, "hashraw")) {
			sha_final(&sh, digest);
			ps("fnv1a64 ");
			px(h);
			ps("  sha256 ");
			for (m = 0; m < 32; m++) {
				char c[2] = { "0123456789abcdef"[digest[m] >> 4], "0123456789abcdef"[digest[m] & 15] };

				put(c, 2);
			}
			put("\n", 1);
		} else {
			ps("hakios-pin: blocks on the raw device that hold the pattern: ");
			pd(same);
			ps(", that hold something else: ");
			pd(differ);
			put("\n", 1);
		}
		leave(0, 0, 0);
	}

	if (streq(cmd, "new")) {
		u64 blocks, bytes, done = 0;

		if (argc != 5) {
			usage();
		}
		blocks = number(argv[3], "BLOCKS");
		nonce = hexnonce(argv[4]);
		if (blocks == 0) {
			refuse("BLOCKS is not 0");
		}
		bytes = blocks * BS;
		/* the order f2fs asks for: empty, pinned, reserved, and only then written */
		fd = create_file(file);
		pin_file(fd, file, 1);
		alloc_file(fd, file, bytes);
		while (done < bytes) {
			u64 n = piece_of(bytes - done), k;

			for (k = 0; k < n / BS; k++) {
				pattern_block(piece + k * BS, done / BS + k);
			}
			write_at(fd, piece, n, done, "write the file");
			done += n;
			progress("written", done, bytes);
		}
		sync_fd(fd, "fsync the file");
		pin_file(fd, file, 1);
		ps("hakios-pin: ");
		ps(file);
		ps(": ");
		pd(blocks);
		ps(" blocks of the pattern, written through Android\n");
		leave(0, 0, 0);
	}

	if (streq(cmd, "put")) {
		u64 bytes, done = 0;

		if (argc != 4) {
			usage();
		}
		bytes = number(argv[3], "BYTES");
		fd = open_file(file, O_WRONLY);
		if (file_bytes != bytes) {
			refuse("BYTES is not the file's size");
		}
		while (done < bytes) {
			u64 n = piece_of(bytes - done);

			read_exact(0, piece, n, "standard input");
			write_at(fd, piece, n, done, "write the file");
			done += n;
			progress("put", done, bytes);
		}
		sync_fd(fd, "fsync the file");
		ps("hakios-pin: ");
		pd(bytes);
		ps(" bytes written to ");
		ps(file);
		ps(" through Android\n");
		leave(0, 0, 0);
	}

	if (streq(cmd, "putraw") || streq(cmd, "putblocks")) {
		u64 first = 0, count;

		if (streq(cmd, "putraw")) {
			if (argc != 5) {
				usage();
			}
			raw_arg = argv[3];
			count = number(argv[4], "BYTES");
			if (count % BS) {
				refuse("BYTES is a whole number of blocks");
			}
			count /= BS;
		} else {
			if (argc != 6) {
				usage();
			}
			raw_arg = argv[3];
			first = number(argv[4], "FIRST");
			count = number(argv[5], "COUNT");
		}
		both(file, 1, 0, 0);
		raw = g_raw;
		if (streq(cmd, "putraw") && file_bytes != count * BS) {
			refuse("BYTES is not the file's size");
		}
		if (count == 0 || first > file_bytes / BS || count > file_bytes / BS - first) {
			refuse("FIRST and COUNT are not whole blocks within the file");
		}
		put_run(raw, first, count);
		if (streq(cmd, "putraw") && sysc(SYS_READ, 0, (long)piece, PIECE, 0, 0, 0) > 0) {
			refuse("more came on standard input than the file has blocks");
		}
		sync_fd(raw, "fsync the raw device");
		ps("hakios-pin: ");
		pd(count);
		ps(" blocks, the volume's from ");
		pd(first);
		ps(" on, of ");
		ps(file);
		ps(" are on ");
		ps(raw_arg);
		ps(" in plaintext, every piece read back and compared\n");
		leave(0, 0, 0);
	}

	if (streq(cmd, "getblocks")) {
		u64 first, count, done = 0;

		if (argc != 6) {
			usage();
		}
		raw_arg = argv[3];
		first = number(argv[4], "FIRST");
		count = number(argv[5], "COUNT");
		both(file, 0, 0, 0);
		if (count == 0 || first > file_bytes / BS || count > file_bytes / BS - first) {
			refuse("FIRST and COUNT are not whole blocks within the file");
		}
		while (done < count) {
			u32 i = extent_of(first + done);
			u64 in = ext[i].lblk + ext[i].n - (first + done), n = count - done < in ? count - done : in;

			if (n > PER_PIECE) {
				n = PER_PIECE;
			}
			read_at(g_raw, piece, n * BS, (ext[i].pblk + (first + done - ext[i].lblk)) * BS, "read the raw device");
			{
				const u8 *q = piece;
				u64 left = n * BS;

				while (left) {
					long r = sysc(SYS_WRITE, 1, (long)q, (long)left, 0, 0, 0);

					if (r <= 0) {
						leave(3, "write standard output", r < 0 ? r : -2);
					}
					q += r, left -= (u64)r;
				}
			}
			done += n;
		}
		leave(0, 0, 0);
	}

	if (streq(cmd, "zeroblocks")) {
		u64 first, count, done = 0;

		if (argc != 6) {
			usage();
		}
		raw_arg = argv[3];
		first = number(argv[4], "FIRST");
		count = number(argv[5], "COUNT");
		both(file, 1, 0, 0);
		if (count == 0 || first > file_bytes / BS || count > file_bytes / BS - first) {
			refuse("FIRST and COUNT are not whole blocks within the file");
		}
		while (done < count) {
			u32 i = extent_of(first + done);
			u64 in = ext[i].lblk + ext[i].n - (first + done), n = count - done < in ? count - done : in;
			u64 at = (ext[i].pblk + (first + done - ext[i].lblk)) * BS;

			if (n > PER_PIECE) {
				n = PER_PIECE;
			}
			memset(piece, 0, (unsigned long)(n * BS));
			put_piece(g_raw, piece, n * BS, at);
			done += n;
			progress("zeroed", done, count);
		}
		sync_fd(g_raw, "fsync the raw device");
		ps("hakios-pin: ");
		pd(count);
		ps(" blocks, the volume's from ");
		pd(first);
		ps(" on, of ");
		ps(file);
		ps(" hold zeros on the raw device, every piece read back and compared\n");
		leave(0, 0, 0);
	}

	if (streq(cmd, "guard")) {
		u64 first = 0, i;

		if (argc != 4 && argc != 5) {
			usage();
		}
		raw_arg = argv[3];
		if (argc == 5) {
			first = number(argv[4], "FIRST");
		}
		both(file, 0, 0, first);
		for (i = 0; i < n_ext; i++) {
			read_at(g_raw, piece, BS, ext[i].pblk * BS, "read the raw device");
			ps("guard ");
			pd(i);
			ps(" 0x");
			px(fnv(0xcbf29ce484222325ULL, piece, BS));
			read_at(g_raw, piece, BS, (ext[i].pblk + ext[i].n - 1) * BS, "read the raw device");
			px(fnv(0xcbf29ce484222325ULL, piece, BS));
			put("\n", 1);
		}
		leave(0, 0, 0);
	}

	if (streq(cmd, "mark")) {
		int k, zero = 1;

		if (argc != 4) {
			usage();
		}
		raw_arg = argv[3];
		both(file, 0, 0, 0);
		read_at(g_raw, piece, BS, ext[0].pblk * BS, "read the raw device");
		for (k = 0; k < 16; k++) {
			zero &= piece[MARKER_AT + k] == 0;
		}
		if (zero) {
			ps("marker none\n");
		} else {
			ps("marker 0x");
			for (k = 0; k < 16; k++) {
				char c[2] = { "0123456789abcdef"[piece[MARKER_AT + k] >> 4],
					     "0123456789abcdef"[piece[MARKER_AT + k] & 15] };

				put(c, 2);
			}
			put("\n", 1);
		}
		leave(0, 0, 0);
	}

	if (streq(cmd, "zeroraw") || streq(cmd, "rewrite")) {
		u64 done = 0, at, lblk, m;

		if (argc != 4) {
			usage();
		}
		raw_arg = argv[3];
		both(file, 1, cmd[0] == 'r', 0);
		raw = g_raw;
		if (cmd[0] == 'r') {
			fd = g_fd;
		}
		while ((m = next_piece(&at, &lblk)) != 0) {
			if (cmd[0] == 'r') {
				/* the plaintext, as Android's kernel decrypts it for us ... */
				read_at(fd, piece, m, lblk * BS, "read the file");
				/* ... onto the file's own blocks of the raw device, and back from there */
				put_piece(raw, piece, m, at);
			} else {
				memset(piece, 0, (unsigned long)m);
				write_at(raw, piece, m, at, "write the raw device");
			}
			done += m;
			progress(cmd[0] == 'r' ? "rewritten" : "zeroed", done, file_bytes);
		}
		sync_fd(raw, "fsync the raw device");
		ps("hakios-pin: ");
		pd(file_bytes / BS);
		ps(" blocks of ");
		ps(file);
		ps(cmd[0] == 'r' ? " are on the raw device in plaintext, every piece read back and compared\n"
				 : " hold zeros on the raw device\n");
		leave(0, 0, 0);
	}

	usage();
}

__asm__(
	".text\n"
	".globl _start\n"
	".type _start, %function\n"
	"_start:\n"
	"	mov x0, sp\n"          /* argc, then argv[] */
	"	bl cmain\n"
	"	mov x8, #93\n"         /* exit */
	"	svc #0\n"
);
