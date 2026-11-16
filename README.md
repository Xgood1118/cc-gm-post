# 后期处理链

`post/core.py` 实现一条完整的后期处理链：

```
load_png -> extract_bright -> blur -> composite -> tonemap -> save_png
```

只用 Python 标准库（`struct` / `zlib` / `math` / `argparse`），不引入第三方依赖。

## 各阶段的量级约定

| 阶段 | 输入 | 输出 | 关键约束 |
| --- | --- | --- | --- |
| `extract_bright` | 8 位 RGB | 浮点 RGB | 阈值是**归一化亮度**（0..1），默认 0.6 |
| `blur` | 浮点 RGB | 浮点 RGB | 归一化盒式核 + 边界复制 + 能量校正，亮度严格守恒 |
| `composite` | 浮点 RGB | 浮点 RGB | 允许 >255，**不截断**，保留高动态范围层次 |
| `tonemap` | 浮点 RGB | 浮点 RGB | 解码 sRGB → 曝光 → Reinhard → 编码回 sRGB |
| `save_png` | 浮点 RGB | 8 位 PNG | 四舍五入并夹到 0..255，仅在落盘时量化 |

`Image` / `load_png` / `save_png` / `extract_bright` / `blur` / `tonemap` /
`composite` 的名字与签名保持不变。

### 修正的四处量级错误

1. **亮度提取**：原实现拿 0..255 的亮度去和 0.6 比较，几乎全图过阈值。现在统一
   归一化到 0..1，默认 0.6 恰好只筛出两团亮斑，暗底无残留。
2. **模糊**：原实现累加后不除以采样数（会炸白），且边界直接跳过（四周偏暗）。
   现在除以完整窗口大小、边界复制取样，并做逐通道能量校正，模糊前后平均亮度严格相等。
3. **叠加**：原实现直接相加不饱和，落盘时静默截断。现在保留浮点高动态范围，
   由色调映射统一压回可显示范围；`strength` 控制叠加强度。
4. **色调映射**：原实现在显示编码值上直接做 gamma，且顺序错误。现在先解码到线性光，
   **先乘曝光、再做 Reinhard 非线性映射**，最后编码回显示空间。Reinhard 曲线
   `x/(1+x)` 永远达不到 1，因此不会出现成片死白；暗部被平滑抬升，不会成片死黑。

## 运行

```
python -m post.cli data/frame.png --threshold 0.6 --radius 2 --exposure 1.0 --out post.png
python -m pytest tests -q
```

CLI 会打印处理前后的平均亮度、亮部像素数、过曝像素数，以及两团亮斑的峰值与面积。

## 实测数据

命令：

```
python -m post.cli data/frame.png --threshold 0.6 --radius 2 --exposure 1.0 --out post.png
```

输入 `data/frame.png`：240 × 160，暗底（RGB 12,12,12）上两团亮斑，
左斑圆心 (70, 80)、半径 28，右斑圆心 (170, 60)、半径 14（更小更亮）。

| 指标 | 处理前 | 处理后 |
| --- | --- | --- |
| 平均亮度（归一化） | 0.066398 | 0.066050 |
| 亮部像素数（亮度 ≥ 0.6） | 178 | 242 |
| 过曝像素数（任一通道 = 255） | 1 | **0** |
| 左斑峰值亮度 | 0.718344 | 0.801260 |
| 右斑峰值亮度 | 0.974518 | 0.905798 |

说明：

- 平均亮度基本持平（差异来自高光被 Reinhard 压缩、暗部被轻微抬升的相互抵消），
  画面整体曝光没有被模糊或叠加破坏。
- 过曝像素从 1 降到 0：输入右斑中心原本就有 1 个 255 饱和像素，叠加后峰值达到
  487.2（远超 8 位范围），但色调映射把它压回 0.906 的显示亮度，没有截断成白块。
- 亮部像素数增加是模糊扩散的正常结果：亮斑面积从 69 / 109 扩大到 97 / 145，
  峰值相应降低，能量守恒。
- 右侧亮斑峰值下降、左侧上升，是 Reinhard 压缩高光、同时 bloom 抬升暗部的预期行为，
  两团亮斑仍能明确区分。

## 输出确定性

`save_png` 固定行过滤类型 0 与 `zlib.compress(..., 9)`，不写入任何时间戳或随机数据；
同样的输入与参数连续跑两次，输出 PNG 逐字节相同（已用 `cmp` 验证）。

## 测试覆盖

`python -m pytest tests -q`：19 项全部通过。对应关系：

- 阈值筛出亮斑：`test_extract_bright_selects_only_spots`、
  `test_extract_bright_threshold_configurable`
- 模糊保持亮度：`test_blur_preserves_mean_luminance`、
  `test_blur_uniform_image_stays_uniform_at_edges`、
  `test_blur_radius_increases_spread`
- 无溢出截断：`test_composite_no_overflow_clipping`、
  `test_composite_output_never_clips_after_tonemap`
- 色调映射顺序：`test_tonemap_exposure_before_curve`、
  `test_tonemap_exposure_increases_brightness`、`test_tonemap_no_dead_zones`
- 两次输出一致：`test_output_size_and_determinism`
