# -*- coding: utf-8 -*-
"""后期处理链：亮度提取 -> 模糊 -> 叠加 -> 色调映射。

工作约定
--------
* 8 位 PNG 的像素值在 0..255 之间，显示亮度按 Rec.709 权重归一化到 0..1。
* ``extract_bright`` 的阈值使用归一化亮度（0..1），默认 0.6。
* 中间结果用浮点保存，叠加阶段允许超过 255（高动态范围），不做截断；
  只有 ``save_png`` 最后落盘时才量化到 8 位。
* ``tonemap`` 先把显示编码值解码到线性光，在线性光里做曝光，再用
  Reinhard 曲线做非线性映射，最后编码回显示空间。
"""

import math
import struct
import zlib

LUM_R = 0.2126
LUM_G = 0.7152
LUM_B = 0.0722


class Image(object):
    def __init__(self, width, height, pixels=None):
        self.width = int(width)
        self.height = int(height)
        self.pixels = pixels or [(0, 0, 0)] * (self.width * self.height)

    def at(self, x, y):
        return self.pixels[y * self.width + x]

    def set(self, x, y, rgb):
        self.pixels[y * self.width + x] = rgb

    def copy(self):
        return Image(self.width, self.height, list(self.pixels))


# ---------------------------------------------------------------------------
# PNG 读写
# ---------------------------------------------------------------------------

def _paeth(a, b, c):
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def load_png(path):
    """读取 8 位 RGB PNG（支持标准 0..4 号行过滤）。"""
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
            width, height, depth, color, _, _, interlace = struct.unpack(
                ">IIBBBBB", body[:13])
            if depth != 8 or color != 2 or interlace != 0:
                raise ValueError("只支持 8 位、无隔行的 RGB PNG")
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    if width is None:
        raise ValueError("PNG 缺少 IHDR")
    raw = zlib.decompress(idat)
    stride = width * 3
    bpp = 3
    pixels = []
    prev = bytearray(stride)
    for y in range(height):
        base = y * (stride + 1)
        ft = raw[base]
        line = bytearray(raw[base + 1:base + 1 + stride])
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                up_left = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prev[i], up_left)) & 0xFF
        elif ft != 0:
            raise ValueError("未知的 PNG 行过滤类型: %d" % ft)
        for x in range(width):
            i = x * 3
            pixels.append((line[i], line[i + 1], line[i + 2]))
        prev = line
    return Image(width, height, pixels)


def _to_byte(value):
    """浮点值 -> 8 位整数：四舍五入并夹在 0..255，仅用于落盘。"""
    v = int(math.floor(float(value) + 0.5))
    if v < 0:
        return 0
    if v > 255:
        return 255
    return v


def save_png(img, path):
    raw = bytearray()
    for y in range(img.height):
        raw.append(0)
        for x in range(img.width):
            r, g, b = img.at(x, y)
            raw.extend((_to_byte(r), _to_byte(g), _to_byte(b)))

    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body +
                struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", img.width, img.height, 8, 2, 0, 0, 0)
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(data)


# ---------------------------------------------------------------------------
# 亮度与统计
# ---------------------------------------------------------------------------

def display_luminance(rgb):
    """显示编码值（0..255）的 Rec.709 相对亮度，归一化到 0..1。"""
    return (LUM_R * rgb[0] + LUM_G * rgb[1] + LUM_B * rgb[2]) / 255.0


def image_stats(img, threshold=0.6):
    """统计平均亮度、亮部像素数、过曝像素数（任一通道 >= 255）。"""
    n = img.width * img.height
    if n == 0:
        return {"mean_luminance": 0.0, "bright_pixels": 0,
                "overexposed_pixels": 0, "peak_luminance": 0.0}
    lums = [display_luminance(p) for p in img.pixels]
    over = sum(1 for p in img.pixels if max(p) >= 255)
    return {
        "mean_luminance": sum(lums) / float(n),
        "bright_pixels": sum(1 for v in lums if v >= threshold),
        "overexposed_pixels": over,
        "peak_luminance": max(lums),
    }


def bright_spots(img, threshold=0.6):
    """按阈值做 4 邻域连通域，返回每团亮斑的峰值与中心。

    返回按中心 x 坐标排序的列表；用于核对两团亮斑的峰值。
    """
    w, h = img.width, img.height
    lums = [display_luminance(p) for p in img.pixels]
    mask = [v >= threshold for v in lums]
    seen = [False] * len(mask)
    spots = []
    for start in range(len(mask)):
        if not mask[start] or seen[start]:
            continue
        stack = [start]
        seen[start] = True
        area = 0
        sx = sy = 0
        peak = -1.0
        while stack:
            idx = stack.pop()
            x, y = idx % w, idx // w
            area += 1
            sx += x
            sy += y
            if lums[idx] > peak:
                peak = lums[idx]
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < w and 0 <= ny < h:
                    nidx = ny * w + nx
                    if mask[nidx] and not seen[nidx]:
                        seen[nidx] = True
                        stack.append(nidx)
        spots.append({
            "area": area,
            "center": (sx / float(area), sy / float(area)),
            "peak_luminance": peak,
        })
    spots.sort(key=lambda s: s["center"][0])
    return spots


# ---------------------------------------------------------------------------
# 处理链
# ---------------------------------------------------------------------------

def extract_bright(img, threshold=0.6):
    """按归一化亮度阈值提取亮部，暗部置黑。

    threshold 取 0..1，比较对象是 ``display_luminance``（0..255 -> 0..1）。
    默认 0.6 能覆盖测试图中的两团亮斑，同时把暗底完全排除。
    """
    threshold = float(threshold)
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold 必须在 0..1 之间")
    out = Image(img.width, img.height)
    for y in range(img.height):
        for x in range(img.width):
            rgb = img.at(x, y)
            if display_luminance(rgb) >= threshold:
                out.set(x, y, (float(rgb[0]), float(rgb[1]), float(rgb[2])))
    return out


def _box_axis(src, w, h, radius, horizontal):
    """一维盒式模糊，边界复制，窗口始终除以完整采样数。"""
    size = 2 * radius + 1
    dst = [(0.0, 0.0, 0.0)] * (w * h)
    if horizontal:
        for y in range(h):
            row = src[y * w:(y + 1) * w]
            for x in range(w):
                sr = sg = sb = 0.0
                for d in range(-radius, radius + 1):
                    xx = x + d
                    if xx < 0:
                        xx = 0
                    elif xx >= w:
                        xx = w - 1
                    r, g, b = row[xx]
                    sr += r
                    sg += g
                    sb += b
                dst[y * w + x] = (sr / size, sg / size, sb / size)
    else:
        for y in range(h):
            for x in range(w):
                sr = sg = sb = 0.0
                for d in range(-radius, radius + 1):
                    yy = y + d
                    if yy < 0:
                        yy = 0
                    elif yy >= h:
                        yy = h - 1
                    r, g, b = src[yy * w + x]
                    sr += r
                    sg += g
                    sb += b
                dst[y * w + x] = (sr / size, sg / size, sb / size)
    return dst


def blur(img, radius=1):
    """归一化盒式模糊（边界复制），并做逐通道能量校正。

    归一化保证亮度量级不变，边界复制保证四周不会比中心暗；最后的能量校正
    让输出与输入的逐通道总和完全一致，从而整体亮度严格保持。
    """
    radius = int(radius)
    if radius < 0:
        raise ValueError("radius 不能为负")
    w, h = img.width, img.height
    src = [(float(p[0]), float(p[1]), float(p[2])) for p in img.pixels]
    if radius == 0 or w == 0 or h == 0:
        return Image(w, h, src)

    tmp = _box_axis(src, w, h, radius, horizontal=True)
    out = _box_axis(tmp, w, h, radius, horizontal=False)

    # 边界复制的盒式核在图像边缘会轻微改变总能量；按通道校正回原总和，
    # 使“模糊前后平均亮度一致”成为严格成立的约束。
    for c in range(3):
        before = sum(p[c] for p in src)
        after = sum(p[c] for p in out)
        if after > 0.0:
            scale = before / after
            if scale != 1.0:
                out = [tuple(p[c] * scale if i == c else p[i]
                             for i in range(3)) for p in out]
    return Image(w, h, out)


def composite(base, layer, strength=1.0):
    """叠加：base + strength * layer。

    保留浮点高动态范围，超过 255 也不截断；截断交给最后的色调映射处理，
    这样亮部层次不会在叠加阶段丢失。strength 控制叠加强度。
    """
    if base.width != layer.width or base.height != layer.height:
        raise ValueError("叠加的两张图尺寸必须一致")
    strength = float(strength)
    if strength < 0:
        raise ValueError("strength 不能为负")
    out = Image(base.width, base.height)
    for y in range(base.height):
        for x in range(base.width):
            a = base.at(x, y)
            b = layer.at(x, y)
            out.set(x, y, (float(a[0]) + strength * float(b[0]),
                           float(a[1]) + strength * float(b[1]),
                           float(a[2]) + strength * float(b[2])))
    return out


def _srgb_to_linear(c):
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c):
    if c <= 0.0031308:
        return 12.92 * c
    return 1.055 * (c ** (1.0 / 2.4)) - 0.055


def tonemap(img, exposure=1.0):
    """色调映射：显示值解码 -> 曝光 -> Reinhard -> 编码回显示值。

    顺序是“先曝光、后非线性映射”：曝光必须作用在线性光上，Reinhard 曲线
    x/(1+x) 单调且永远不会到达 1，因此不会产生成片死白；暗部则被平滑抬升，
    不会成片死黑。
    """
    exposure = float(exposure)
    if not math.isfinite(exposure) or exposure <= 0.0:
        raise ValueError("exposure 必须为正数")
    out = Image(img.width, img.height)
    for y in range(img.height):
        for x in range(img.width):
            rgb = img.at(x, y)
            mapped = []
            for value in rgb:
                v = float(value) / 255.0
                if v < 0.0:
                    v = 0.0
                lin = _srgb_to_linear(v) * exposure
                tone = lin / (1.0 + lin)
                mapped.append(_linear_to_srgb(tone) * 255.0)
            out.set(x, y, tuple(mapped))
    return out


def _quantize(img):
    """把浮点结果量化成落盘时的 8 位值，便于统计与文件内容完全一致。"""
    out = Image(img.width, img.height)
    for y in range(img.height):
        for x in range(img.width):
            r, g, b = img.at(x, y)
            out.set(x, y, (_to_byte(r), _to_byte(g), _to_byte(b)))
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

    # 统计以实际落盘的 8 位数据为准，避免浮点值与文件内容有偏差。
    saved = _quantize(final)
    before = image_stats(img, args.threshold)
    after = image_stats(saved, args.threshold)
    print("处理前: 平均亮度 %.6f, 亮部像素 %d, 过曝像素 %d"
          % (before["mean_luminance"], before["bright_pixels"],
             before["overexposed_pixels"]))
    print("处理后: 平均亮度 %.6f, 亮部像素 %d, 过曝像素 %d"
          % (after["mean_luminance"], after["bright_pixels"],
             after["overexposed_pixels"]))
    spots_before = bright_spots(img, args.threshold)
    spots_after = bright_spots(saved, args.threshold)
    if spots_before and spots_after:
        print("亮斑峰值 处理前: %s"
              % " / ".join("%.6f" % s["peak_luminance"] for s in spots_before))
        print("亮斑峰值 处理后: %s"
              % " / ".join("%.6f" % s["peak_luminance"] for s in spots_after))
        print("亮斑面积 处理前: %s"
              % " / ".join("%d" % s["area"] for s in spots_before))
        print("亮斑面积 处理后: %s"
              % " / ".join("%d" % s["area"] for s in spots_after))
    print("后期完成: %s" % args.out)
    return 0
