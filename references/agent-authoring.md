# Agent 编写规范（pier 0.3）

一个 agent = 一个 Markdown：YAML frontmatter（可移植字段）+ 正文（角色约束）。
身份只用 `name`（不再有 `package` / `aliases`）。

## 1. 最小模板

```markdown
---
name: sql-reviewer
description: Review SQL migrations for correctness and lock risk. Read-only.
tools: read, grep, find, ls
thinking: medium
---

You are a SQL migration reviewer.

Scope:
- Only review; never edit files.
- Focus on correctness, locking/downtime risk, and rollback safety.

Output:
- Findings ordered by severity, each with file:line and a one-line reason.
- End with "OK to merge" or "Changes needed".
```

## 2. 支持的 frontmatter

| 字段 | Pi 效果 | 说明 |
|---|---|---|
| `name` | `--name` | 必填或用文件名；字母/数字/`.`/`_`/`-`，≤64；查找键 |
| `description` | 无 | 列表/选择用，建议填写 |
| `advertise` / `async` | 无 | 元数据 only |
| `model` | `--model` 原样 | 例 `anthropic/claude-sonnet-4-5`、`provider/id:low`；`inherit`/空 = 省略 |
| `thinking` | `--thinking` | off/minimal/low/medium/high/xhigh/max；model 带 `:level` 时后缀优先 |
| `defaultProvider` | `--provider` | 仅当 model 无 `provider/` 前缀 |
| `tools` | `--tools` CSV | Pi 原生工具；空值 = `--no-tools`；扩展专用工具名会丢弃并提示 |
| `excludeTools` | `--exclude-tools` CSV | |
| `systemPromptMode` | `--system-prompt` / `--append-system-prompt` | 默认 `replace`；`append` 追加 |
| `skills` / `skill` | 重复 `--skill <path>` | 按名从 Pi 的 project/user/config/package 技能位置解析；空列表或 `inheritSkills: false` → `--no-skills`；未解析到则报错 |
| `skillPath` | 重复 `--skill <path>` | 相对 agent md 目录；绝对路径原样 |
| `extensions` | `--no-extensions` + 重复 `--extension` | 空列表 → 仅 `--no-extensions`；相对路径相对 agent md；`builtin:`/`npm:`/`git:`/`http(s):`/`file:`/绝对路径透传 |
| `subagentOnlyExtensions` | 并入 `--extension` allowlist | 同上 |
| `inheritProjectContext` | `--no-context-files` | 仅当显式 `false` |
| `inheritSkills` | `--no-skills` | 仅当显式 `false` |

`inheritGlobalContext`、`launcher`、`timeoutMs`、`acceptance*`、`memory`、`output`、嵌套相关字段等：**忽略、无效果**（stderr 一行提示）。

## 3. 正文（body）

正文经 Pi `--system-prompt`（或 `--append-system-prompt`）传入，**不再**拼进用户 prompt。
用户 prompt 只有：

```
## Context handoff    ← --handoff-file（可选）
## Task               ← 任务
```

`--instructions` / `--role` 覆盖正文；`--no-agent-body` 不传 system-prompt 相关 flag。
`pier resume` 不重新发送 system prompt（沿用会话里原来的）。

正文只写可复用的角色约束；具体任务、路径、临时上下文放进 task。

## 4. 选模型与工具

- 只读调研/评审：`tools: read, grep, find, ls`；可加 `excludeTools: write, edit`。
- 需要改代码：省略 `tools`（Pi 默认）或显式列出。
- 纯文本：`tools:` 空 → `--no-tools`。
- `thinking`：简单检索 low，评审/规划 medium，难推理 high。

## 5. 验证与落盘

```bash
pier agents validate /tmp/sql-reviewer.md
pier run --agent-file /tmp/sql-reviewer.md "sample" --dry-run
pier agents new sql-reviewer --file /tmp/sql-reviewer.md          # <cwd>/.pier/agents/
pier agents new sql-reviewer --file /tmp/sql-reviewer.md --user   # ~/.pier/agents/
```

- 同名已存在需 `--force`。
- 不要写入本 skill 的 `agents/`（示例，更新会覆盖）。
- 不要写入 pi-subagents 目录；仅当用户明确指定路径时用 `--dir`。
