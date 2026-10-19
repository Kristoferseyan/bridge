import struct, zlib
from pathlib import Path

BG = (0x11, 0x12, 0x13)
BARS = [((0xff, 0x96, 0x6c), 0.62), ((0x4f, 0xd6, 0xbe), 0.44), ((0x82, 0xaa, 0xff), 0.53)]


def draw(size):
    px = [[BG] * size for _ in range(size)]
    left, h, gap = round(size * 0.22), round(size * 0.12), round(size * 0.08)
    top = (size - (3 * h + 2 * gap)) // 2
    r = h // 2
    for i, (color, frac) in enumerate(BARS):
        y0, x1 = top + i * (h + gap), left + round(size * frac)
        for y in range(y0, y0 + h):
            for x in range(left, x1):
                cx = left + r if x < left + r else (x1 - r - 1 if x > x1 - r - 1 else x)
                cy = y0 + r
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    px[y][x] = color
    raw = b"".join(b"\0" + bytes(c for p in line for c in p) for line in px)
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


out = Path(__file__).resolve().parent.parent / "static"
for s in (180, 512):
    (out / f"icon-{s}.png").write_bytes(draw(s))
