**English** | [简体中文](README.zh-CN.md)

# 个人知识图谱 · Personal Knowledge Graph

> 把一个 Markdown 文件夹变成自包含的知识库 + 可交互知识图谱：纯文件、零外部服务，适用于任何支持 SKILL.md 的 Agent（ZCode、Claude Code 等）。

![图谱总览](assets/graph-overview.png)

## 功能

- **元数据层** — 为每篇文档起草 frontmatter：日期、领域、状态（`active / superseded / reference`）、一句话结论。
- **三类关系** — `supersedes`（演进：谁推翻了谁）、`related`（语义相关）、`[[双链]]`（正文引用），构成带时间维度的思路演进图。
- **可重建的蒸馏层** — 决策登记簿、按领域组织的图谱邻接表、机器可读 JSON、健康报告（缺元数据/断链清单），随时秒级全量重建。
- **单文件 HTML 图谱页** — 瑞士档案风格交互页面：力导向图谱、卡片节点（标题 + 一句话含义）、搜索筛选、档案详情面板、清单表格视图；零依赖，离线可开。

| 节点档案详情 | 清单视图 |
|---|---|
| ![节点详情](assets/node-detail.png) | ![清单](assets/table-view.png) |

## 安装

```bash
git clone <本仓库> ~/.agents/skills/personal-knowledge-graph
```

支持 SKILL.md 的 Agent（ZCode、Claude Code 等）会自动发现并加载。

## 使用

直接对 Agent 说：

| 你说 | 模式 |
|---|---|
| "帮我把 ~/notes 建成知识库和知识图谱" | 建库 |
| "更新一下知识库" | 增量迭代 |
| "回忆一下我之前关于 X 的思路和结论" | 决策前回忆 |

也可手动重建生成物：

```bash
python3 gen_kb_graph.py <知识库目录> [--output graph] [--ignore "books,old"] [--no-html]
```

## 设计原则

1. **文档是唯一事实源。** Agent 只往源文件写 frontmatter 和双链；`graph/` 目录随时可删、可完整重建。
2. **AI 起草、人确认。** 元数据与关系建议在写入前以样本和清单形式展示，确认后才落盘。
3. **零服务。** 纯标准库脚本 + 单个 HTML 文件；约 500 篇文档内体验最佳，超出后元数据和双链可无损迁移 LightRAG / Neo4j。

## 文件结构

```
personal-knowledge-graph/
├── SKILL.md               # Agent 工作流程
├── references/schema.md   # 完整规范（字段 / 边 / 输出模板）
├── scripts/gen_kb_graph.py# 图谱生成脚本（纯标准库）
└── assets/                # 截图
```

## 许可

[MIT](LICENSE)
