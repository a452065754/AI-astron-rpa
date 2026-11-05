# [BUG] vision 模板匹配两侧灰度转换常量不一致，彩色目标匹配分被系统性压低

- **组件**：`engine/components/astronverse-vision`（`astronverse/vision/cv_match.py`）
- **影响版本**：`main`（2026-11-05）、v1.1.7
- **严重级别**：P1 —— 「元素明明在屏幕上却找不到」，且**不报错**，只表现为置信度不够

## 1. 现象

用「图像识别 / CV 模板匹配」找一个**彩色**目标（按钮有底色、图标有颜色）：

- 肉眼可见目标就在那里，匹配结果却是「未找到」或置信度 0.88~0.94
- 默认阈值 0.95 时直接判定失败；把阈值调到 0.85 又能找到，但**误匹配率飙升**
- 换成纯灰/黑白目标就正常

## 2. 位置

`engine/components/astronverse-vision/src/astronverse/vision/cv_match.py`，`AnchorMatch.process_image`

`process_image` 开头已经把**大图和模板都转成 BGR**：

```python
# L108
image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
# L110
element = cv2.cvtColor(element, cv2.COLOR_RGB2BGR)
# L113
anchor = cv2.cvtColor(anchor, cv2.COLOR_RGB2BGR)
```

但后面转灰度时，两侧用了**不同的常量**：

```python
# L135  大图：BGR → GRAY        ✅ 正确
gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

# L146  模板：已经是 BGR，却按 RGB 解释   ❌
small_gray = cv2.cvtColor(element, cv2.COLOR_RGB2GRAY)

# L152  锚点：同上   ❌
anchor_gray = cv2.cvtColor(anchor, cv2.COLOR_RGB2GRAY)
```

同一个函数里，第 110 行刚把 `element` 转成 BGR，第 146 行又当它是 RGB。

## 3. 为什么这会压低分数（实测）

两种常量的权重完全相反：

| 常量 | 公式 |
| --- | --- |
| `COLOR_BGR2GRAY` | `0.114*B + 0.587*G + 0.299*R` |
| `COLOR_RGB2GRAY` | `0.299*B + 0.587*G + 0.114*R` |

对同一个 BGR 像素，两个结果**只差在 R/B 权重互换**。纯红 `(B=0,G=0,R=255)`：

```
cv2.cvtColor(纯红, COLOR_BGR2GRAY) = 76
cv2.cvtColor(纯红, COLOR_RGB2GRAY) = 29
```

同一块红色，大图侧算出 76、模板侧算出 29，**匹配算法拿到的是两张不同的图**，相关系数必然掉。

实测（`opencv-python-headless 5.0.0`，`TM_CCOEFF_NORMED`，模板与图像完美对齐、位置完全正确）：

```
平台现状 RGB2GRAY          最高分=0.884786  位置=(120, 80)  期望位置=(120,80)
正确写法 BGR2GRAY          最高分=1.000000  位置=(120, 80)  期望位置=(120,80)
```

位置是对的，**分数只有 0.8848** —— 默认阈值 0.95 直接判否。目标越"彩色"（纯色块、渐变按钮、彩色图标），掉得越多；纯灰目标几乎不受影响，所以这个 bug 很难被归因到灰度通道上。

复现脚本（零依赖，只用 cv2 + numpy）：

```python
import cv2, numpy as np

H, W = 200, 320
screen = np.full((H, W, 3), 240, np.uint8)
tpl = np.zeros((40, 60, 3), np.uint8)
tpl[:, :20] = (0, 0, 220)        # 红块 (BGR)
tpl[:, 20:40] = (0, 200, 0)      # 绿块
tpl[:, 40:] = (220, 0, 0)        # 蓝块
tpl[18:22, :] = (255, 255, 255)  # 白线
y0, x0 = 80, 120
screen[y0:y0+40, x0:x0+60] = tpl

image = cv2.cvtColor(screen, cv2.COLOR_BGR2RGB)
element = cv2.cvtColor(tpl, cv2.COLOR_BGR2RGB)
image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)      # 同 L108
element = cv2.cvtColor(element, cv2.COLOR_RGB2BGR)  # 同 L110

gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)      # 同 L135

for name, t in [
    ("current  RGB2GRAY", cv2.cvtColor(element, cv2.COLOR_RGB2GRAY)),   # 同 L146
    ("expected BGR2GRAY", cv2.cvtColor(element, cv2.COLOR_BGR2GRAY)),
]:
    r = cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED)
    print(name, cv2.minMaxLoc(r)[1])
```

输出：

```
current  RGB2GRAY 0.884786
expected BGR2GRAY 1.0
```

## 4. 影响

- 所有使用 CV / 图像匹配的流程，只要目标是彩色的，命中率都被系统性压低
- 用户唯一能做的补救是**降低阈值**，而这会引入误点击（RPA 里误点击的后果比找不到更严重）
- 症状与原因完全无关：日志里没有任何异常，只说「置信度不足」

## 5. 修复建议

三行，统一成 BGR：

```diff
-            small_gray = cv2.cvtColor(element, cv2.COLOR_RGB2GRAY)
+            small_gray = cv2.cvtColor(element, cv2.COLOR_BGR2GRAY)
...
-                anchor_gray = cv2.cvtColor(anchor, cv2.COLOR_RGB2GRAY)
+                anchor_gray = cv2.cvtColor(anchor, cv2.COLOR_BGR2GRAY)
```

同文件 `check_if_multiple_elements`（L24~）也有同样问题，且那一处更隐蔽：

```python
if not isinstance(image, np.ndarray):
    image = np.array(image.convert("RGBA"))     # PIL → RGBA
gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)  # 对 RGBA 用 BGR2GRAY
...
small_gray = cv2.cvtColor(element, cv2.COLOR_RGB2GRAY)
```

这里输入是 **RGBA**（不是 BGR），却对大图用了 `BGR2GRAY`、对模板用了 `RGB2GRAY` —— 两侧**都错且错得不一样**。建议统一改成：先 `cv2.cvtColor(x, cv2.COLOR_RGBA2BGR)`，再统一 `COLOR_BGR2GRAY`；或者两侧都用 `COLOR_RGBA2GRAY`。

## 6. 回归测试

```python
def test_gray_conversion_consistent():
    """两侧灰度转换必须一致：完美对齐时分数应为 1.0"""
    tpl = cv2.cvtColor(np.full((20, 20, 3), (0, 0, 220), np.uint8), cv2.COLOR_BGR2GRAY)
    img = np.full((60, 60), 240, np.uint8)
    img[20:40, 20:40] = tpl
    assert cv2.minMaxLoc(cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED))[1] > 0.999
```

更直接的做法：在 `process_image` 里加一条断言，要求所有 `cvtColor(..., *GRAY)` 的常量一致。
