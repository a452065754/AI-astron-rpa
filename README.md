# 星辰开源

> 参考 iFlytek 开源生态（[astron-agent](https://github.com/iflytek/astron-agent) / [astron-rpa](https://github.com/iflytek/astron-rpa)）的理念，打造面向「**决策（Agent）+ 执行（RPA）**」闭环的开源项目。

## 项目状态

- **阶段**：调研与立项（2026-10-28 启动）
- **当前成果**：已完成对 astron-agent、astron-rpa 的架构、技术栈、核心模块与生态调研
  - 调研文档：[docs/astron-调研.md](docs/astron-调研.md)
- **待确认**：项目定位、技术栈取舍、首阶段范围（见调研文档第 8 节）

## 参考项目速览

| 项目 | 定位 | 协议 | 关键技术 |
| --- | --- | --- | --- |
| astron-agent | 企业级 Agentic Workflow 平台 | Apache 2.0 | 工作流编排、模型管理、MCP 工具、Casdoor 鉴权、Docker |
| astron-rpa | 企业级 RPA 桌面应用 | Open Source License（自定义） | Vue3+Electron、Spring Boot+FastAPI、Python 引擎、300+ 原子能力 |

两者双向打通：Agent 可调用 RPA 节点，RPA 可调用 Agent 工作流。

## 目录结构

```
星辰开源/
├── README.md              # 本文件：项目首页
└── docs/
    └── astron-调研.md      # 参考项目调研报告
```

## 下一步

1. 与团队确认项目定位与技术栈（见调研文档第 8 节待确认问题）。
2. 产出架构设计文档 / 最小可运行骨架（MVP）。
3. 选定并落实开源协议。
