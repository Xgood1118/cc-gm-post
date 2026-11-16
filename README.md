# 后期处理链

骨架能跑通亮度提取、模糊、叠加、色调映射四步，但每一步都有量级错误。

## 已知缺口

- `extract_bright` 的阈值按 0 到 255 的直觉给出（默认 0.6），实际比较用的是 0 到 255 的
  亮度值，等于几乎全图都过阈值。
- `blur` 累加后不除以采样数，画面整体乘了方框内像素数，一圈模糊把图炸白。
- 边界直接跳过，不做边缘补值，四周比中心暗一圈。
- `composite` 直接相加不饱和，叠加后溢出 255 的像素在写 PNG 时被静默截断，彩色高光变白块。
- `tonemap` 先乘曝光再做 gamma，顺序反了，暗部被抬亮得过头。
- 没有做线性空间与显示空间的分离。

## 运行

```
python gen_samples.py
python -m post.cli data/frame.png --threshold 0.6 --radius 2 --exposure 1.0 --out post.png
python -m pytest tests -q
```

`frame.png` 是暗底两团亮斑，其中一团更小更亮，用来检验阈值是否真的只挑出亮部。
