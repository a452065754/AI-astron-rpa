# -*- coding: utf-8 -*-
"""建一个机器人并打印 id（已存在则直接返回）。用法：python _new_robot.py "名字" """
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import publish_flow as pf

name = sys.argv[1] if len(sys.argv) > 1 else "临时机器人"
ck = pf.load_session()
rid = pf.find_robot(ck, name)
if rid:
    print(f"EXISTS {rid}")
else:
    rid = pf.create_robot(ck, name)
    print(f"CREATED {rid}")
