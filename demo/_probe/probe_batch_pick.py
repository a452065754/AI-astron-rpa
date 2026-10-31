"""探针：验证能否脚本化产出 data_batch 需要的 BATCH 型元素。

思路：web_picker.py 里拾取器的 BATCH 链路是
  elementFromSelect -> elementIsTable -> tableDataBatch/tableColumnDataBatch
只要这三步都能从 bridge 拿到，就能自己组装 BATCH path 写库，
从而让「数据抓取（web）」不再依赖人工在设计器里点。
"""
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import element_pick as ep

URL = "http://127.0.0.1:8765/"
SEL = "#tbl"

print("== 0) 桥接连通性")
try:
    print("   ", ep.browser_status())
except Exception as e:
    print("    桥接不可用：", e)

print("== 1) 导航到测试页")
try:
    print("   ", ep.open_url(URL))
    ep.wait_for_url(URL)
    print("    已到测试页")
except Exception as e:
    print("    导航失败：", e)

print("== 2) elementFromSelect 拾取", SEL)
items = ep.probe(SEL, by="css", url=URL)
info = items[0]
print("    命中", len(items), "个；tag=", info.get("tag"), "| keys=", sorted(info.keys()))

print("== 3) elementIsTable 判定")
is_table_res = None
try:
    is_table_res = ep.bridge("elementIsTable", info)
    print("    ->", json.dumps(is_table_res, ensure_ascii=False)[:400])
except Exception as e:
    print("    失败：", e)

print("== 4) tableDataBatch 抓数据")
tdb = None
try:
    payload = is_table_res if isinstance(is_table_res, dict) and is_table_res.get("isTable") is not None else info
    tdb = ep.bridge("tableDataBatch", payload, timeout=20)
    print("    keys:", list(tdb.keys()) if isinstance(tdb, dict) else type(tdb))
    print("    ->", json.dumps(tdb, ensure_ascii=False)[:600])
except Exception as e:
    print("    失败：", e)

out = {
    "pick_info": info,
    "elementIsTable": is_table_res,
    "tableDataBatch": tdb,
}
os.makedirs(os.path.join(os.path.dirname(__file__)), exist_ok=True)
with open(os.path.join(os.path.dirname(__file__), "batch_probe_result.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print("\n已存 demo/_probe/batch_probe_result.json")
