# -*- coding: utf-8 -*-
"""后期处理链骨架。

亮度提取用固定阈值，模糊不做归一，色调映射在错误的色彩空间里做。
"""

import struct
import zlib


class Image(object):
    def __init__(self, width, height, pixels=None):
        self.width = int(width)
        self.height = int(height)
        self.pixels = pixels or [(0, 0, 0)] * (self.width * self.height)

    def at(self, x, y):
        return self.pixels[y * self.width + x]

    def set(self, x, y, rgb):
        self.pixels[y * self.width + x] = rgb


def load_png(path):
    with open(path, "rb") as f:
        data = f.read()
    pos = 8
    idat = b""
    width = height = None
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height = struct.unpack(">II", body[:8])
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    raw = zlib.decompress(idat)
    pixels = []
    stride = width * 3
    for y in range(height):
        ft = raw[y * (stride + 1)]
        line = raw[y * (stride + 1) + 1: y * (stride + 1) + 1 + stride]
        if ft != 0:
            raise ValueError("只支持无过滤的 PNG")
        for x in range(width):
            pixels.append(tuple(line[x * 3:x * 3 + 3]))
    return Image(width, height, pixels)


def save_png(img, path):
    raw = bytearray()
    for y in range(img.height):
        raw.append(0)
        for x in range(img.width):
            raw.extend(img.at(x, y))
    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body +
                struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", img.width, img.height, 8, 2, 0, 0, 0)
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(data)


def extract_bright(img, threshold=0.6):
    """提亮区。阈值按 0..255 的空间写，但比较时用的是 0..1 的值。"""
    out = Image(img.width, img.height)
    for y in range(img.height):
        for x in range(img.width):
            r, g, b = img.at(x, y)
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            if lum > threshold:
                out.set(x, y, (r, g, b))
    return out


def blur(img, radius=1):
    """盒式模糊。边界直接跳过，不做归一，也不补边。"""
    out = Image(img.width, img.height)
    for y in range(img.height):
        for x in range(img.width):
            sr = sg = sb = 0
            n = 0
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < img.width and 0 <= yy < img.height:
                        r, g, b = img.at(xx, yy)
                        sr += r
                        sg += g
                        sb += b
                        n += 1
            out.set(x, y, (sr, sg, sb))
    return out


def tonemap(img, exposure=1.0):
    """色调映射。直接在线性值上做 gamma，顺序错了。"""
    out = Image(img.width, img.height)
    for y in range(img.height):
        for x in range(img.width):
            r, g, b = img.at(x, y)
            rr = min(255, int(r * exposure))
            gg = min(255, int(g * exposure))
            bb = min(255, int(b * exposure))
            out.set(x, y, (int(rr ** 0.4545), int(gg ** 0.4545), int(bb ** 0.4545)))
    return out


def composite(base, layer, strength=1.0):
    """叠加。当前直接相加，没有做饱和处理。"""
    out = Image(base.width, base.height)
    for y in range(base.height):
        for x in range(base.width):
            a = base.at(x, y)
            b = layer.at(x, y)
            out.set(x, y, (a[0] + b[0], a[1] + b[1], a[2] + b[2]))
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="post")
    ap.add_argument("png")
    ap.add_argument("--threshold", type=float, default=0.6)
    ap.add_argument("--radius", type=int, default=2)
    ap.add_argument("--exposure", type=float, default=1.0)
    ap.add_argument("--out", default="post.png")
    args = ap.parse_args(argv)
    img = load_png(args.png)
    bright = extract_bright(img, args.threshold)
    bloom = blur(bright, args.radius)
    merged = composite(img, bloom)
    final = tonemap(merged, args.exposure)
    save_png(final, args.out)
    print("后期完成: %s" % args.out)
    return 0
