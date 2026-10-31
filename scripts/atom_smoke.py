# -*- coding: utf-8 -*-
"""原子冒烟测试（atom_smoke.py）—— 免建机器人，直接调原子函数。

思路：`docs/atom_dict_cache.json` 里每个原子有 `src`（如
`astronverse.dataprocess.string.StringProcess().get_string_length`），
据此 import 模块 → 实例化类 → 调方法；枚举参数按 schema 的 `types` 自动转成员。

必须用设计器自带 python 运行（本机任意 python 没有 astronverse）：
  "%APPDATA%/astron-rpa/python_core/python.exe" scripts/atom_smoke.py
  ... --only StringProcess          # 只跑某模块
  ... --list                        # 只列计划，不执行
"""
import argparse
import importlib
import json
import os
import sys
import enum
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "docs", "atom_dict_cache.json")
DEMO = os.path.join(ROOT, "demo")

# 每个原子：要覆盖的入参（其余必填项用 schema 默认值）
TESTS = {
    # ---- 时间 ----
    "TimeProcess.get_current_time": {"time_format": "%Y-%m-%d %H:%M:%S"},
    # ---- 字符串 ----
    "StringProcess.get_string_length": {"string_data": "hello世界"},
    "StringProcess.change_case_of_string": {"string_data": "Hello", "case_type": "upper"},
    "StringProcess.replace_content_in_string": {"text": "a-b-c", "replace_type": "string",
                                                "replaced_string": "-", "new_value": "_"},
    "StringProcess.split_string_to_list": {"string_data": "a,b,c", "separator": ","},
    "StringProcess.strip_string": {"string_data": "  hi  ", "strip_method": "both"},
    "StringProcess.concatenate_string": {"string_data_1": "foo", "string_data_2": "bar",
                                         "concat_type": "none", "separator": ""},
    "StringProcess.extract_content_from_string": {"text": "abc123def", "extract_type": "digit"},
    # ---- 数学 ----
    "MathProcess.calculate_expression": {"left": 3, "operator": "+", "right": 4},
    "MathProcess.get_rounding_number": {"number": 3.14159, "precision": 2},
    "MathProcess.get_absolute_number": {"raw_number": -5},
    "MathProcess.self_calculation_number": {"number": 5, "add_sub": "add", "add_sub_number": 1},
    "MathProcess.generate_random_number": {"number_type": "integer", "start": 1, "end": 10},
    # ---- 数据转换 ----
    "DataConvertProcess.str_to_other": {"input_data": "123", "convert_type": "str_to_int"},
    "DataConvertProcess.other_to_str": {"input_data": 123},
    # ---- 加密 ----
    "Encrypt.md5_encrypt": {"source_str": "hello"},
    "Encrypt.sha_encrypt": {"source_str": "hello", "sha_method": "sha256"},
    "Encrypt.base64_encoding": {"encode_type": "string", "string_data": "hello"},
    "Encrypt.base64_decoding": {"decode_type": "string", "string_data": "aGVsbG8="},
    # ---- 文件夹（不弹窗，安全） ----
    "Folder.folder_create": {"target_path": DEMO, "folder_name": "_atomtest"},
    "Folder.folder_exist": {"folder_path": os.path.join(DEMO, "_atomtest")},
    "Folder.get_folder_list": {"folder_path": DEMO},
    "Folder.folder_rename": {"folder_path": os.path.join(DEMO, "_atomtest"), "new_name": "_atomtest2"},
    "Folder.folder_delete": {"folder_path": os.path.join(DEMO, "_atomtest2")},
    # ---- 列表 ----
    "ListProcess.get_length_of_list": {"list_data": [1, 2, 3]},
    "ListProcess.get_unique_list": {"list_data": [1, 1, 2, 3]},
    "ListProcess.sort_list": {"list_data": [3, 1, 2], "sort_method": "asc"},
    "ListProcess.reverse_list": {"list_data": [1, 2, 3]},
    "ListProcess.merge_list": {"list_data_1": [1, 2], "list_data_2": [3, 4]},
    "ListProcess.get_common_elements_from_list": {"list_data_1": [1, 2, 3], "list_data_2": [2, 3, 4]},
    "ListProcess.filter_elements_from_list": {"list_data_1": [1, 2, 3, 4], "list_data_2": [2, 4]},
    "ListProcess.get_value_from_list": {"list_data": [10, 20, 30], "index": 1},
    "ListProcess.insert_value_to_list": {"list_data": [1, 2], "value": 3,
                                         "insert_method": "append", "index": ""},
    "ListProcess.change_value_in_list": {"list_data": [1, 2, 3], "index": 1, "new_value": 9},
    "ListProcess.get_list_position": {"list_data": [1, 2, 3], "value": 2},
    # ---- 字典 ----
    "DictProcess.get_keys_from_dict": {"dict_data": {"a": 1, "b": 2}},
    "DictProcess.get_values_from_dict": {"dict_data": {"a": 1, "b": 2}},
    "DictProcess.get_value_from_dict": {"dict_data": {"a": 1}, "dict_key": "a"},
    "DictProcess.set_value_to_dict": {"dict_data": {"a": 1}, "dict_key": "b", "value": 2},
    "DictProcess.delete_value_from_dict": {"dict_data": {"a": 1, "b": 2}, "dict_key": "a"},
    # ---- 网络（打 .130 的演示页） ----
    "Network.http_request": {"url": "http://192.168.76.130:8899/test.html",
                             "request_type": "get", "save_type": "no", "save_path": "",
                             "save_name": "", "headers": "", "body": "", "file_path": "",
                             "time_out": 60},
    "Network.http_download": {"url": "http://192.168.76.130:8899/test.html",
                              "dst_dir": DEMO, "rename": "_dl.html",
                              "state_type": "create", "exist_type": "rename"},
    # ---- 系统 ----
    "System.get_pid": {"process_name": "chrome.exe", "search_type": "exact", "pid_type": "all"},
}


def load_atoms():
    with open(CACHE, encoding="utf-8") as f:
        return json.load(f)


def find_enum(mod_name, type_name):
    try:
        m = importlib.import_module(mod_name)
        c = getattr(m, type_name, None)
        if isinstance(c, type(enum.Enum)):
            return c
    except Exception:
        pass
    return None


def to_enum(raw, type_name, mod_name, default=None):
    if not type_name:
        return raw
    c = find_enum(mod_name, type_name)
    if not c:
        return raw
    for m in c:
        if str(m.value) == str(raw) or m.name.lower() == str(raw).lower():
            return m
    if default is not None:
        for m in c:
            if str(m.value) == str(default):
                return m
    return raw


def run_one(atom_key, atoms):
    a = atoms[atom_key]
    src = a.get("src") or ""
    if "()" not in src:
        return False, f"src 异常: {src!r}"
    mod_path, cls, meth = src.rsplit(".", 2)
    cls = cls.replace("()", "")
    mod = importlib.import_module(mod_path)
    obj = getattr(mod, cls)()

    overrides = TESTS.get(atom_key, {})
    kwargs = {}
    for p in a.get("inputList") or []:
        if not isinstance(p, dict):
            continue
        k = p.get("key")
        tn = p.get("types")
        default = p.get("default")
        if k in overrides:
            raw = overrides[k]
        elif p.get("required"):
            raw = default
        else:
            continue
        if raw in (None, "") and k not in overrides:
            continue
        kwargs[k] = to_enum(raw, tn, mod_path, default)

    ret = getattr(obj, meth)(**kwargs)
    return True, ret


def main():
    ap = argparse.ArgumentParser(description="原子冒烟测试（免建机器人）")
    ap.add_argument("--only", default="", help="只跑指定模块前缀，如 StringProcess")
    ap.add_argument("--list", action="store_true", help="只列计划不执行")
    args = ap.parse_args()

    if not os.path.exists(CACHE):
        print(f"✗ 缺少原子缓存 {CACHE}（先跑 flow_generator 拉一次）")
        return 2
    atoms = load_atoms()

    keys = [k for k in TESTS if k in atoms and (not args.only or k.startswith(args.only))]
    missing = [k for k in TESTS if k not in atoms]
    if missing:
        print(f"⚠️ 缓存里找不到这些原子（跳过）：{missing}")

    if args.list:
        for k in keys:
            print("  ", k, "|", atoms[k].get("title"))
        return 0

    results = []
    for k in keys:
        title = atoms[k].get("title", "")
        try:
            ok, ret = run_one(k, atoms)
            results.append((k, title, ok, ret if ok else ""))
            print(f"✅ {k:42s} {title:16s} -> {ret!r}")
        except Exception as e:
            results.append((k, title, False, f"{type(e).__name__}: {e}"))
            print(f"❌ {k:42s} {title:16s} -> {type(e).__name__}: {e}")
            if os.environ.get("ATOM_SMOKE_TRACE"):
                traceback.print_exc()

    ok_cnt = sum(1 for _, _, ok, _ in results if ok)
    print("\n" + "=" * 70)
    print(f"通过 {ok_cnt}/{len(results)}")
    fails = [(k, r) for k, _, ok, r in results if not ok]
    if fails:
        print("失败明细：")
        for k, r in fails:
            print(f"  ❌ {k} -> {r}")
    return 0 if ok_cnt == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
