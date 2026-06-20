# Pier Agent Definition Format

> 定义 pier agent 的 `.md` 文件格式。
> 格式对齐 pi-subagent 社区规范（以 nicobailon/pi-subagents 为参考）。
> 关联: ADR-0004, docs/knowledge/pi-agent-rpc-mode.md

---

## 一、设计原则

1. **对齐 pi-subagent 格式** — frontmatter 字段名与语义与 pi-subagent 社区规范保持一致，不另创命名
2. **Pi CLI 直映射** — frontmatter 字段直接映射到 Pi CLI flag 或 RPC 初始化命令
3. **单一源头** — `.md` 文件是 agent 的唯一来源，无 overlay 层

---

## 二、文件位置与发现

### 2.1 扫描路径

发现优先级从低到高（高优先级覆盖低优先级的同名 agent）：

```
~/.pi/agent/extensions/subagent/agents/   # [可选] pi-subagent 内置 agent（通过 --discover-pi-subagent-agents 开启）
├── scout.md
├── oracle.md
├── planner.md
└── reviewer.md
                                        # 优先级 ▲
~/.pi/agent/agents/                     # 用户全局 agent
├── review.md
├── deploy-check.md
└── security-audit.md
                                        # 优先级 ▲
.pi/agents/                             # 项目 agent（最高）
├── review.md
└── hotfix.md
```

### 2.2 发现规则

- 扫描各路径下所有 `**/*.md`
- frontmatter `name` 作为 agent 标识符
- 同名时高优先级覆盖低优先级，即 项目 > 用户 > 内置
- 内置 agent 路径默认**不扫描**，需 pier 启动时加 `--discover-pi-subagent-agents` 开启

运行时发现语义：

- Pier 启动时只加载全局 agent，作为稳定基础集合
- 项目 agent 不按 “Pier 服务自身所在仓库” 加载
- 当 MCP/REST 调用提供 `cwd` 时，Pier 才会根据这个 `cwd` 向上定位所属项目根，再读取该项目根下的 `.pi/agents/`
- 因此项目 agent 的作用域始终属于“当前被操作的项目”，不是 Pier 项目本身

### 2.3 内置 agent 路径说明

`~/.pi/agent/extensions/subagent/agents/` 是 `pi-subagents`（nicobailon/pi-subagents）包安装后的内置 agent 目录。当用户执行 `pi install npm:pi-subagents` 后，该目录存在于 `~/.pi/agent/extensions/subagent/agents/`。这些内置 agent 可在 pier 中复用，无需重复创建同名 `.md` 文件。

> 不通过 `--discover-pi-subagent-agents` 开启时，pier 不感知此路径。

---

## 三、Frontmatter 字段

```
---
name: review
description: Review code changes for bugs, security, and style
model: anthropic/claude-sonnet-4-20250514
thinking: medium
tools:
  - read
  - bash
  - mcp:chrome-devtools
extensions:
inheritSkills: false
skills:
  - browser-testing
inheritProjectContext: false
systemPromptMode: replace
autoCompaction: true
autoRetry: true
---
```

### 3.1 必填字段

| 字段 | 类型 | pi-subagent | 说明 |
|------|------|-------------|------|
| `name` | string | ✅ 标准 | 唯一标识。max 64 字符，小写字母、数字、连字符，不能以连字符开头或结尾 |
| `description` | string | ✅ 标准 | MCP `list_agents` 显示用描述。max 1024 字符 |

### 3.2 pi-subagent 标准字段（支持的）

| 字段 | 类型 | 默认 | pi-subagent | Pi CLI | 说明 |
|------|------|------|-------------|--------|------|
| `model` | string | *(Pi 默认)* | ✅ 标准 | `--model` | `provider/id` 格式。空值使用 Pi 默认模型 |
| `thinking` | string | `"medium"` | ✅ 标准 | `--thinking` | `off`/`minimal`/`low`/`medium`/`high`/`xhigh` |
| `tools` | string[] | `[read,bash,edit,write]` | ✅ 标准 | `--tools` | 内置工具 allowlist。支持 `mcp:server-name` 引用 MCP 工具（需 `pi-mcp-adapter`） |
| `extensions` | string[] | *(不指定=全部加载)* | ✅ 标准 | `--no-extensions` + `-e` | **不指定**：加载全部已发现扩展；**空值** `extensions:`：不加载扩展（`--no-extensions`）；**列表**：`--no-extensions -e a -e b` 白名单加载 |
| `skills` | string[] | `[]` | ✅ 标准 | `--skill` | 按 name 加载指定技能。Pier 从各技能目录按 frontmatter name 解析出路径 |
| `inheritSkills` | boolean | `true` | ✅ 标准 | `--no-skills` | `false` 时禁用技能文件发现（`--no-skills`） |
| `inheritProjectContext` | boolean | `true` | ✅ 标准 | `--no-context-files` | `false` 时禁用 AGENTS.md/CLAUDE.md 发现 |
| `systemPromptMode` | string | `"replace"` | ✅ 标准 | `--system-prompt` / `--append-system-prompt` | `replace` 替换默认 system prompt；`append` 追加到尾部 |

**`extensions` 三态语义**（完全对齐 pi-subagent）：

```yaml
# 不写该字段 → 加载全部已发现扩展
model: ...

# extensions: （空值）→ --no-extensions，不加载任何扩展
extensions:

# extensions: [a, b] → --no-extensions -e a -e b
extensions:
  - /path/to/ext-a.ts
  - pi-mcp-adapter
```

**`tools` 三态语义**：

```yaml
# 不写该字段 → 使用 Pi 默认工具集
model: ...

# tools: （空值）→ 不赋予任何工具
tools:

# tools: [a, b] → 白名单
tools:
  - read
  - bash
```

**`tools` 与 MCP 工具**：

`mcp:server-name` 语法由 `pi-mcp-adapter` 的 subagent integration 支持。`tools` 中可写 `mcp:chrome-devtools` 来引用该 MCP server 注册的所有工具。未安装 `pi-mcp-adapter` 时 `mcp:` 前缀无意义。

**`model` 与 `thinking` 的交互**：

`thinking` 值会在运行时拼接为 `model:thinking` 后缀，除非 model 已包含 `:<level>` 后缀。例如 `model: claude-sonnet-4` + `thinking: high` → 实际运行 `--model claude-sonnet-4:high`。

**Skills 解析顺序**：当 frontmatter `skills` 指定名称列表时，Pier 按以下优先级查找对应 SKILL.md：

| 范围 | 路径 | 来源 |
|------|------|------|
| Project | `<cwd>/.pi/skills/` | Pi-standard |
| Project | `<cwd>/.agents/skills/` | Agent Skills spec |
| User | `$PI_CODING_AGENT_DIR/skills/` (默认 `~/.pi/agent/skills/`) | Pi-standard |
| User | `~/.agents/skills/` | Agent Skills spec |
| User | `~/.pi/skills/` | Legacy (pre-Pi) |

高优先级覆盖低优先级同名 skill，即 Project > User。`inheritSkills: false` 时跳过所有用户级路径，仅扫描项目路径。

### 3.3 pi-subagent 标准字段（忽略的）

| 字段 | 来源 | 忽略原因 |
|------|------|---------|
| `package` | nicobailon/pi-subagents | Pier 的 agent 命名空间由文件路径管理，无需 package 前缀 |
| `fallbackModels` | nicobailon/pi-subagents | Pier 不实现模型级故障转移，由 MCP 客户端自行决定重试策略 |
| `output` | nicobailon/pi-subagents | 子进程间文件共享模式不适用于 pier 的 MCP 架构 |
| `defaultReads` | nicobailon/pi-subagents | 同上 |
| `defaultProgress` | nicobailon/pi-subagents | 同上 |
| `completionGuard` | nicobailon/pi-subagents | Pier 通过 RPC abort 控制，不需要 frontmatter 配置 |
| `interactive` | nicobailon/pi-subagents | RPC 模式无交互终端 |
| `maxSubagentDepth` | nicobailon/pi-subagents | Pier 不嵌套 subagent |
| `display_name` | tintinweb/pi-subagents | 仅使用 `name` 作为显示标识 |
| `prompt_mode` | tintinweb/pi-subagents | 使用更广泛的 `systemPromptMode` 命名 |
| `inherit_context` | tintinweb/pi-subagents | 使用更明确的 `inheritProjectContext` |
| `memory` | tintinweb/pi-subagents | Pier 不做持久化记忆层 |
| `disallowed_tools` | tintinweb/pi-subagents | 使用 `tools` allowlist + pier 层排除逻辑 |
| `max_turns` | tintinweb/pi-subagents | 控制在 MCP 调用方/Cli 端 |
| `run_in_background` | tintinweb/pi-subagents | RPC 模式为同步请求-响应模型 |
| `isolated` | tintinweb/pi-subagents | 通过 `extensions:` 空值 + `inheritSkills: false` 组合实现等价效果 |
| `defaultContext` | nicobailon/pi-subagents | Pier 每次运行是独立 session，不 fork |

### 3.4 Pier 扩展字段

| 字段 | 类型 | 默认 | 映射 | 说明 |
|------|------|------|------|------|
| `enabled` | boolean | `true` | *(Pier 层控制)* | `false` 时不在 MCP `list_agents` 中暴露。无需删除 `.md` 即可下线 |
| `autoCompaction` | boolean | `true` | RPC: `set_auto_compaction` | 是否启用自动上下文压缩 |
| `autoRetry` | boolean | `true` | RPC: `set_auto_retry` | 是否在临时错误后自动重试 |

**`autoCompaction` 与 `autoRetry`**：在 RPC 连接建立后、首次 `prompt` 前，Pier 自动发送：

```json
{"type": "set_auto_compaction", "enabled": true}
{"type": "set_auto_retry", "enabled": true}
```

---

## 四、完整示例

### 4.1 `~/.pi/agent/agents/code-review.md`

```markdown
---
name: code-review
description: Review pull request changes for bugs and style issues
model: anthropic/claude-sonnet-4-20250514
thinking: high
tools:
  - read
  - bash
  - grep
systemPromptMode: replace
---
You are a senior code reviewer. Analyze the provided code changes and:
1. Identify potential bugs and logic errors
2. Check for security vulnerabilities
3. Suggest style improvements
4. Verify test coverage

Be concise and focus on actionable findings.
```

### 4.2 `.pi/agents/debug.md`

```markdown
---
name: debug
description: Debug browser issues with Chrome DevTools
model: anthropic/claude-sonnet-4-20250514
thinking: high
tools:
  - read
  - bash
  - mcp:chrome-devtools
  - mcp:github
inheritSkills: false
skills:
  - browser-testing
systemPromptMode: append
---
Use Chrome DevTools to inspect the browser state, check console errors,
and analyze network requests. Make targeted fixes based on findings.
```

### 4.3 `~/.pi/agent/agents/shell.md`

```markdown
---
name: shell
description: Execute a single shell command safely
model: openai/gpt-4o-mini
thinking: off
tools:
  - bash
extensions:
inheritSkills: false
inheritProjectContext: false
systemPromptMode: replace
autoRetry: false
---
Execute the requested shell command and return its output.

Rules:
- Run exactly one command per request
- Never modify files
- Never install software
```

---

## 五、映射总表

### 5.1 Pi CLI Flags

| Frontmatter 字段 | Pi CLI Flag | 条件 |
|-----------------|-------------|------|
| `model` | `--model <value>` | — |
| `thinking` | `--thinking <value>` | — |
| `tools` | `--tools <list>` | `tools` 非空 |
| `extensions`（不指定） | *(不加 flag)* | Pi 加载全部已发现扩展 |
| `extensions`（空值） | `--no-extensions` | `extensions:` |
| `extensions`（列表） | `--no-extensions -e a -e b` | 白名单模式 |
| `inheritSkills: false` | `--no-skills` | — |
| `skills` | `--skill <path>` (多个) | `skills` 非空 |
| `inheritProjectContext: false` | `--no-context-files` | — |
| `systemPromptMode: replace` | `--system-prompt "<body>"` | — |
| `systemPromptMode: append` | `--append-system-prompt "<body>"` | — |

### 5.2 RPC 初始化命令

| 字段 | RPC 命令 | 时机 |
|------|---------|------|
| `autoCompaction` | `{"type": "set_auto_compaction", "enabled": true/false}` | session 启动后、首次 prompt 前 |
| `autoRetry` | `{"type": "set_auto_retry", "enabled": true/false}` | session 启动后、首次 prompt 前 |

### 5.3 强制默认值（Hardcoded）

| 设置 | 值 | 说明 |
|------|----|------|
| `PI_OFFLINE` | `1` | 禁止 Pi 启动时连接 pi.dev |
| 子进程 `cwd` | 强制 | 通过 Node `spawn(..., { cwd })` 设置 Pi 工作目录，而不是传递 Pi CLI flag |
| `--approve` | 强制 | 信任运行目录 cwd 下 `.pi/` 的项目配置 |
| `--name <agent-name>` | 强制 | sets session display name from agent name |
| `--no-prompt-templates` | 强制 | Pier 不管理 prompt template |
| `--mode rpc` | 强制 | 仅使用 RPC 模式 |

---

## 六、Session 存储

每次 agent 运行的会话记录存储在：

```
~/.pier/sessions/<project-path>/<timestamp>_<uuidv7>.jsonl
```

`<project-path>` 是 cwd 的路径安全编码，`/` 替换为 `-`，首尾加 `--`。示例：

```
~/.pier/sessions/--Users-changjunjie-Developer-my-github-orgs-pier-sh-pier--/
```

Pi 自动生成文件名（`<timestamp>_<uuidv7>.jsonl`），可通过 RPC `get_state` 获取 sessionId 用于后续 resume。

---

## 七、安全边界

| 风险 | 缓解 |
|------|------|
| 技能文件注入恶意指令 | 技能来自用户 `~/.pi/` 或项目 `.pi/`，属用户已信任范围 |
| Pi 子进程访问网络 | `PI_OFFLINE=1` 禁止 Pi 自身网络请求；模型 API 调用不受影响 |
| MCP 工具链式调用 | Pier 检测递归 MCP 调用（`PI_OFFLINE=1` 已阻断网络递归） |
