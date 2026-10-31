#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星辰开源 · 元素重新定位器（Element Relocator）
================================================

解决的真问题：RPA 流程里的 web 元素定位器随页面改版失效，
执行时报「网页元素查找失败 元素在第N节点/xpath 处发生变动」。

原理（全部来自 .130 源码实证，非猜测）：
  1. 元素存于 `c_element.element_data`（JSON），核心是 `path` 对象；
     `path.pathDirs` 是**定位真源**（ElementDirectory 数组）。
  2. 引擎/插件在 `checkType === 'visualization'` 时**不直接用** path.xpath，
     而是用 `Utils.generateXPath(pathDirs)` 现场拼 xpath：
       - 只取 `dir.checked === true` 的层级；
       - 每层只取 `attr.checked === true` 的属性；
       - attr.type: 0=精确(@a="v") / 1=包含(contains(@a,"v")) / 2=正则(后置过滤)；
       - attr.name: index→position()=n, text/innertext→text() / contains(.,"v"),
                     class/id/其它→@a="v" 或 contains(@a,"v")。
  3. `rebuildDirectory` 会尝试取消勾选 index/class，让路径尽量短且稳。
  4. 重新定位 = 复刻插件的 `getElementDirectory` 算法，在**新页面**上
     重新生成一份 pathDirs，并回写 c_element。

工作流：
  A. 诊断（--check）：对每个元素在目标 URL 上验证旧定位器是否还能命中。
  B. 修复（--fix）：失效时用语义线索（text/class/id/邻近结构）重新生成 pathDirs。
  C. 回写（--apply）：把新 element_data 写回 c_element（先备份，默认只写
     robot_version=0/1 全部行，保持与既有结构一致）。

安全约束：
  - 默认 dry-run，只报告不写库；必须显式 --apply 才落库。
  - 写库前自动把被改动的行备份到本地 JSON（backup/ 目录）。
  - 只改 element_data，不动流程节点树、API Key、Casdoor。

用法：
  python scripts/element_relocator.py --list
  python scripts/element_relocator.py --check --robot-id 2065231113695461380
  python scripts/element_relocator.py --fix  --robot-id 2065231113695461380 --apply
"""

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime

try:
    import paramiko
except ImportError:
    paramiko = None

def _require_playwright():
    """惰性引入 playwright —— 只有真正要开浏览器校验元素时才需要它。

    背景：flow_generator / ai_flow 只是复用本文件的 ServerDb（MySQL/SSH），
    不该因为「没装浏览器」就整条链路起不来（坑 65）。这里改成按需导入。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.stderr.write("缺少依赖 playwright，请先: pip install playwright && playwright install chromium\n")
        raise SystemExit(2)
    return sync_playwright


# ----------------------------- 配置 -----------------------------
import rpa_config as cfg


def _db_conf_or_die():
    """取 SQL 通道配置；未启用就报一条「怎么开」的错，而不是 KeyError。"""
    c = cfg.db_conf()
    if not c:
        raise SystemExit(
            "SQL 通道未启用（rpa_config.json 里 db.enabled=false）。\n"
            "  这条链路只在官方 HTTP API 做不到时才用（例如 c_module 的版本复制）。\n"
            "  迁移到新环境时请优先用纯 API 方案；确实要用就在 rpa_config.json 里\n"
            "  把 db.enabled 改成 true，并填 ssh_host / ssh_user / ssh_password / password。"
        )
    if not c.get("password"):
        raise SystemExit("db.enabled=true 但没填 db.password（MySQL 密码）。")
    return c


BACKUP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backup")

# 与插件 constant.ts 一致
MAX_TEXT_LENGTH = 10
MAX_TEXT_INCLUDE_LENGTH = 64


# ------------------------- 服务器/DB 访问 -------------------------
class ServerDb:
    """通过 SSH 在服务端执行 mysql 查询。

    **这是「官方 HTTP API 做不到时」的兜底通道**，默认关闭（`rpa_config.json` 的
    `db.enabled=false`）。迁移到新环境时优先走纯 API 方案；确实要用再开开关填凭据。
    """

    def __init__(self, host=None, user=None, pw=None):
        c = _db_conf_or_die()
        self.conf = c
        self.host = host or c["ssh_host"]
        self.user = user or c["ssh_user"]
        self.pw = pw or c["ssh_password"]
        self._client = None

    def _connect(self):
        if self._client is None:
            c = paramiko.SSHClient()
            c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(self.host, port=22, username=self.user, password=self.pw, timeout=20)
            self._client = c
        return self._client

    def _mysql(self, payload):
        c = self.conf
        return (f"docker exec {c['container']} mysql -u{c['user']} -p{c['password']} "
                f"{c['database']} {payload}")

    def sql(self, query, binary=False):
        """执行 SQL，返回原始文本（tab 分隔，无表头）。"""
        cmd = self._mysql(f"-N -B -e {json.dumps(query)}")
        _, out, err = self._connect().exec_command(cmd, timeout=180)
        text = out.read().decode(errors="replace")
        return text

    def sql_rows(self, query):
        rows = []
        for line in self.sql(query).split("\n"):
            line = line.rstrip("\r")
            if not line.strip():
                continue
            rows.append(line.split("\t"))
        return rows

    def sql_exec(self, statement):
        """执行写语句。"""
        cmd = self._mysql(f"-e {json.dumps(statement)}")
        _, out, err = self._connect().exec_command(cmd, timeout=180)
        o = out.read().decode(errors="replace")
        e = err.read().decode(errors="replace")
        if "ERROR" in e:
            raise RuntimeError(f"SQL 执行失败: {e.strip()[:400]}")
        return o

    def close(self):
        if self._client:
            self._client.close()
            self._client = None


def b64d(s):
    return base64.b64decode(re.sub(r"\s+", "", s)).decode("utf-8", errors="replace")


def rpa_value(v):
    """把 {'rpa':..,'value':[{'type':..,'value':..}]} 拆成纯字符串。"""
    if isinstance(v, dict):
        return rpa_value(v.get("value"))
    if isinstance(v, list):
        parts = [rpa_value(x) for x in v]
        parts = [p for p in parts if p not in (None, "")]
        return " | ".join(str(p) for p in parts)
    return v


def unwrap_special(data):
    """
    复刻引擎 `Params._recursive_convert_params`：
    把 {"rpa":"special","value":[{"type":..,"value":..}]} 递归拆成纯字符串。
    库里的 pathDirs/attr.value 都是这个包装格式，
    引擎运行时才拆开；生成 xpath 前必须先拆，否则会拼出字典字面量。
    """
    if isinstance(data, dict):
        if data.get("rpa") == "special" and "value" in data:
            v = data["value"]
            if isinstance(v, list) and len(v) > 0:
                pieces = []
                for item in v:
                    pieces.append(str(item.get("data", item.get("value", ""))))
                return "".join(pieces)
            return v
        return {k: unwrap_special(x) for k, x in data.items()}
    if isinstance(data, list):
        return [unwrap_special(x) for x in data]
    return data


def wrap_special(text):
    """写回库时的包装格式：{"rpa":"special","value":[{"type":"other","value":X}]}。"""
    return {"rpa": "special", "value": [{"type": "other", "value": text}]}


def unwrap_path_dirs(dirs):
    """把 pathDirs 里所有 attr.value 的 special 包装拆开。"""
    out = []
    for d in dirs or []:
        nd = dict(d)
        attrs = []
        for a in d.get("attrs", []) or []:
            na = dict(a)
            na["value"] = unwrap_special(a.get("value"))
            attrs.append(na)
        nd["attrs"] = attrs
        out.append(nd)
    return out


# --------------------------- 元素读取 ---------------------------
def list_elements(db, robot_id=None):
    """读取 c_element，返回元素记录列表（按 element_id 去重，保留多版本行信息）。"""
    where = f"WHERE robot_id='{robot_id}' AND deleted=0" if robot_id else "WHERE deleted=0"
    rows = db.sql_rows(
        "SELECT id, element_id, element_name, robot_version, "
        "REPLACE(TO_BASE64(element_data),'\\n','') "
        f"FROM c_element {where} ORDER BY element_id, robot_version"
    )
    elems = {}
    for r in rows:
        if len(r) < 5 or not r[4].strip():
            continue
        try:
            data = json.loads(b64d(r[4]))
        except Exception:
            continue
        eid = r[1]
        rec = elems.setdefault(eid, {
            "element_id": eid, "element_name": r[2],
            "rows": [], "data": data,
        })
        rec["rows"].append({"id": r[0], "robot_version": r[3]})
    return list(elems.values())


def get_path(data):
    return (data or {}).get("path") or {}


def element_summary(data):
    p = get_path(data)
    return {
        "type": data.get("type"),
        "app": data.get("app"),
        "checkType": p.get("checkType"),
        "tag": p.get("tag"),
        "text": p.get("text"),
        "url": unwrap_special(p.get("url", {})),
        "tabUrl": p.get("tabUrl"),
        "xpath": unwrap_special(p.get("xpath", {})),
        "cssSelector": unwrap_special(p.get("cssSelector", {})),
        "pathDirs": p.get("pathDirs") or [],
    }


# ------------------- 插件算法的 Python 复刻 -------------------
def is_number_string(s):
    return bool(re.search(r"\d", s or ""))


SPECIAL_CHAR_RE = re.compile(
    r"[·~`!@#$%^&*()+\-={}\[\]|:;\"'<>,.?/（）￥！、；：【】《》，。？—]"
)


def is_special_character(s):
    s = s or ""
    if s and s[0].isdigit():
        return True
    return bool(SPECIAL_CHAR_RE.search(s))


def is_dynamic_attribute(attr_name, attr_value):
    """复刻 Utils.isDynamicAttribute。"""
    uuid_p = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
    long_rand = re.compile(r"^[\w-]{20,}$")
    kws = ["temp-", "dynamic-", "random-", "unique-", "session-", "token-", "uuid-",
           "id-", "key-", "rand-", "hover-", "active-", "focus-", "selected-",
           "-open", "-active", "-hover", "-focus", "-selected", "show", "hide", "hidden"]
    low = (attr_value or "").lower()
    if attr_name == "id":
        return bool(uuid_p.match(attr_value or "")) or bool(long_rand.match(attr_value or "")) \
            or any(k in low for k in kws)
    return any(k in low for k in kws)


def is_effect_character(s):
    return bool(re.search(r"[0-9a-z一-龥]", s or "", re.I))


def is_control_character(s):
    return bool(re.search(r"[\x00-\x1f\x7f]", s or ""))


def textfn(val):
    """复刻 Utils.textfn —— 生成 xpath 的 text() 条件。"""
    if '"' in val:
        parts = val.split('"')
        out = []
        for i, part in enumerate(parts):
            if i < len(parts) - 1:
                out.append(f'"{part}", \'"\', ')
            else:
                out.append(f'"{part}"')
        return f'text()=concat({("".join(out))})'
    return f'text()="{val}"'


def condition_str(attr):
    """复刻 Utils.conditionStr —— 单个属性的 xpath 条件。"""
    name = attr.get("name")
    val = str(attr.get("value", ""))
    atype = attr.get("type", 0)
    if name == "index":
        return f"position()={val}"
    if name in ("innertext", "text"):
        return f'contains(., "{val}")' if atype == 1 else textfn(val)
    if name == "local-name":
        return f'local-name()="{val}"'
    return f'contains(@{name}, "{val}")' if atype == 1 else f'@{name}="{val}"'


def generate_xpath(dirs, only_position=False):
    """复刻 Utils.generateXPath —— 由 pathDirs 现场拼 xpath。"""
    if not dirs:
        return ""
    dirs = unwrap_path_dirs(json.loads(json.dumps(dirs)))
    if only_position:
        for item in dirs:
            for attr in item.get("attrs", []):
                av = str(attr.get("value", "")).strip()
                if attr.get("name") == "index" and av != "":
                    attr["checked"] = True
                elif attr.get("name") == "id" and av != "":
                    attr["checked"] = True
                else:
                    attr["checked"] = False
    xpath = ""
    checked_dir_index = -1
    for i, d in enumerate(dirs):
        if not d.get("checked"):
            continue
        attrs = [a for a in d.get("attrs", [])
                 if not (a.get("type") == 2 and a.get("checked"))
                 and a.get("checked")]
        conds = " and ".join(condition_str(a) for a in attrs)
        segment = f"{d.get('tag')}[{conds}]" if conds else d.get("tag")
        sep = "/"
        if i == 0:
            sep = "/" if d.get("tag") == "html" else "//"
        if abs(i - checked_dir_index) > 1:
            sep = "//"
        xpath += f"{sep}{segment}"
        checked_dir_index = i
    return xpath


def generate_css_selector(dirs):
    """复刻 Utils.generateCssSelector —— 由 pathDirs 拼 cssSelector。"""
    if not dirs:
        return ""
    dirs = unwrap_path_dirs(json.loads(json.dumps(dirs)))
    selector = ""
    checked_dir_index = -1
    for i, d in enumerate(dirs):
        if not d.get("checked"):
            continue
        segment = d.get("tag")
        for attr in d.get("attrs", []):
            if attr.get("checked") and attr.get("type") == 1 and attr.get("value"):
                av = str(attr["value"]).strip()
                if attr.get("name") == "index" and av != "":
                    segment += f":nth-child({av})"
                elif attr.get("name") == "class" and av != "":
                    for cls in [c for c in av.split() if c.strip()]:
                        segment += f".{cls}"
        sep = ""
        if i == 0:
            sep = ""
        elif abs(i - checked_dir_index) == 1:
            sep = " > "
        elif abs(i - checked_dir_index) > 1:
            sep = " "
        selector += f"{sep}{segment}"
        checked_dir_index = i
    return selector


# ---------------------- 浏览器侧：DOM 探测 ----------------------
# 注入页面的 JS：复刻插件 getElementDirectory/rebuildDirectory，
# 在**当前真实 DOM** 上为给定元素生成一份新的 pathDirs。
JS_REBUILD = r"""
(args) => {
  const { targetIndex, tagHint, textHint } = args;

  const MAX_TEXT_LENGTH = 10;
  const MAX_TEXT_INCLUDE_LENGTH = 64;

  const isNumberString = s => /\d/.test(s);
  const SPECIAL = /[·~`!@#$%^&*()+\-={}\[\]|:;"'<>,.?/（）￥！、；：【】《》，。？—]/;
  const isSpecialCharacter = s => {
    if (!s) return false;
    if (isNumberString(s[0])) return true;
    return SPECIAL.test(s);
  };
  const isDynamicAttribute = (attrName, attrValue) => {
    const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
    const longRandomPattern = /^[\w-]{20,}$/;
    const dynamicKeywords = ['temp-','dynamic-','random-','unique-','session-','token-','uuid-','id-','key-','rand-','hover-','active-','focus-','selected-','-open','-active','-hover','-focus','-selected','show','hide','hidden'];
    const lowerValue = (attrValue||'').toLowerCase();
    if (attrName === 'id') {
      return uuidPattern.test(attrValue) || longRandomPattern.test(attrValue)
        || dynamicKeywords.some(kw => lowerValue.includes(kw));
    }
    return dynamicKeywords.some(kw => lowerValue.includes(kw));
  };
  const isControlCharacter = s => /[\x00-\x1F\x7F]/.test(s);
  const isEffectCharacter = s => /[0-9a-z\u4E00-\u9FA5]/i.test(s);

  const getAttr = (el, attrName) => {
    const attrMap = {
      accept: el.getAttribute('accept'), id: el.getAttribute('id'),
      class: el.getAttribute('class'), name: el.getAttribute('name'),
      type: el.getAttribute('type'), value: el.value,
      href: el.href, src: el.src || el.getAttribute('src'),
      title: el.title, text: getNodeText(el), placeholder: el.placeholder,
      readonly: String(el.readOnly), role: el.getAttribute('role'),
      'aria-label': el.getAttribute('aria-label'),
    };
    if (attrName in attrMap) return attrMap[attrName] || '';
    return el.getAttribute(attrName);
  };
  function getNodeText(el) {
    const nodeText = Array.from(el.childNodes)
      .filter(n => n.nodeType === Node.TEXT_NODE && n.textContent && n.textContent.trim())
      .map(n => n.textContent.replace(/[\x00-\x1F\x7F]/g, '').trim())
      .find(t => t.length > 0) || '';
    return nodeText;
  }
  const getSupportTag = t => isSpecialCharacter(t) ? '*' : t;
  const getElementIndex = el => el.parentElement
    ? Array.from(el.parentElement.children)
        .filter(s => s.tagName.toLowerCase() === el.tagName.toLowerCase())
        .indexOf(el) + 1 : 0;
  const getAllElementIndex = el => el.parentElement
    ? Array.from(el.parentElement.children).indexOf(el) + 1 : 0;
  const hasSameTypeSiblings = el => el.parentElement
    ? Array.from(el.parentElement.children)
        .filter(s => s.tagName.toLowerCase() === el.tagName.toLowerCase()).length > 1 : false;
  const hasSiblings = el => el.parentElement
    ? Array.from(el.parentElement.children).length > 1 : false;
  const hasSameClassSiblings = (el, className) => {
    const sibs = el.parentElement ? Array.from(el.parentElement.children).filter(s => s !== el) : [];
    return sibs.length > 0 && sibs.some(s => s.classList.contains(className));
  };
  const pickClass = el => {
    for (const cls of Array.from(el.classList)) {
      if (!hasSameClassSiblings(el, cls)) return cls;
    }
    return '';
  };
  const isUniqueIdFn = id => id && !isNumberString(id) && !isSpecialCharacter(id)
    && document.querySelectorAll('#' + id).length === 1;
  const isHighWeightClass = cls => cls && !isNumberString(cls) && !isSpecialCharacter(cls)
    && !isDynamicAttribute('class', cls);
  const isSvgElement = el => el.namespaceURI === 'http://www.w3.org/2000/svg';

  const generateXPath = (dirs) => {
    if (!dirs || dirs.length === 0) return '';
    let xpath = '', checkedDirIndex = -1;
    for (let i = 0; i < dirs.length; i++) {
      const dir = dirs[i];
      if (!dir.checked) continue;
      const attrs = dir.attrs.filter(a => !(a.type === 2 && a.checked) && a.checked)
        .map(a => {
          const v = String(a.value);
          switch (a.name) {
            case 'index': return `position()=${v}`;
            case 'innertext': case 'text':
              return a.type === 1 ? `contains(., "${v}")`
                : (v.includes('"') ? `text()=concat(${v.split('"').map((p,i,arr)=> i<arr.length-1 ? `"${p}", '"', ` : `"${p}"`).join('')})` : `text()="${v}"`);
            case 'local-name': return `local-name()="${v}"`;
            default: return a.type === 1 ? `contains(@${a.name}, "${v}")` : `@${a.name}="${v}"`;
          }
        }).join(' and ');
      const segment = attrs ? `${dir.tag}[${attrs}]` : dir.tag;
      let separator = '/';
      if (i === 0) separator = dir.tag === 'html' ? '/' : '//';
      if (Math.abs(i - checkedDirIndex) > 1) separator = '//';
      xpath += `${separator}${segment}`;
      checkedDirIndex = i;
    }
    return xpath;
  };
  const getElementsByXpath = (path) => {
    try {
      const res = document.evaluate(path, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
      const out = [];
      for (let i = 0; i < res.snapshotLength; i++) {
        const n = res.snapshotItem(i);
        if (n && n.nodeType === 1) out.push(n);
      }
      return out;
    } catch (e) { return []; }
  };
  const rebuildDirectory = (originElement, dirs) => {
    const idIndex = dirs.findLastIndex(d => d.attrs.some(a => a.name === 'id' && a.checked));
    for (let i = dirs.length - 1; i >= 0; i--) {
      const dir = dirs[i];
      if (idIndex !== -1 && i < idIndex) { dir.checked = false; dir.attrs.forEach(a => a.checked = false); }
      const tryUncheck = (attrName) => {
        const attr = dir.attrs.find(a => a.name === attrName);
        if (attr && attr.checked) {
          attr.checked = false;
          const els = getElementsByXpath(generateXPath(dirs));
          attr.checked = !(els && els.length === 1 && els[0] === originElement);
        }
      };
      tryUncheck('index'); tryUncheck('class');
    }
    return dirs;
  };

  const getElementDirectory = (element) => {
    if (!element) return [];
    const originElement = element;
    const elementDirectory = [];
    while (element) {
      const id = getAttr(element, 'id');
      const className = pickClass(element);
      const type = getAttr(element, 'type');
      const text = getNodeText(element);
      let tagName = getSupportTag(element.tagName.toLowerCase());
      let index = getElementIndex(element);
      let hasSubling = hasSameTypeSiblings(element);
      const isSvg = isSvgElement(element);
      tagName = isSvg ? '*' : tagName;
      index = isSvg ? getAllElementIndex(element) : index;
      hasSubling = isSvg ? hasSiblings(element) : hasSubling;

      const attrs = [];
      if (id) attrs.push({ name: 'id', value: id, checked: isUniqueIdFn(id), type: 0 });
      if (isSvg) attrs.push({ name: 'local-name', value: element.tagName.toLowerCase(), checked: true, type: 0 });
      if (hasSubling && index) attrs.push({ name: 'index', value: index, checked: true, type: 0 });
      if (type) attrs.push({ name: 'type', value: type, checked: true, type: 0 });
      if (className) attrs.push({ name: 'class', value: className, checked: isHighWeightClass(className), type: 1 });
      if (text && text.length < MAX_TEXT_INCLUDE_LENGTH) {
        const textChecked = text.length < MAX_TEXT_LENGTH && isEffectCharacter(text) && !isControlCharacter(text);
        attrs.push({ name: 'text', value: text, checked: textChecked, type: 1 });
      }
      const specialAttrs = ['id','class','type','text','index','local-name'];
      for (const key in element.attributes) {
        const a = element.attributes[key];
        if (a && a.name && a.value && !specialAttrs.includes(a.name)) {
          attrs.push({ name: a.name, value: a.value, checked: false, type: 0 });
        }
      }
      elementDirectory.unshift({ tag: tagName, checked: true, value: tagName, attrs });
      element = element.parentElement;
      if (element && element.tagName.toLowerCase() === 'body') {
        return rebuildDirectory(originElement, elementDirectory);
      }
    }
    return rebuildDirectory(originElement, elementDirectory);
  };

  // ---- 定位候选元素 ----
  const all = Array.from(document.body.querySelectorAll('*'));
  const visible = all.filter(el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  });

  const norm = s => (s || '').replace(/[\x00-\x1F\x7F]/g, '').trim();

  // 候选打分：tag 命中 + 文本精确/包含 + class 命中
  const cands = [];
  for (const el of visible) {
    const tag = el.tagName.toLowerCase();
    const own = norm(el.textContent).slice(0, 40);
    const nodeText = getNodeText(el);
    let score = 0;
    const reasons = [];
    if (tagHint && tag === tagHint) { score += 2; reasons.push('tag'); }
    if (textHint) {
      if (own === textHint) { score += 6; reasons.push('text='); }
      else if (own && own.includes(textHint)) { score += 3; reasons.push('text~'); }
      else if (nodeText === textHint) { score += 5; reasons.push('nodetext='); }
      else if (nodeText && nodeText.includes(textHint)) { score += 2; reasons.push('nodetext~'); }
    }
    if (score > 0) cands.push({ el, score, reasons });
  }
  cands.sort((a, b) => b.score - a.score);

  const picked = [];
  for (const c of cands) {
    if (picked.length >= 8) break;
    picked.push(c);
  }

  return picked.map(c => {
    const dirs = getElementDirectory(c.el);
    const xpath = generateXPath(dirs);
    const matched = getElementsByXpath(xpath);
    const outer = c.el.outerHTML || '';
    return {
      score: c.score,
      reasons: c.reasons,
      tag: c.el.tagName.toLowerCase(),
      text: norm(c.el.textContent).slice(0, 60),
      nodeText: getNodeText(c.el),
      class: c.el.getAttribute('class') || '',
      id: c.el.getAttribute('id') || '',
      outerHTML: outer.slice(0, 400),
      pathDirs: dirs,
      generatedXpath: xpath,
      matchCount: matched.length,
      matchIsTarget: matched.length === 1 && matched[0] === c.el,
    };
  });
}
"""


# ------------------------- 定位器健康检查 -------------------------
def check_locator(page, summary):
    """在页面上验证旧定位器是否命中。返回 (命中数, 说明)。"""
    xp = summary.get("xpath")
    css = summary.get("cssSelector")
    counts = {}
    if xp:
        try:
            counts["xpath"] = page.locator(f"xpath={xp}").count()
        except Exception as e:
            counts["xpath"] = f"ERR {str(e)[:60]}"
    if css:
        try:
            counts["css"] = page.locator(css).count()
        except Exception as e:
            counts["css"] = f"ERR {str(e)[:60]}"
    # pathDirs 还原出的 xpath（引擎真实用的）
    dirs = summary.get("pathDirs") or []
    if dirs:
        gen = generate_xpath(dirs)
        try:
            counts["pathDirs_xpath"] = page.locator(f"xpath={gen}").count()
            counts["pathDirs_xpath_str"] = gen
        except Exception as e:
            counts["pathDirs_xpath"] = f"ERR {str(e)[:60]}"
    return counts


def diagnose(db, robot_id, headless=True, timeout=30000):
    """逐元素打开其 URL，检查定位器是否还活着。"""
    elems = list_elements(db, robot_id)
    results = []
    sync_playwright = _require_playwright()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()
        for el in elems:
            s = element_summary(el["data"])
            url = s.get("url") or s.get("tabUrl")
            rec = {"element_id": el["element_id"], "name": el["element_name"], "url": url}
            if not url:
                rec["status"] = "NO_URL"
                results.append(rec)
                continue
            try:
                page.goto(url, timeout=timeout, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                counts = check_locator(page, s)
                rec["counts"] = counts
                vals = [v for k, v in counts.items() if k.endswith("xpath") or k == "css"]
                alive = any(isinstance(v, int) and v > 0 for v in vals)
                rec["status"] = "ALIVE" if alive else "BROKEN"
                rec["new_xpath_from_pathDirs"] = counts.get("pathDirs_xpath_str")
            except Exception as e:
                rec["status"] = "ERROR"
                rec["error"] = str(e)[:200]
            results.append(rec)
            print(f"  [{rec['status']:<6}] {el['element_name']:<22} {url}")
        browser.close()
    return results


# --------------------------- 重新定位 ---------------------------
def relocate_one(page, el, timeout=30000):
    """
    对单个失效元素重新定位：
      1. 打开原 URL；
      2. 用语义线索（tag + text）在真实 DOM 里找候选；
      3. 复刻插件算法生成新 pathDirs；
      4. 校验生成的 xpath 唯一命中候选元素。
    """
    s = element_summary(el["data"])
    url = s.get("url") or s.get("tabUrl")
    if not url:
        return {"ok": False, "reason": "NO_URL"}

    page.goto(url, timeout=timeout, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)

    tag_hint = (s.get("tag") or "").strip()
    # path.tag 是业务语义名（如"行元素"），不是 HTML 标签；用真实标签兜底
    html_tags = {"行元素", "按钮", "输入框", "链接", "图像", "文本", "下拉框", "复选框", "单选框"}
    tag_hint = "" if tag_hint in html_tags else tag_hint
    text_hint = (s.get("text") or "").strip()

    cands = page.evaluate(JS_REBUILD, {"targetIndex": 0, "tagHint": tag_hint, "textHint": text_hint})
    if not cands:
        return {"ok": False, "reason": "NO_CANDIDATE"}

    # 优先取「生成的 xpath 唯一命中自己」且文本最贴近的候选
    best = None
    for c in cands:
        if c["matchIsTarget"]:
            best = c
            break
    if best is None:
        # 退而求其次：匹配数==1 的最高分候选
        singles = [c for c in cands if c["matchCount"] == 1]
        best = singles[0] if singles else cands[0]

    new_dirs = best["pathDirs"]
    new_xpath = generate_xpath(new_dirs)
    new_css = generate_css_selector(new_dirs)

    # 二次校验：新 xpath 在页面上必须唯一命中
    try:
        hit = page.locator(f"xpath={new_xpath}").count()
    except Exception as e:
        return {"ok": False, "reason": f"VERIFY_ERR {str(e)[:80]}", "candidate": best}

    return {
        "ok": hit == 1,
        "reason": "UNIQUE" if hit == 1 else f"MATCH_{hit}",
        "match_count": hit,
        "old_xpath": s.get("xpath"),
        "old_css": s.get("cssSelector"),
        "new_xpath": new_xpath,
        "new_css": new_css,
        "new_pathDirs": new_dirs,
        "candidate": {k: best[k] for k in ("score", "reasons", "tag", "text", "class", "id", "outerHTML", "matchCount")},
    }


def apply_fix(db, el, new_dirs, new_xpath, new_css, dry_run=True):
    """把新的 element_data 写回 c_element（写前备份）。"""
    data = json.loads(json.dumps(el["data"]))
    p = data.setdefault("path", {})

    # 新 pathDirs 来自浏览器（attr.value 是纯字符串），
    # 写库时按库内既有格式包成 {"rpa":"special","value":[{"type":"other","value":X}]}
    wrapped_dirs = []
    for d in new_dirs:
        nd = dict(d)
        nd["attrs"] = [
            dict(a, value=wrap_special(a.get("value", "")))
            for a in d.get("attrs", []) or []
        ]
        wrapped_dirs.append(nd)

    p["pathDirs"] = wrapped_dirs
    p["xpath"] = wrap_special(new_xpath)
    p["cssSelector"] = wrap_special(new_css)

    new_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    b64 = base64.b64encode(new_json.encode("utf-8")).decode("ascii")

    targets = el["rows"]
    if dry_run:
        return {"written": 0, "targets": len(targets), "dry_run": True}

    # 备份
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bpath = os.path.join(BACKUP_DIR, f"c_element_{el['element_id']}_{ts}.json")
    old_b64 = db.sql_rows(
        "SELECT id, REPLACE(TO_BASE64(element_data),'\\n','') FROM c_element "
        f"WHERE element_id='{el['element_id']}'"
    )
    with open(bpath, "w", encoding="utf-8") as f:
        json.dump({
            "element_id": el["element_id"], "element_name": el["element_name"],
            "rows": [{"id": r[0], "element_data_b64": r[1]} for r in old_b64],
        }, f, ensure_ascii=False, indent=2)

    for row in targets:
        db.sql_exec(
            "UPDATE c_element SET element_data=FROM_BASE64('"
            + b64 + "') WHERE id=" + str(row["id"])
        )
    return {"written": len(targets), "targets": len(targets), "backup": bpath, "dry_run": False}


# ------------------------------ 入口 ------------------------------
def main():
    ap = argparse.ArgumentParser(description="星辰开源 · 元素重新定位器")
    ap.add_argument("--robot-id", help="目标 robot_id")
    ap.add_argument("--list", action="store_true", help="列出元素清单")
    ap.add_argument("--check", action="store_true", help="诊断：旧定位器是否还命中")
    ap.add_argument("--fix", action="store_true", help="重新定位失效元素")
    ap.add_argument("--apply", action="store_true", help="真正写回数据库（默认 dry-run）")
    ap.add_argument("--headed", action="store_true", help="显示浏览器窗口（默认 headless）")
    ap.add_argument("--only", help="只处理指定 element_id")
    args = ap.parse_args()

    if not (args.list or args.check or args.fix):
        ap.error("请指定 --list / --check / --fix 之一")

    db = ServerDb()
    try:
        if args.list:
            elems = list_elements(db, args.robot_id)
            print(f"共 {len(elems)} 个元素（robot_id={args.robot_id or 'ALL'}）")
            for el in elems:
                s = element_summary(el["data"])
                print(f"  {el['element_id']}  {el['element_name']:<24} "
                      f"tag={s.get('tag'):<8} text={s.get('text')!r:<12} "
                      f"xp={s.get('xpath')!r}")
                print(f"      url={s.get('url')}  rows={len(el['rows'])}")
            return 0

        if args.check:
            print("=== 定位器健康检查 ===")
            res = diagnose(db, args.robot_id, headless=not args.headed)
            broken = [r for r in res if r["status"] == "BROKEN"]
            print(f"\n统计：总 {len(res)}，失效 {len(broken)}")
            for r in broken:
                print(f"  ✗ {r['name']}  ({r.get('counts',{}).get('xpath')} 命中)")
                print(f"     旧 xpath: {r.get('counts',{}).get('pathDirs_xpath_str')}")
            return 0

        if args.fix:
            elems = list_elements(db, args.robot_id)
            if args.only:
                elems = [e for e in elems if e["element_id"] == args.only]
            report = []
            sync_playwright = _require_playwright()
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=not args.headed)
                ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
                page = ctx.new_page()
                for el in elems:
                    s = element_summary(el["data"])
                    print(f"\n--- {el['element_name']} ({el['element_id']})")
                    try:
                        counts_check = None
                        url = s.get("url") or s.get("tabUrl")
                        if url:
                            page.goto(url, timeout=30000, wait_until="domcontentloaded")
                            page.wait_for_timeout(1200)
                            counts_check = check_locator(page, s)
                        alive = any(isinstance(v, int) and v > 0
                                    for k, v in (counts_check or {}).items()
                                    if k.endswith("xpath") or k == "css")
                        print(f"    旧定位器命中: {counts_check}")
                        if alive:
                            print("    ✓ 仍有效，跳过")
                            report.append({"name": el["element_name"], "action": "SKIP_ALIVE"})
                            continue

                        res = relocate_one(page, el)
                        if not res.get("ok"):
                            print(f"    ✗ 重新定位失败: {res.get('reason')}")
                            report.append({"name": el["element_name"], "action": "FAILED",
                                           "reason": res.get("reason")})
                            continue
                        print(f"    ✓ 新 xpath: {res['new_xpath']}")
                        print(f"      候选: {res['candidate']['tag']} "
                              f"text={res['candidate']['text']!r} class={res['candidate']['class']!r}")
                        wr = apply_fix(db, el, res["new_pathDirs"], res["new_xpath"],
                                       res["new_css"], dry_run=not args.apply)
                        print(f"    写回: {wr}")
                        report.append({"name": el["element_name"], "action": "FIXED",
                                       "old_xpath": res.get("old_xpath"),
                                       "new_xpath": res["new_xpath"], "write": wr})
                    except Exception as e:
                        print(f"    ✗ 异常: {e}")
                        report.append({"name": el["element_name"], "action": "ERROR", "error": str(e)[:200]})
                browser.close()

            out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "docs", "relocate-report.json")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print(f"\n报告已写入 {out}")
            return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
