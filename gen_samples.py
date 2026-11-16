# -*- coding: utf-8 -*-
"""生成一张高动态范围感的测试图：暗底上两团亮斑，一团更亮更小。"""
import os
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 240, 160


def main():
    raw = bytearray()
    for y in range(H):
        raw.append(0)
        for x in range(W):
            r = g = b = 12
            d1 = ((x - 70) ** 2 + (y - 80) ** 2) ** 0.5
            d2 = ((x - 170) ** 2 + (y - 60) ** 2) ** 0.5
            if d1 < 28:
                k = int(240 * (1 - d1 / 28.0))
                r, g, b = min(255, r + k), min(255, g + int(k * 0.7)), b
            if d2 < 14:
                k = int(255 * (1 - d2 / 14.0))
                r, g, b = min(255, r + k), min(255, g + k), min(255, b + int(k * 0.6))
            raw.extend((r, g, b))
    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body +
                struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
    os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
    path = os.path.join(HERE, "data", "frame.png")
    with open(path, "wb") as f:
        f.write(data)
    print("已生成", path, W, "x", H)


if __name__ == "__main__":
    main()
