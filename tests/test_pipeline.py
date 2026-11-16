# -*- coding: utf-8 -*-
"""后期处理链的行为测试。"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from post.core import (  # noqa: E402
    Image,
    blur,
    bright_spots,
    composite,
    display_luminance,
    extract_bright,
    image_stats,
    load_png,
    main,
    save_png,
    tonemap,
)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAME = os.path.join(HERE, "data", "frame.png")

# frame.png 里两团亮斑的圆心与半径（见 gen_samples.py）
SPOT_A = (70, 80, 28)
SPOT_B = (170, 60, 14)


def _in_spot(x, y):
    for cx, cy, r in (SPOT_A, SPOT_B):
        if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
            return True
    return False


# ---------------------------------------------------------------------------
# 1. 亮度提取
# ---------------------------------------------------------------------------

def test_extract_bright_selects_only_spots():
    img = load_png(FRAME)
    out = extract_bright(img, 0.6)
    chosen = [(x, y) for y in range(img.height) for x in range(img.width)
              if out.at(x, y) != (0, 0, 0)]
    assert chosen, "默认阈值下应当筛出亮斑"
    leaked = [(x, y) for x, y in chosen if not _in_spot(x, y)]
    assert leaked == [], "亮斑之外不应有残留，泄漏了 %d 个像素" % len(leaked)
    # 两团亮斑都要被覆盖（中心附近至少有像素过阈值）
    for cx, cy, _ in (SPOT_A, SPOT_B):
        assert any((x - cx) ** 2 + (y - cy) ** 2 <= 4 for x, y in chosen)


def test_extract_bright_threshold_configurable():
    img = load_png(FRAME)
    low = extract_bright(img, 0.4)
    high = extract_bright(img, 0.85)
    count_low = sum(1 for p in low.pixels if p != (0, 0, 0))
    count_high = sum(1 for p in high.pixels if p != (0, 0, 0))
    assert count_high < count_low, "阈值提高后亮部像素应当减少"
    # 高阈值仍是低阈值的子集
    for a, b in zip(low.pixels, high.pixels):
        if b != (0, 0, 0):
            assert a == b


def test_extract_bright_threshold_range_checked():
    img = Image(1, 1, [(255, 255, 255)])
    try:
        extract_bright(img, 1.5)
    except ValueError:
        pass
    else:
        raise AssertionError("越界阈值应当报错")


# ---------------------------------------------------------------------------
# 2. 模糊
# ---------------------------------------------------------------------------

def test_blur_preserves_mean_luminance():
    img = load_png(FRAME)
    layer = extract_bright(img, 0.6)
    out = blur(layer, 3)
    before = image_stats(layer)["mean_luminance"]
    after = image_stats(out)["mean_luminance"]
    assert abs(before - after) < 1e-9, "模糊前后整体亮度必须一致"


def test_blur_uniform_image_stays_uniform_at_edges():
    img = Image(9, 7, [(100, 120, 140)] * 63)
    out = blur(img, 3)
    ref = out.at(4, 3)
    for y in range(img.height):
        for x in range(img.width):
            assert out.at(x, y) == ref, "边界不应比中心暗"


def test_blur_radius_increases_spread():
    img = load_png(FRAME)
    layer = extract_bright(img, 0.6)
    small = blur(layer, 1)
    large = blur(layer, 4)
    n_small = sum(1 for p in small.pixels if max(p) > 0)
    n_large = sum(1 for p in large.pixels if max(p) > 0)
    assert n_large > n_small, "半径增大时亮斑扩散范围应随之增大"
    # 扩散后的峰值应低于原峰值
    assert max(display_luminance(p) for p in large.pixels) < \
        max(display_luminance(p) for p in small.pixels)


# ---------------------------------------------------------------------------
# 3. 叠加
# ---------------------------------------------------------------------------

def test_composite_no_overflow_clipping():
    base = Image(2, 1, [(200, 10, 10), (0, 0, 0)])
    layer = Image(2, 1, [(200, 10, 10), (0, 0, 0)])
    out = composite(base, layer, 1.0)
    assert out.at(0, 0)[0] == 400.0, "叠加应保留超过 255 的浮点值，不能截断"
    assert out.at(0, 0)[1] == 20.0


def test_composite_strength_configurable_and_keeps_detail():
    base = Image(2, 1, [(10, 10, 10), (200, 200, 200)])
    layer = Image(2, 1, [(50, 50, 50), (100, 100, 100)])
    half = composite(base, layer, 0.5)
    assert half.at(0, 0) == (35.0, 35.0, 35.0)
    assert half.at(1, 0) == (250.0, 250.0, 250.0)
    # 暗部与亮部仍可区分
    assert half.at(0, 0)[0] < half.at(1, 0)[0]


def test_composite_output_never_clips_after_tonemap():
    base = Image(1, 1, [(255, 255, 255)])
    layer = Image(1, 1, [(255, 255, 255)])
    out = tonemap(composite(base, layer, 1.0), 1.0)
    r, g, b = out.at(0, 0)
    assert max(r, g, b) < 255.0, "色调映射后不应有死白"
    assert min(r, g, b) > 0.0, "高光映射后不应被压成死黑"


# ---------------------------------------------------------------------------
# 4. 色调映射
# ---------------------------------------------------------------------------

def _srgb_decode(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _srgb_encode(v):
    return 12.92 * v if v <= 0.0031308 else 1.055 * (v ** (1 / 2.4)) - 0.055


def test_tonemap_exposure_before_curve():
    """曝光必须作用在线性光上，然后才做 Reinhard 曲线。"""
    value = 128
    img = Image(1, 1, [(value, value, value)])
    out = tonemap(img, exposure=2.0)
    lin = _srgb_decode(value / 255.0) * 2.0
    expected = _srgb_encode(lin / (1.0 + lin)) * 255.0
    assert abs(out.at(0, 0)[0] - expected) < 1e-9

    # 若顺序反了（先映射/伽马再乘曝光），结果会明显不同
    wrong_lin = _srgb_decode(value / 255.0)
    wrong = _srgb_encode(wrong_lin / (1.0 + wrong_lin)) * 255.0 * 2.0
    assert abs(out.at(0, 0)[0] - wrong) > 1.0


def test_tonemap_exposure_increases_brightness():
    img = load_png(FRAME)
    base = composite(img, blur(extract_bright(img, 0.6), 2), 1.0)
    dim = image_stats(tonemap(base, 0.5))["mean_luminance"]
    normal = image_stats(tonemap(base, 1.0))["mean_luminance"]
    bright = image_stats(tonemap(base, 2.0))["mean_luminance"]
    assert dim < normal < bright


def test_tonemap_no_dead_zones():
    img = load_png(FRAME)
    merged = composite(img, blur(extract_bright(img, 0.6), 2), 1.0)
    out = tonemap(merged, 1.0)
    assert image_stats(out)["overexposed_pixels"] == 0, "不应出现成片死白"
    values = [c for p in out.pixels for c in p]
    assert min(values) > 0, "不应出现死黑"
    # 暗部依然保留层次（不只一个灰度级）
    dark = {round(display_luminance(p), 6) for p in out.pixels
            if display_luminance(p) < 0.2}
    assert len(dark) > 1


# ---------------------------------------------------------------------------
# 5. 输出与统计
# ---------------------------------------------------------------------------

def test_output_size_and_determinism(tmp_path):
    src = load_png(FRAME)
    out1 = str(tmp_path / "a.png")
    out2 = str(tmp_path / "b.png")
    for path in (out1, out2):
        main([FRAME, "--threshold", "0.6", "--radius", "2",
              "--exposure", "1.0", "--out", path])
    assert load_png(out1).width == src.width
    assert load_png(out1).height == src.height
    with open(out1, "rb") as f:
        a = f.read()
    with open(out2, "rb") as f:
        b = f.read()
    assert a == b, "连续两次输出必须逐字节相同"


def test_stats_before_after():
    img = load_png(FRAME)
    before = image_stats(img, 0.6)
    after = image_stats(tonemap(composite(img, blur(extract_bright(img, 0.6), 2),
                                         1.0), 1.0), 0.6)
    for stats in (before, after):
        assert 0.0 <= stats["mean_luminance"] <= 1.0
        assert stats["bright_pixels"] >= 0
        assert stats["overexposed_pixels"] >= 0
    assert before["mean_luminance"] != after["mean_luminance"]


def test_bright_spots_detects_two_blobs():
    img = load_png(FRAME)
    spots = bright_spots(img, 0.6)
    assert len(spots) == 2, "应当恰好检测到两团亮斑"
    assert abs(spots[0]["center"][0] - 70) < 3
    assert abs(spots[1]["center"][0] - 170) < 3
    # 右侧那团更亮
    assert spots[1]["peak_luminance"] > spots[0]["peak_luminance"]
    # 右侧那团物理上更小：用“高于暗底”的支撑面积比较（过阈值面积受亮度影响，
    # 右侧三通道同时亮，过阈值面积反而更大，不能用来判断大小）。
    background = display_luminance((12, 12, 12))
    left_support = sum(1 for y in range(img.height) for x in range(0, 120)
                       if display_luminance(img.at(x, y)) > background + 1e-9)
    right_support = sum(1 for y in range(img.height) for x in range(120, 240)
                        if display_luminance(img.at(x, y)) > background + 1e-9)
    assert right_support < left_support


def test_png_roundtrip(tmp_path):
    img = Image(3, 2, [(0, 0, 0), (255, 255, 255), (12, 34, 56),
                       (200, 100, 50), (1, 2, 3), (250, 240, 230)])
    path = str(tmp_path / "rt.png")
    save_png(img, path)
    back = load_png(path)
    assert (back.width, back.height) == (3, 2)
    assert [tuple(int(v) for v in p) for p in back.pixels] == img.pixels
