# -*- coding: utf-8 -*-
"""
make_module —— 把本地 .py 建成/更新成平台上的 Python 子流程模块

为什么要有这一步（可迁移的关键）：
  流程规格里的 `Script.module` 节点靠 `module_id` 指向服务端存的模块内容。
  模块必须用**官方接口**建（creator_id = 当前登录用户），发布时平台才会自己把它
  复制进版本；手工 SQL 插的行 creator_id 是旧格式，发布后找不到 → 「该模块不存在」。

用法：
  # 新建机器人（可选）
  python make_module.py --new-robot "PDF版面还原Word"

  # 把 py 建成模块，并把 module_id 写回流程规格
  python make_module.py --py mod_pdf_layout.py --robot 2116xxxxxxxx \
      --name "PDF版面还原" --spec ../flows/PDF版面还原Word小流程.json

  # 只更新已有模块的内容（改了 py 之后重新同步）
  python make_module.py --py mod_pdf_layout.py --robot 2116xxxxxxxx \
      --module-id 2116yyyyyyyy --name "PDF版面还原"
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import publish_flow as pf


def update_spec_module_id(spec_path, module_id, param="module_id"):
    """把 module_id 写回规格里那个参数的 default。"""
    with open(spec_path, "r", encoding="utf-8") as f:
        spec = json.load(f)
    hit = False
    for p in spec.get("params") or []:
        if p.get("name") == param:
            p["default"] = str(module_id)
            hit = True
    if not hit:
        raise SystemExit(f"规格里没有名为 {param} 的参数：{spec_path}")
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return spec_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--py", default="", help="要上传的 Python 文件")
    ap.add_argument("--robot", default="", help="目标机器人 robotId")
    ap.add_argument("--new-robot", default="", help="先建一个机器人，名字")
    ap.add_argument("--name", default="", help="模块名（便于在设计器里认）")
    ap.add_argument("--module-id", default="", help="已有模块 ID；给了就是覆盖内容")
    ap.add_argument("--spec", default="", help="把 module_id 写回这个规格文件")
    ap.add_argument("--param", default="module_id", help="规格里存 module_id 的参数名")
    args = ap.parse_args()

    cookie = pf.load_session()

    robot_id = args.robot
    if args.new_robot:
        rid = pf.create_robot(cookie, args.new_robot)
        robot_id = str(rid or "")
        if not robot_id:
            raise SystemExit("建机器人没返回 robotId")
        print(f"机器人已建：{robot_id}（{args.new_robot}）")

    if not args.py:
        if robot_id:
            print(f"robotId={robot_id}")
        return

    if not robot_id:
        raise SystemExit("要么给 --robot，要么给 --new-robot")

    with open(os.path.join(HERE, args.py) if not os.path.isabs(args.py) else args.py,
              "r", encoding="utf-8") as f:
        content = f.read()

    if args.module_id:
        pf.save_module(cookie, robot_id, args.module_id, content)
        module_id = args.module_id
        print(f"模块内容已更新：{module_id}（{len(content)} 字符）")
    else:
        module_id = pf.create_module(cookie, robot_id, content, args.name)
        print(f"模块已建：{module_id}（{len(content)} 字符）")

    if args.spec:
        p = update_spec_module_id(args.spec, module_id, args.param)
        print(f"已写回规格：{p} → {args.param}={module_id}")

    print(f"ROBOT={robot_id}")
    print(f"MODULE={module_id}")


if __name__ == "__main__":
    main()
