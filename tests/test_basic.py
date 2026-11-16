# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from post.core import Image, blur, extract_bright  # noqa: E402


def test_image_default():
    img = Image(4, 3)
    assert len(img.pixels) == 12
    assert img.at(1, 1) == (0, 0, 0)


def test_extract_bright_threshold():
    img = Image(3, 1, [(0, 0, 0), (200, 200, 200), (255, 255, 255)])
    out = extract_bright(img, 0.6)
    assert out.at(0, 0) == (0, 0, 0)


def test_blur_keeps_size():
    img = Image(8, 8)
    out = blur(img, 1)
    assert out.width == 8 and out.height == 8
