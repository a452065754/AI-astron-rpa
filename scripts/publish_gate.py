# -*- coding: utf-8 -*-
"""发布闸门：**默认只做草稿，发布必须显式确认**。

为什么要有这一层（2026-11-05，A20 事故复盘后定）：

AI 生成的流程一旦「发布版本 + 被加进定时任务」，就会在正式服上长出一条
`robot_execute` 记录；点「定时任务 → 添加应用」时，平台会把该机器人**线上版本**
的全部流程参数序列化进 `Executor.paramJson`，再用 `SensitiveDataSanitizer`
跑脱敏 —— 那个正则的递归深度≈字符数，参数一多就 `StackOverflowError`，
把**这个账号**的整张应用列表打成 `999999`，而别的账号看着一切正常。

这条链路上，AI 唯一「不能自主决定」的一步就是**发布**：
- 落库（`robot_design` + `c_process` + `c_param`）= 草稿，不进「添加应用」列表，
  参数再多也不会引爆（生产库里 3 份 MES 草稿各 25 条参数，长期无事）；
- 发布（`robot-version/publish`）+ 加进定时任务 = 上膛，谁点的谁挨炸。

所以规则定成：

    默认 `--stage draft`（只在本地生成+校验，零写操作）；
    上平台测试用 `--stage review`（写设计态，仍是草稿，不产生版本）；
    `--stage publish` / `--stage expose` **必须加 `--confirm-publish`**，
    且这道确认只能由人给出 —— AI 不许替用户决定发布。

`RPA_ALLOW_PUBLISH=1` 是给无人值守流水线留的旁路，日常不要设。
"""

import os

# 默认阶段：只生成+校验。宁可多一步，也不要默认往正式服写。
DEFAULT_STAGE = "draft"

# 显式确认开关
CONFIRM_FLAG = "--confirm-publish"
ENV_ALLOW = "RPA_ALLOW_PUBLISH"

# 会「上膛」的阶段
GATED_STAGES = ("publish", "expose")


# 被拦下时的退出码：2（与「校验不通过」一致；1 留给真正的异常）
EXIT_CODE = 2


_BANNER = """\
❌ 这条命令会**发布到正式服**，已按默认策略拦下（未执行任何写操作）。

   为什么默认拦：
     · 草稿态（robot_design + c_process）不进「定时任务 → 添加应用」列表，参数再多也安全；
     · 一旦发布并被加进定时任务，参数会序列化进 paramJson，超过长度线就
       StackOverflowError → 该账号整张应用列表报 999999（平台缺陷 A20）；
     · 发布是不可逆的对外动作，必须由人确认，不能由 AI 自行决定。

   正确流程：
     1) 生成草稿：  python scripts/ai_flow.py --spec <规格>              # 默认 draft
     2) 上平台测试：python scripts/ai_flow.py --spec <规格> --stage review
     3) 人工在设计器里点「运行」验收通过
     4) 由人自己发布（设计器「发布」按钮），或明确要求后再执行：
        python scripts/ai_flow.py --spec <规格> --stage publish --version N --confirm-publish

   发布前务必先看参数预算：python scripts/param_health.py
"""


def is_confirmed(flag=False):
    """是否被显式授权发布。返回 (ok, 依据)。"""
    if flag:
        return True, "命令行 %s" % CONFIRM_FLAG
    if str(os.environ.get(ENV_ALLOW, "")).strip().lower() in ("1", "true", "yes", "on"):
        return True, "环境变量 %s" % ENV_ALLOW
    return False, ""


def require(stage, confirmed=False, robot=""):
    """发布前的硬闸门。stage 不在发布类里就直接放行（返回 None）。"""
    if stage not in GATED_STAGES:
        return None
    ok, why = is_confirmed(confirmed)
    if ok:
        print("⚠️ 已授权发布（依据：%s）—— 请确认目标机器人是%s自己选的。"
              % (why, ("「%s」" % robot) if robot else "你"))
        return None
    msg = _BANNER
    if robot:
        msg += "\n   本次目标机器人：%s\n" % robot
    print(msg)
    raise SystemExit(EXIT_CODE)


def check_spec_flags(spec):
    """规格里若写了「帮我发布」这类意图，返回提示文案（不阻塞）。

    让 `draft/review` 阶段也能提醒一句：规格作者以为自己已经授权了，
    但闸门只认命令行开关 —— 免得两处口径不一致。
    """
    if not isinstance(spec, dict):
        return ""
    for k in ("publish", "auto_publish", "publish_after_review"):
        if spec.get(k):
            return ("规格里写了 %s=%r，但发布闸门只认命令行 %s（或环境变量 %s）—— "
                    "该字段不产生授权效果。" % (k, spec.get(k), CONFIRM_FLAG, ENV_ALLOW))
    return ""
