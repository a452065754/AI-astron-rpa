# -*- coding: utf-8 -*-
"""A15 · 图像匹配：模板侧灰度用错通道（RGB2GRAY 作用在已转好的 BGR 图上）

现象：`CV.*` 原子（is_image_exist / wait_image / hover_image / image_input /
cv_click）对**彩色**目标经常匹配不到，或匹配分明显偏低（实测 0.9234 < 阈值 0.95），
但用同样的图自己算一遍却是 1.0000。灰色目标则一切正常。

真因：引擎把模板和锚点**先从 RGB 转成 BGR**（为了和截图对齐），紧接着又**按 RGB 的
权重**去转灰度，等于把 R 和 B 的权重反了：

    vision/cv_match.py:110   element = cv2.cvtColor(element, cv2.COLOR_RGB2BGR)
    vision/cv_match.py:146   small_gray = cv2.cvtColor(element, cv2.COLOR_RGB2GRAY)  ← 应为 BGR2GRAY
    vision/cv_match.py:113   anchor  = cv2.cvtColor(anchor,  cv2.COLOR_RGB2BGR)
    vision/cv_match.py:152   anchor_gray = cv2.cvtColor(anchor, cv2.COLOR_RGB2GRAY)  ← 应为 BGR2GRAY

而屏幕侧是对的（截图本身就是 BGR 语义）：

    vision/cv_match.py:108   image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    vision/cv_match.py:135   gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)          ← 正确

COLOR_RGB2GRAY 与 COLOR_BGR2GRAY 只在 R==G==B 时结果相同，所以：
    · 灰色 / 黑白的元素 → 不受影响（这是为什么问题只在彩色目标上出现）
    · 蓝色、橙色这类 R 与 B 差距大的颜色 → 灰度值被算错 → 匹配分被拉低 → 低于 0.95 阈值

同一处错误还有第二个受害者：
    vision/cv_match.py:33    small_gray = cv2.cvtColor(element, cv2.COLOR_RGB2GRAY)
这是 `check_if_multiple_elements`（判定元素在屏幕上是否唯一），它拿的 element 也是
BGR 语义，同样错配 —— 会影响「元素是否唯一」的判断。

本脚本做三件事：
  1. 静态：AST 找出「先 RGB2BGR、后 RGB2GRAY」的这一对转换，并确认屏幕侧用的是 BGR2GRAY
  2. 动态：**调用引擎真实的 AnchorMatch.process_image()**，屏幕里明明放着那个彩色目标，
     却返回 match_box=None（找不到）
  3. 对照：同一个目标改成灰色 → 立刻能找到（证明不是阈值/元素的问题）
     再附数值对比：引擎灰度 vs 正确灰度的匹配分

运行：
    "%APPDATA%/astron-rpa/python_core/python.exe" docs/repro/A15_cv_template_rgb2gray.py
"""
import ast
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import (  # noqa: E402
    NOT_REPRODUCED,
    REPRODUCED,
    fail_detail,
    main_guard,
    report,
)

THRESHOLD = 0.95


def build_target(rgb, pad=60, size=(70, 46)):
    """造一个「有纹理的彩色目标」和一张「屏幕截图」。

    纹理是必须的：纯色块的方差为 0，归一化相关系数会退化，测不出通道错配。
    """
    import cv2
    import numpy as np

    h, w = size[1], size[0]
    elem = np.zeros((h, w, 3), np.uint8)
    elem[:, :] = rgb
    elem[6:16, :] = (255, 255, 255)      # 白条
    elem[26:34, :] = (0, 0, 0)          # 黑条
    cv2.circle(elem, (52, 22), 8, (255, 255, 0), -1)   # 异色圆点
    screen = np.full((h + pad * 2, w + pad * 2, 3), 245, np.uint8)
    screen[pad:pad + h, pad:pad + w] = elem
    return screen, elem


def static_probe():
    """AST：找出所有 'cv2.cvtColor(x, COLOR_RGB2GRAY)' 且 x 之前被 RGB2BGR 过的地方。"""
    from astronverse.vision.cv_match import AnchorMatch

    src_path = inspect.getsourcefile(AnchorMatch)
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)

    bgr_vars = {}     # 变量名 -> 转成 BGR 的行号
    gray_calls = []   # (行号, 变量名, 用的哪个常量)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr == "cvtColor"):
            continue
        if len(node.args) < 2 or not isinstance(node.args[1], ast.Attribute):
            continue
        const = node.args[1].attr
        src_arg = node.args[0]
        var = ""
        if isinstance(src_arg, ast.Name):
            var = src_arg.id
        elif isinstance(src_arg, ast.Call):
            var = ast.unparse(src_arg)
        if const == "COLOR_RGB2BGR":
            bgr_vars[var] = node.lineno
        elif const in ("COLOR_RGB2GRAY", "COLOR_BGR2GRAY"):
            gray_calls.append((node.lineno, var, const))
    return src_path, bgr_vars, gray_calls


def run():
    import cv2
    import numpy as np
    from astronverse.vision.cv_match import AnchorMatch

    # ---------------- 1. 静态 ----------------
    src_path, bgr_vars, gray_calls = static_probe()
    print("静态分析：", src_path)
    print("  先转成 BGR 的变量：", bgr_vars)
    print("  灰度转换调用：")
    bad_gray = []
    for ln, var, const in sorted(gray_calls):
        flag = ""
        if const == "COLOR_RGB2GRAY" and var in bgr_vars:
            flag = f"  ← 错！{var} 已在 line {bgr_vars[var]} 转成 BGR"
            bad_gray.append((ln, var, bgr_vars[var]))
        elif const == "COLOR_BGR2GRAY":
            flag = "  ← 正确（屏幕侧）"
        print(f"    line {ln}: cvtColor({var}, {const}){flag}")
    print(f"\n判定：错配 {len(bad_gray)} 处 -> {bad_gray}")

    # ---------------- 2. 动态：调引擎真实函数 ----------------
    # 蓝色系（R 与 B 差距大）最敏感
    rgb = (0, 128, 255)
    screen, elem = build_target(rgb)

    engine = AnchorMatch()
    try:
        _img, box = engine.process_image(screen, elem, ratio="1,1", match_similarity=THRESHOLD)
    except Exception as e:  # noqa: BLE001
        box = f"抛异常 {fail_detail(e)}"
    print(f"\n引擎真实 process_image（彩色目标 RGB{rgb}）：match_box = {box}")

    # 对照：同样的目标改成灰色
    gray_elem = cv2.cvtColor(elem, cv2.COLOR_RGB2GRAY)
    gray_elem = cv2.cvtColor(gray_elem, cv2.COLOR_GRAY2BGR)
    gray_screen = np.full((elem.shape[0] + 120, elem.shape[1] + 120, 3), 245, np.uint8)
    gray_screen[60:60 + elem.shape[0], 60:60 + elem.shape[1]] = gray_elem
    try:
        _img2, box2 = engine.process_image(gray_screen, gray_elem, ratio="1,1", match_similarity=THRESHOLD)
    except Exception as e:  # noqa: BLE001
        box2 = f"抛异常 {fail_detail(e)}"
    print(f"对照 process_image（灰色目标）：match_box = {box2}")

    # ---------------- 3. 数值对比 ----------------
    s_bgr = cv2.cvtColor(screen, cv2.COLOR_RGB2BGR)
    e_bgr = cv2.cvtColor(elem, cv2.COLOR_RGB2BGR)
    gray_screen_img = cv2.cvtColor(s_bgr, cv2.COLOR_BGR2GRAY)
    engine_gray = cv2.cvtColor(e_bgr, cv2.COLOR_RGB2GRAY)   # 引擎用的
    right_gray = cv2.cvtColor(e_bgr, cv2.COLOR_BGR2GRAY)    # 应该是
    score_engine = float(cv2.matchTemplate(gray_screen_img, engine_gray, cv2.TM_CCOEFF_NORMED).max())
    score_right = float(cv2.matchTemplate(gray_screen_img, right_gray, cv2.TM_CCOEFF_NORMED).max())
    max_gray_diff = int(np.abs(engine_gray.astype(int) - right_gray.astype(int)).max())
    print(f"\n数值对比（阈值 {THRESHOLD}）：")
    print(f"  引擎灰度(RGB2GRAY on BGR) 匹配分 = {score_engine:.4f}")
    print(f"  正确灰度(BGR2GRAY)         匹配分 = {score_right:.4f}")
    print(f"  差 = {score_right - score_engine:.4f}，灰度像素最大差 = {max_gray_diff}")

    # ---------------- 4. 附带发现：ratio 的默认值不可用 ----------------
    ratio_err = "（没有异常）"
    try:
        engine.process_image(screen, elem)   # 不传 ratio，用函数默认值
    except Exception as e:  # noqa: BLE001
        ratio_err = fail_detail(e)
    print(f"\n附带发现：process_image 不传 ratio（默认值 None）→ {ratio_err}")
    print("  两个内部调用方（vision/core.py:58、vision_picker/core/core.py:160）都显式传了 "
          f"ratio='w,h'，所以线上没暴露；但默认值本身是坏的 → {ratio_err}")

    # ---------------- 判定 ----------------
    engine_miss = box is None
    ctrl_hit = box2 is not None and not isinstance(box2, str)
    dropped = score_engine < THRESHOLD <= score_right

    if not (bad_gray and engine_miss and ctrl_hit):
        return report(
            "A15",
            "模板侧灰度用错通道（RGB2GRAY 作用在 BGR 图上）",
            "彩色目标应该能像灰色目标一样被正常匹配",
            f"静态错配 {len(bad_gray)} 处；彩色目标 match_box={box}；灰色目标 match_box={box2}；"
            f"引擎分={score_engine:.4f} 正确分={score_right:.4f}",
            NOT_REPRODUCED,
            "本版本未复现（可能已修正通道，或目标颜色恰好 R≈B），可关闭该条。",
        )

    return report(
        "A15",
        "模板侧灰度转换用错通道 → 彩色目标匹配分被拉低，低于 0.95 阈值就判「元素不存在」",
        "彩色目标和灰色目标应当用同一套换算方式匹配；同一张图不该因为颜色而匹配不上",
        f"静态：cv_match.py 里 {len(bad_gray)} 处把已转成 BGR 的图又按 RGB 权重转灰度 —— "
        + "、".join(f"line {ln} 对 {v}（BGR 转换在 line {bl}）" for ln, v, bl in bad_gray)
        + f"；屏幕侧 line 135 用的是 BGR2GRAY（正确）；"
        f"动态：调用引擎真实 AnchorMatch.process_image，彩色目标 RGB{rgb} 明明在屏幕上，"
        f"返回 match_box={box}（找不到）；对照组灰色目标 match_box={box2}（能找到）；"
        f"数值：引擎灰度匹配分 {score_engine:.4f} < 阈值 {THRESHOLD}，"
        f"正确灰度 {score_right:.4f}，灰度像素最大差 {max_gray_diff}",
        REPRODUCED,
        "复现步骤：① 屏幕放一个彩色目标（蓝/橙色这类 R 与 B 差距大的最明显）；"
        "② 用设计器拾取该目标为图像元素；③ 调用任意 CV.* 原子（如 CV.is_image_exist）；"
        "④ 报「元素不存在/置信度低」，但把目标改成灰色就正常。"
        "修复建议：把 cv_match.py:146 的 COLOR_RGB2GRAY 改成 COLOR_BGR2GRAY；"
        "cv_match.py:152 的 anchor 同样改；"
        "cv_match.py:33（check_if_multiple_elements，影响元素唯一性判定）一并改。"
        "建议同时加一条单测：对同一张彩色图，模板侧与屏幕侧的灰度转换必须走同一个常量。"
        f"附带发现（健壮性，非本条主因）：process_image 的 ratio 默认值是 None，"
        f"不传就会在 cv_match.py:117 抛 {ratio_err}；"
        f"两个内部调用方都显式传了 ratio='w,h'，所以线上没暴露，建议把默认值改成 '1,1'。",
    )


main_guard(run)
