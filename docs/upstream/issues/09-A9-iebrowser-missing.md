# [BUG] 字典里有 36 个 IE 系原子，发布包里没有 astronverse.iebrowser 模块

- **组件**：`engine/components`（缺 `astronverse-iebrowser`）+ 原子字典 / 前端
- **影响版本**：`main`（2026-11-05）、v1.1.7
- **严重级别**：P2 —— 36 个原子可拖出、可保存，执行必 `ModuleNotFoundError`

## 1. 现象

从「浏览器 → IE」分类拖出任意原子（`IeBrowserElement.click`、`IeBrowserSoftware.open`、`IeBrowserScript.run` …）：

- 设计器里显示正常，能配参数、能保存、能发布
- 执行到该步骤，直接报：

```
ModuleNotFoundError: No module named 'astronverse.iebrowser'
```

## 2. 证据

### 2.1 main 的 components 目录里没有这个包

`engine/components` 下共 26 个组件，**没有 `astronverse-iebrowser`**：

```
astronverse-ai          astronverse-browser     astronverse-control
astronverse-cua         astronverse-database    astronverse-dataprocess
astronverse-datatable   astronverse-dialog      astronverse-email
astronverse-encrypt     astronverse-enterprise  astronverse-excel
astronverse-input       astronverse-network     astronverse-openapi
astronverse-pdf         astronverse-report      astronverse-script
astronverse-smart       astronverse-software    astronverse-system
astronverse-verifycode  astronverse-vision      astronverse-window
astronverse-winelement  astronverse-word
```

对照：现在的浏览器能力在 `astronverse-browser`（Chrome/Edge 插件方案），IE 走的是另一套 COM 自动化，两者不是同一个包。

### 2.2 但字典里还有 36 个 IE key

对 `c_atom_meta_new`（357 条）逐个反射实现类，结果：

```json
{
  "checked_at": "2026-11-02 11:34:16",
  "total": 357,
  "ok": 291,
  "missing": {
    "IeBrowserSoftware.*": 18,
    "IeBrowserElement.*":  17,
    "IeBrowserScript.*":    1
  }
}
```

缺失的 39 条里 **36 条是 IE 系**，剩下 3 条是另两个模块的命名不一致（已单独提）。

抽样：

```
IeBrowserElement.wait_element     → astronverse.iebrowser.browser_element 导入失败
IeBrowserElement.click            → 同上
IeBrowserElement.input            → 同上
IeBrowserElement.hover_over       → 同上
IeBrowserElement.screenshot       → 同上
IeBrowserSoftware.open            → astronverse.iebrowser.browser_software 导入失败
```

## 3. 影响

- 用户以为 IE 自动化可用，实际 100% 不可用
- 一旦流程里混入 IE 原子，整条流程在**运行时**才失败，且失败点在流程中途
- 排查方向容易被误导：报错是「找不到模块」，用户会去重装/升级，但装什么都补不上，因为包根本没发布

比"功能缺失"更糟的是**缺失得无声无息**：前端不提示、字典不标记、编译期不校验。

## 4. 修复建议

三选一，明确表态即可：

### A. 恢复该组件（如果要继续支持 IE）

- 补 `engine/components/astronverse-iebrowser`（`browser_element.py` / `browser_software.py` / `browser_script.py`）
- 在 `engine/components/` 的构建清单里加入，确保打包进发布物
- 依赖 `pywin32`（COM）

### B. 从字典与前端移除（如果不打算支持）

- 清理 `c_atom_meta_new` 里 36 条 `IeBrowser*`
- 前端「浏览器」分类下隐藏 IE 分组
- 迁移脚本里对已有流程给出提示（含 IE 原子的流程需人工替换为 `Browser.*`）

### C. 至少让它在能暴露的时候暴露

- 引擎启动时做一次「字典 key → 实现方法」自检，缺失的写 warn 日志并上报（这次就是我们自己跑反射才发现 39 条对不上）
- 前端对自检失败的原子置灰并提示「当前版本未提供该能力」

**至少要做 C** —— 无论最终选 A 还是 B，这个自检都值得有：它把"运行时才炸"提前到了"启动时就知道"。

## 5. 复现方式

```python
python -c "import astronverse.iebrowser"
# ModuleNotFoundError: No module named 'astronverse.iebrowser'
```

或在流程里拖入 `IeBrowserElement.click` 直接运行。

## 6. 建议的验收

- 若保留：36 个 key 全部能反射到实现，`import astronverse.iebrowser` 成功
- 若移除：字典里 `IeBrowser%` 查询结果为 0，前端不再展示该分组
- 无论哪种：字典 key 与实现方法的自检结果应为 0 缺失
