# -*- coding: utf-8 -*-
"""流程参数「长度预算」—— 平台 A20 缺陷的量化模型（零依赖，可被 lint/巡检/publish 共用）。

背景（详见 docs/平台缺陷清单.md A20 / docs/开发进度.md §26.6e）
--------------------------------------------------------------
平台「添加应用」列表接口 `GET /api/robot/triggerTask/robotExe/list` 会把每个机器人的
参数列表拼成 `Executor.paramJson`（一个 JSON 数组字符串），然后进
`ApiLogAspect.formatResult()` → `SensitiveDataSanitizer.sanitize(Object)`。
`sanitize` 里真正命中的是 `QUOTED_VALUE`，它的值部分
`\\[(?:\\\\.|[^\\]\\\\])*\\]` 在 Java 里由 `java.util.regex.Pattern$Loop` **递归**实现，
**递归深度 ≈ 匹配到的字符数**，默认 1MB 栈在 ~1500~1800 字符处爆 `StackOverflowError`。
异常冒泡到全局兜底 → 整个接口只剩 `{"code":"999999"}` → 前端「添加应用」报错。

**所以真正的变量不是「参数有几条」，而是「paramJson 序列化后有多长」。**
一条 varValue 写几千字的参数，同样能单独把列表打挂。

本模块按平台 `ParamDto` 的真实字段算长度（2026-11-05 用 .183 生产库 6 条参数回算：
估算 1037 / 真实 inner 1037，逐字符一致）：

    [{"id":2117525245841833987,"varDirection":0,"varName":"","varType":"",
      "varValue":"","varDescribe":"","processId":"2117525243662102528"}, ...]

阈值来自同一台生产 JVM（OpenJDK 1.8.0_504，容器内 `java -cp .:/app/app.jar SanHarness2`
直接调 `SensitiveDataSanitizer.sanitize(Object)`），2026-11-05 二次校准：

    日志串 1268 / inner 995   OK
    日志串 1438 / inner 1139  OK
    日志串 1578 / inner 1279  OK
    日志串 1628 / inner 1355  OK   ← 实测最后的安全点
    ---- 悬崖 ----
    日志串 2112 / inner 1673  StackOverflowError（NCIC订单处理-华为 的**真实**参数，3/3 复现）
    日志串 2473 / inner 2153  StackOverflowError
    U8修改出库单 的 16 条真实参数估算 3249，同区间，必爆

所以：**inner 1355 安全、1673 必爆**，工程上按 ≤1050（约 6 条典型参数）用，
硬线画在 1450（悬崖中点，留 ~10% 余量给「服务端线程栈比 main 更深」这件事）。
"""
from __future__ import annotations

# 单条 ParamDto 的固定骨架（7 个字段 + 两个 19 位雪花 id + 全部 JSON 标点），空内容时 134 字符
SKELETON_LEN = 134

# 数组括号 `[` `]` + 元素间逗号
ARRAY_LEN = 2

# 建议值（工程目标）：约 6 条典型参数
SPAN_WARN = 1050
# 实测悬崖：8 条≈1400 仍 OK / 9 条≈1580 必爆；留 50 字符余量
SPAN_ERR = 1450
# 条数硬上限（条数与长度同时超限时优先报条数，提示更直观）
COUNT_ERR = 8
# 条数建议上限
COUNT_WARN = 6


def _txt(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        # 平台侧 varValue 是 varchar，Java 里 true/false 会落成 "true"/"false"
        return "true" if v else "false"
    return str(v)


def estimate_span(params):
    """估算 `paramJson` 序列化后的字符数（≈ 正则递归深度）。

    `params` 元素支持两种写法：
      - 规格写法：{"name","type","default","describe"}
      - 服务端写法：{"varName","varType","varValue","varDescribe"}
    两者可混用（缺哪个取哪个）。
    """
    items = [p for p in (params or []) if isinstance(p, dict)]
    if not items:
        return 0
    n = len(items)
    total = ARRAY_LEN + n * SKELETON_LEN + (n - 1)
    extra_escape = 0
    for p in items:
        name = _txt(p.get("name") if p.get("name") is not None else p.get("varName"))
        typ = _txt(p.get("type") if p.get("type") is not None else p.get("varType")) or "Str"
        val = _txt(p.get("default") if p.get("default") is not None else p.get("varValue"))
        desc = _txt(p.get("describe") if p.get("describe") is not None else p.get("varDescribe"))
        total += len(name) + len(typ) + len(val) + len(desc)
        # 内容里的 `"` / `\` 在外层 JSON 序列化时要各多占 1 个字符，正是它们把长度顶上去
        for s in (name, typ, val, desc):
            extra_escape += s.count('"') + s.count("\\")
    return total + extra_escape


def level(span, count):
    """风险等级：red / yellow / green。"""
    if span >= SPAN_ERR or count > COUNT_ERR:
        return "red"
    if span >= SPAN_WARN or count > COUNT_WARN:
        return "yellow"
    return "green"


def advise(params):
    """给出「该砍哪些参数」的建议：按占用长度从大到小排，同名（多版本重复）按一条算。"""
    items = [p for p in (params or []) if isinstance(p, dict)]
    agg = {}
    for p in items:
        name = _txt(p.get("name") if p.get("name") is not None else p.get("varName"))
        val = _txt(p.get("default") if p.get("default") is not None else p.get("varValue"))
        desc = _txt(p.get("describe") if p.get("describe") is not None else p.get("varDescribe"))
        size = SKELETON_LEN + len(name) + len(val) + len(desc)
        if name in agg:
            agg[name][0] = max(agg[name][0], size)   # 同名多版本只算一遍
        else:
            agg[name] = [size, name, val]
    ranked = [tuple(v) for v in agg.values()]
    ranked.sort(reverse=True)
    return ranked


def summary(params):
    """一行摘要，给巡检表用。"""
    items = [p for p in (params or []) if isinstance(p, dict)]
    span = estimate_span(items)
    return {"count": len(items), "span": span, "level": level(span, len(items))}
