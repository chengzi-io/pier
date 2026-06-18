# Pi Agent RPC 模式完整说明

> 来源: [pi.dev/docs/latest/rpc](https://pi.dev/docs/latest/rpc) · `@earendil-works/pi-coding-agent` · commit: main

---

## 一、概述

RPC 模式通过 stdin/stdout 上的 JSON 协议实现 Pi Coding Agent 的**无头运行**（headless operation），适合嵌入到其他应用、IDE 或自定义 UI 中。

特点：

- **非交互式** — 不启动 TUI，所有控制通过 stdin 发送 JSON 命令
- **双向通信** — 命令-响应模式 + 事件流
- **JSONL 帧协议** — 严格以 `\n`（LF）作为记录分隔符
- **扩展 UI 子协议** — 支持对话框（select/confirm/input/editor）的 request/response

> 对于 Node.js/TypeScript 项目，也可直接使用 `AgentSession` 类（`@earendil-works/pi-coding-agent`）而无需启动子进程。

---

## 二、启动方式

```bash
pi --mode rpc [options]
```

### 通用启动参数

| 参数 | 说明 |
|------|------|
| `--provider <name>` | 设置 LLM 提供商（anthropic, openai, google 等） |
| `--model <pattern>` | 模型模式或 ID，支持 `provider/id` 和可选 `:<thinking>` |
| `--name <name>` / `-n <name>` | 设置会话显示名称 |
| `--no-session` | 关闭会话持久化（临时模式） |
| `--session-dir <path>` | 自定义会话存储目录 |
| `--approve` / `-a` | 覆盖项目信任策略（信任） |
| `--no-approve` / `-na` | 覆盖项目信任策略（不信任） |

### 完整 CLI 参考

| 类别 | 参数 | 说明 |
|------|------|------|
| **运行模式** | (默认) | 交互式模式 |
| | `-p`, `--print` | 打印响应后退出 |
| | `--mode json` | 输出所有事件为 JSON 行 |
| | `--mode rpc` | RPC 模式（进程集成） |
| | `--export [out]` | 导出会话到 HTML |
| **模型选项** | `--provider <name>` | LLM 提供商 |
| | `--model <pattern>` | 模型模式或 ID |
| | `--api-key <key>` | API 密钥（覆盖环境变量） |
| | `--thinking <level>` | 推理深度: off/minimal/low/medium/high/xhigh |
| | `--models <patterns>` | 逗号分隔的 Ctrl+P 循环模型列表 |
| | `--list-models [search]` | 列出可用模型 |
| **会话选项** | `-c`, `--continue` | 继续最近会话 |
| | `-r`, `--resume` | 浏览并选择会话 |
| | `--session <path\|id>` | 使用指定会话文件或部分 UUID |
| | `--fork <path\|id>` | 从指定会话派生新会话 |
| | `--session-dir <path>` | 自定义会话目录 |
| | `--no-session` | 临时模式（不保存） |
| | `--name <name>`, `-n <name>` | 设置会话显示名称 |
| **工具选项** | `--tools <list>`, `-t <list>` | 白名单特定工具 |
| | `--exclude-tools <list>`, `-xt <list>` | 禁用特定工具 |
| | `--no-builtin-tools`, `-nbt` | 禁用内置工具（保留扩展/自定义工具） |
| | `--no-tools`, `-nt` | 禁用所有工具 |
| **资源选项** | `-e`, `--extension <source>` | 加载扩展（可重复） |
| | `-nc`, `--no-context-files` | 禁用上下文文件加载 |
| | `--no-skills` | 禁用技能加载 |
| | `--no-extensions` | 禁用扩展加载 |
| | `--no-prompts` | 禁用提示模板加载 |
| **环境/其他** | `--cwd <path>` | 设置工作目录 |
| | `-v`, `--verbose` | 详细日志输出（可重复） |
| | `--version` | 显示版本 |
| | `--help` | 显示帮助 |

> 内置工具列表: `read`, `bash`, `edit`, `write`, `grep`, `find`, `ls`

### 非交互模式的信任策略

RPC 模式（以及 `-p`、`--mode json`）**不会显示信任确认提示**。行为由 `defaultProjectTrust` 设置控制：

- `ask`（默认）：忽略项目资源
- `always`：自动信任
- `never`：忽略

可在 `~/.pi/agent/settings.json` 中设置，或通过 `--approve`/`--no-approve` 单次覆盖。

---

## 三、协议与帧格式

### 帧规则（重要）

RPC 模式使用严格的 JSONL 语义：

- 仅以 `\n`（LF）作为记录分隔符
- 可选的 `\r\n` 输入，通过去掉尾部 `\r` 兼容
- **禁止使用** Node.js `readline` 等通用行读取器（它会将 `U+2028`、`U+2029` 也视为换行符，而这些字符可能合法出现在 JSON 字符串中）

### 三种消息类型

| 类型 | 方向 | 说明 |
|------|------|------|
| **命令（Command）** | stdin → agent | JSON 对象，每行一个 |
| **响应（Response）** | agent → stdout | `type: "response"`，标记命令成功/失败 |
| **事件（Event）** | agent → stdout | 异步事件流，不含 `id` 字段 |

所有命令支持可选的 `id` 字段用于请求/响应关联。

---

## 四、命令参考（stdin）

### 4.1 Prompting（提示类）

#### `prompt` — 发送用户提示

```json
{"id": "req-1", "type": "prompt", "message": "Hello, world!"}
```

**支持图片附件的格式：**

```json
{"type": "prompt", "message": "What's in this image?", "images": [{"type": "image", "data": "base64-encoded-data", "mimeType": "image/png"}]}
```

**流式处理中的行为：**

如果 agent 正在流式响应，必须指定 `streamingBehavior`：

```json
{"type": "prompt", "message": "New instruction", "streamingBehavior": "steer"}
```

`streamingBehavior` 取值：
- `"steer"` — 当前 assistant turn 的工具调用执行完毕后，在下次 LLM 调用前交付
- `"followUp"` — 等待 agent 完全停止后交付

> 如果 agent 正在流式响应且未指定 `streamingBehavior`，命令将返回错误。

**扩展命令：** 如果消息是以 `/` 开头的扩展命令（如 `/mycommand`），即使在流式处理中也会立即执行。

**输入展开：** 技能命令（`/skill:name`）和提示模板（`/template`）会在发送/排队前展开。

响应：
```json
{"id": "req-1", "type": "response", "command": "prompt", "success": true}
```

> `success: true` 表示提示已被接受、排队或即时处理。`success: false` 表示在接受前被拒绝。接受后的失败通过正常事件流报告，不会为此请求 id 发送第二个 `response`。

---

#### `steer` — 插入引导消息（流式处理中）

```json
{"type": "steer", "message": "Stop and do this instead"}
```

支持图片：
```json
{"type": "steer", "message": "Look at this instead", "images": [{"type": "image", "data": "base64-encoded-data", "mimeType": "image/png"}]}
```

响应：
```json
{"type": "response", "command": "steer", "success": true}
```

---

#### `follow_up` — 排队后续消息

```json
{"type": "follow_up", "message": "After you're done, also do this"}
```

响应：
```json
{"type": "response", "command": "follow_up", "success": true}
```

---

#### `abort` — 中止当前操作

```json
{"type": "abort"}
```

响应：
```json
{"type": "response", "command": "abort", "success": true}
```

---

#### `new_session` — 开启新会话

```json
{"type": "new_session"}
```

可选关联父会话：
```json
{"type": "new_session", "parentSession": "/path/to/parent-session.jsonl"}
```

响应（未被扩展取消）：
```json
{"type": "response", "command": "new_session", "success": true, "data": {"cancelled": false}}
```

响应（被扩展取消）：
```json
{"type": "response", "command": "new_session", "success": true, "data": {"cancelled": true}}
```

> 可被 `session_before_switch` 扩展事件处理器取消。

---

### 4.2 State（状态查询类）

#### `get_state` — 获取会话当前状态

```json
{"type": "get_state"}
```

响应：
```json
{
  "type": "response",
  "command": "get_state",
  "success": true,
  "data": {
    "model": { /* Model 对象 */ },
    "thinkingLevel": "medium",
    "isStreaming": false,
    "isCompacting": false,
    "steeringMode": "all",
    "followUpMode": "one-at-a-time",
    "sessionFile": "/path/to/session.jsonl",
    "sessionId": "abc123",
    "sessionName": "my-feature-work",
    "autoCompactionEnabled": true,
    "messageCount": 5,
    "pendingMessageCount": 0
  }
}
```

- `model`：完整 Model 对象或 `null`
- `sessionName`：通过 `set_session_name` 设置的值，未设置则省略

---

#### `get_messages` — 获取会话消息列表

```json
{"type": "get_messages"}
```

响应：
```json
{
  "type": "response",
  "command": "get_messages",
  "success": true,
  "data": {"messages": [/* AgentMessage 数组 */]}
}
```

---

### 4.3 Model（模型控制类）

#### `set_model` — 切换模型

```json
{"type": "set_model", "provider": "anthropic", "modelId": "claude-sonnet-4-20250514"}
```

响应包含完整 Model 对象。

---

#### `cycle_model` — 切换到下一个可用模型

```json
{"type": "cycle_model"}
```

响应：
```json
{
  "type": "response",
  "command": "cycle_model",
  "success": true,
  "data": {
    "model": { /* Model 对象 */ },
    "thinkingLevel": "medium",
    "isScoped": false
  }
}
```

> 如果只有一个模型，返回 `null` data。

---

#### `get_available_models` — 列出所有已配置模型

```json
{"type": "get_available_models"}
```

响应：
```json
{
  "type": "response",
  "command": "get_available_models",
  "success": true,
  "data": {
    "models": [/* Model 对象数组 */]
  }
}
```

---

### 4.4 Thinking（推理深度控制）

#### `set_thinking_level` — 设置推理深度

```json
{"type": "set_thinking_level", "level": "high"}
```

可用级别：
| 级别 | 说明 |
|------|------|
| `"off"` | 关闭推理 |
| `"minimal"` | 最小推理 |
| `"low"` | 低 |
| `"medium"` | 中（默认） |
| `"high"` | 高 |
| `"xhigh"` | 极高（仅 OpenAI codex-max 支持） |

响应：
```json
{"type": "response", "command": "set_thinking_level", "success": true}
```

---

#### `cycle_thinking_level` — 循环切换推理深度

```json
{"type": "cycle_thinking_level"}
```

响应：
```json
{
  "type": "response",
  "command": "cycle_thinking_level",
  "success": true,
  "data": {"level": "high"}
}
```

---

### 4.5 Queue Modes（队列模式控制）

#### `set_steering_mode` — 控制引导消息的投递方式

```json
{"type": "set_steering_mode", "mode": "one-at-a-time"}
```

- `"all"` — 当前 assistant turn 完成后投递所有引导消息
- `"one-at-a-time"` — 每次完成一个 assistant turn 投递一条引导消息（默认）

---

#### `set_follow_up_mode` — 控制后续消息的投递方式

```json
{"type": "set_follow_up_mode", "mode": "one-at-a-time"}
```

- `"all"` — agent 完成时投递所有后续消息
- `"one-at-a-time"` — 每次 agent 完成投递一条后续消息（默认）

---

### 4.6 Compaction（上下文压缩）

#### `compact` — 手动执行上下文压缩

```json
{"type": "compact"}
```

带自定义指令：
```json
{"type": "compact", "customInstructions": "Focus on code changes"}
```

响应：
```json
{
  "type": "response",
  "command": "compact",
  "success": true,
  "data": {
    "summary": "Summary of conversation...",
    "firstKeptEntryId": "abc123",
    "tokensBefore": 150000,
    "details": {}
  }
}
```

---

#### `set_auto_compaction` — 启用/禁用自动压缩

```json
{"type": "set_auto_compaction", "enabled": true}
```

---

### 4.7 Retry（自动重试控制）

#### `set_auto_retry` — 启用/禁用自动重试

```json
{"type": "set_auto_retry", "enabled": true}
```

针对临时错误（overloaded、rate limit、5xx）的自动重试。

---

#### `abort_retry` — 中止正在进行的重试

```json
{"type": "abort_retry"}
```

---

### 4.8 Bash（Shell 命令执行）

#### `bash` — 执行 shell 命令

```json
{"type": "bash", "command": "ls -la"}
```

响应：
```json
{
  "type": "response",
  "command": "bash",
  "success": true,
  "data": {
    "output": "total 48\ndrwxr-xr-x ...",
    "exitCode": 0,
    "cancelled": false,
    "truncated": false
  }
}
```

输出被截断时包含 `fullOutputPath`：
```json
{
  "type": "response",
  "command": "bash",
  "success": true,
  "data": {
    "output": "truncated output...",
    "exitCode": 0,
    "cancelled": false,
    "truncated": true,
    "fullOutputPath": "/tmp/pi-bash-abc123.log"
  }
}
```

**Bash 结果如何到达 LLM 的机制：**

1. `bash` 命令立即执行，返回 `BashResult`
2. 内部创建 `BashExecutionMessage` 并存储到 agent 的消息状态中——**不触发事件**
3. 当下一个 `prompt` 命令发送时，所有消息（包括 `BashExecutionMessage`）被转换为 `UserMessage` 格式：

```
Ran `ls -la`
```
total 48
drwxr-xr-x ...
```

这意味着：
- Bash 输出在**下一次 prompt** 时才包含到 LLM 上下文中
- 可在 prompt 前执行多个 bash 命令，所有输出都会被包含
- `BashExecutionMessage` 本身**不会**发出事件

---

#### `abort_bash` — 中止正在运行的 bash 命令

```json
{"type": "abort_bash"}
```

---

### 4.9 Session（会话管理）

#### `get_session_stats` — 获取会话统计

```json
{"type": "get_session_stats"}
```

响应：
```json
{
  "type": "response",
  "command": "get_session_stats",
  "success": true,
  "data": {
    "sessionFile": "/path/to/session.jsonl",
    "sessionId": "abc123",
    "userMessages": 5,
    "assistantMessages": 5,
    "toolCalls": 12,
    "toolResults": 12,
    "totalMessages": 22,
    "tokens": {
      "input": 50000,
      "output": 10000,
      "cacheRead": 40000,
      "cacheWrite": 5000,
      "total": 105000
    },
    "cost": 0.45,
    "contextUsage": {
      "tokens": 60000,
      "contextWindow": 200000,
      "percent": 30
    }
  }
}
```

- `tokens`：当前会话状态的 assistant 用量汇总
- `contextUsage`：用于压缩和 footer 显示的当前上下文窗口估计值
  - 无模型或上下文窗口时省略
  - 压缩后 `tokens` 和 `percent` 为 `null`，直到新的 assistant 响应提供有效数据

---

#### `export_html` — 导出会话为 HTML

```json
{"type": "export_html"}
```

指定路径：
```json
{"type": "export_html", "outputPath": "/tmp/session.html"}
```

响应：
```json
{
  "type": "response",
  "command": "export_html",
  "success": true,
  "data": {"path": "/tmp/session.html"}
}
```

---

#### `switch_session` — 切换会话文件

```json
{"type": "switch_session", "sessionPath": "/path/to/session.jsonl"}
```

响应：
```json
{"type": "response", "command": "switch_session", "success": true, "data": {"cancelled": false}}
```

> 可被 `session_before_switch` 扩展事件处理器取消。

---

#### `fork` — 从之前的用户消息创建派生

```json
{"type": "fork", "entryId": "abc123"}
```

响应：
```json
{
  "type": "response",
  "command": "fork",
  "success": true,
  "data": {"text": "The original prompt text...", "cancelled": false}
}
```

> 可被 `session_before_fork` 扩展事件处理器取消。

---

#### `clone` — 复制当前活跃分支到新会话

```json
{"type": "clone"}
```

响应：
```json
{
  "type": "response",
  "command": "clone",
  "success": true,
  "data": {"cancelled": false}
}
```

---

#### `get_fork_messages` — 获取可用的 fork 消息列表

```json
{"type": "get_fork_messages"}
```

响应：
```json
{
  "type": "response",
  "command": "get_fork_messages",
  "success": true,
  "data": {
    "messages": [
      {"entryId": "abc123", "text": "First prompt..."},
      {"entryId": "def456", "text": "Second prompt..."}
    ]
  }
}
```

---

#### `get_last_assistant_text` — 获取最后一条 assistant 消息的文本

```json
{"type": "get_last_assistant_text"}
```

响应：
```json
{
  "type": "response",
  "command": "get_last_assistant_text",
  "success": true,
  "data": {"text": "The assistant's response..."}
}
```

无 assistant 消息时返回 `{"text": null}`。

---

#### `set_session_name` — 设置会话显示名称

```json
{"type": "set_session_name", "name": "my-feature-work"}
```

响应：
```json
{"type": "response", "command": "set_session_name", "success": true}
```

> 初始名称可通过启动参数 `--name <name>` / `-n <name>` 设置。

---

### 4.10 Commands（命令枚举）

#### `get_commands` — 获取可用命令列表

```json
{"type": "get_commands"}
```

响应：
```json
{
  "type": "response",
  "command": "get_commands",
  "success": true,
  "data": {
    "commands": [
      {"name": "session-name", "description": "Set or clear session name", "source": "extension", "path": "/home/user/.pi/agent/extensions/session.ts"},
      {"name": "fix-tests", "description": "Fix failing tests", "source": "prompt", "location": "project", "path": "/home/user/myproject/.pi/agent/prompts/fix-tests.md"},
      {"name": "skill:brave-search", "description": "Web search via Brave API", "source": "skill", "location": "user", "path": "/home/user/.pi/agent/skills/brave-search/SKILL.md"}
    ]
  }
}
```

每个命令：
| 字段 | 说明 |
|------|------|
| `name` | 命令名称（通过 `/name` 调用） |
| `description` | 人类可读描述（扩展命令可选） |
| `source` | 类型: `"extension"` / `"prompt"` / `"skill"` |
| `location` | 加载位置: `"user"` / `"project"` / `"path"`（扩展命令不含此字段） |
| `path` | 命令源文件的绝对路径（可选） |

> 内置 TUI 命令（`/settings`、`/hotkeys` 等）不会出现在此列表中，它们仅在交互模式下可用。

---

## 五、事件参考（stdout 事件流）

事件在 agent 操作期间作为 JSON 行流式输出到 stdout。事件**不含** `id` 字段。

### 事件类型一览

| 事件 | 说明 |
|------|------|
| `agent_start` | Agent 开始处理 |
| `agent_end` | Agent 完成（包含所有生成的消息） |
| `turn_start` | 新一轮开始 |
| `turn_end` | 轮次完成（包含 assistant 消息和工具结果） |
| `message_start` | 消息开始 |
| `message_update` | 流式更新（text/thinking/toolcall 差异增量） |
| `message_end` | 消息完成 |
| `tool_execution_start` | 工具开始执行 |
| `tool_execution_update` | 工具执行进度（流式输出） |
| `tool_execution_end` | 工具执行完成 |
| `queue_update` | 待处理的引导/后续队列发生变化 |
| `compaction_start` | 上下文压缩开始 |
| `compaction_end` | 上下文压缩完成 |
| `auto_retry_start` | 自动重试开始（临时错误后） |
| `auto_retry_end` | 自动重试完成 |
| `extension_error` | 扩展抛出错误 |

### 事件详情

#### `agent_start`

```json
{"type": "agent_start"}
```

#### `agent_end`

包含本次运行中生成的所有消息：

```json
{
  "type": "agent_end",
  "messages": [...]
}
```

#### `turn_start` / `turn_end`

一个 turn 包含一次 assistant 响应及由此产生的工具调用和结果：

```json
{"type": "turn_start"}
```

```json
{
  "type": "turn_end",
  "message": {...},
  "toolResults": [...]
}
```

#### `message_start` / `message_end`

`message` 字段包含 `AgentMessage`：

```json
{"type": "message_start", "message": {...}}
{"type": "message_end", "message": {...}}
```

#### `message_update`（流式）

包含部分消息和流式增量事件：

```json
{
  "type": "message_update",
  "message": {...},
  "assistantMessageEvent": {
    "type": "text_delta",
    "contentIndex": 0,
    "delta": "Hello ",
    "partial": {...}
  }
}
```

`assistantMessageEvent` 增量类型：

| 类型 | 说明 |
|------|------|
| `start` | 消息生成开始 |
| `text_start` | 文本块开始 |
| `text_delta` | 文本内容块 |
| `text_end` | 文本块结束 |
| `thinking_start` | 推理块开始 |
| `thinking_delta` | 推理内容块 |
| `thinking_end` | 推理块结束 |
| `toolcall_start` | 工具调用开始 |
| `toolcall_delta` | 工具调用参数块 |
| `toolcall_end` | 工具调用结束（含完整 `toolCall` 对象） |
| `done` | 消息完成（reason: `"stop"` / `"length"` / `"toolUse"`） |
| `error` | 发生错误（reason: `"aborted"` / `"error"`） |

#### `tool_execution_start` / `tool_execution_update` / `tool_execution_end`

```json
{
  "type": "tool_execution_start",
  "toolCallId": "call_abc123",
  "toolName": "bash",
  "args": {"command": "ls -la"}
}
```

流式进度更新（`partialResult` 为累计输出，非增量）：

```json
{
  "type": "tool_execution_update",
  "toolCallId": "call_abc123",
  "toolName": "bash",
  "args": {"command": "ls -la"},
  "partialResult": {
    "content": [{"type": "text", "text": "partial output so far..."}],
    "details": {"truncation": null, "fullOutputPath": null}
  }
}
```

完成时：

```json
{
  "type": "tool_execution_end",
  "toolCallId": "call_abc123",
  "toolName": "bash",
  "result": {
    "content": [{"type": "text", "text": "total 48\n..."}],
    "details": {...}
  },
  "isError": false
}
```

#### `queue_update`

```json
{
  "type": "queue_update",
  "steering": ["Focus on error handling"],
  "followUp": ["After that, summarize the result"]
}
```

#### `compaction_start` / `compaction_end`

```json
{"type": "compaction_start", "reason": "threshold"}
```

`reason` 取值：`"manual"`、`"threshold"`、`"overflow"`

```json
{
  "type": "compaction_end",
  "reason": "threshold",
  "result": {
    "summary": "Summary of conversation...",
    "firstKeptEntryId": "abc123",
    "tokensBefore": 150000,
    "details": {}
  },
  "aborted": false,
  "willRetry": false
}
```

- `willRetry`：若原因为 `"overflow"` 且压缩成功，值为 `true`，agent 会自动重试 prompt
- 压缩被中止时 `result` 为 `null`、`aborted` 为 `true`
- 压缩失败时 `result` 为 `null`、`aborted` 为 `false`，`errorMessage` 包含错误描述

#### `auto_retry_start` / `auto_retry_end`

```json
{
  "type": "auto_retry_start",
  "attempt": 1,
  "maxAttempts": 3,
  "delayMs": 2000,
  "errorMessage": "529 {\"type\":\"error\",\"error\":{\"type\":\"overloaded_error\",\"message\":\"Overloaded\"}}"
}
```

```json
{
  "type": "auto_retry_end",
  "success": true,
  "attempt": 2
}
```

最终失败：
```json
{
  "type": "auto_retry_end",
  "success": false,
  "attempt": 3,
  "finalError": "529 overloaded_error: Overloaded"
}
```

#### `extension_error`

```json
{
  "type": "extension_error",
  "extensionPath": "/path/to/extension.ts",
  "event": "tool_call",
  "error": "Error message..."
}
```

---

## 六、扩展 UI 子协议

扩展可以通过 `ctx.ui.select()`、`ctx.ui.confirm()` 等请求用户交互。在 RPC 模式下，它们被转换为 request/response 子协议。

### 分类

| 类别 | 方法 | 说明 |
|------|------|------|
| **对话框**（需响应） | `select`、`confirm`、`input`、`editor` | 发出 `extension_ui_request` 到 stdout，阻塞等待客户端通过 stdin 发送 `extension_ui_response`（含匹配的 `id`） |
| **即发即弃**（无需响应） | `notify`、`setStatus`、`setWidget`、`setTitle`、`set_editor_text` | 发出 `extension_ui_request` 但不需要响应 |

> 对话框若包含 `timeout` 字段，超时后 agent 端自动以默认值解析——客户端无需跟踪超时。

### RPC 模式下受限的方法

| 方法 | 行为 |
|------|------|
| `custom()` | 返回 `undefined` |
| `setWorkingMessage()` | 无操作 |
| `setWorkingIndicator()` | 无操作 |
| `setFooter()` | 无操作 |
| `setHeader()` | 无操作 |
| `setEditorComponent()` | 无操作 |
| `setToolsExpanded()` | 无操作 |
| `getEditorText()` | 返回 `""` |
| `getToolsExpanded()` | 返回 `false` |
| `pasteToEditor()` | 委托给 `setEditorText()` |
| `getAllThemes()` | 返回 `[]` |
| `getTheme()` | 返回 `undefined` |
| `setTheme()` | 返回 `{ success: false, error: "..." }` |

> `ctx.mode` 为 `"rpc"`、`ctx.hasUI` 为 `true`。需要使用 `ctx.mode === "tui"` 来守卫 TUI 特定功能（如 `custom()`）。

### 扩展 UI 请求（stdout 输出）

所有请求的通用格式：
```json
{
  "type": "extension_ui_request",
  "id": "uuid-1",
  "method": "select"
}
```

#### `select`

```json
{
  "type": "extension_ui_request",
  "id": "uuid-1",
  "method": "select",
  "title": "Allow dangerous command?",
  "options": ["Allow", "Block"],
  "timeout": 10000
}
```

期望响应：`{"type": "extension_ui_response", "id": "uuid-1", "value": "Allow"}` 或 `{"cancelled": true}`

#### `confirm`

```json
{
  "type": "extension_ui_request",
  "id": "uuid-2",
  "method": "confirm",
  "title": "Clear session?",
  "message": "All messages will be lost.",
  "timeout": 5000
}
```

期望响应：`{"type": "extension_ui_response", "id": "uuid-2", "confirmed": true}` 或 `{"cancelled": true}`

#### `input`

```json
{
  "type": "extension_ui_request",
  "id": "uuid-3",
  "method": "input",
  "title": "Enter a value",
  "placeholder": "type something..."
}
```

期望响应：`{"type": "extension_ui_response", "id": "uuid-3", "value": "entered text"}` 或 `{"cancelled": true}`

#### `editor`

```json
{
  "type": "extension_ui_request",
  "id": "uuid-4",
  "method": "editor",
  "title": "Edit some text",
  "prefill": "Line 1\nLine 2\nLine 3"
}
```

期望响应：`{"type": "extension_ui_response", "id": "uuid-4", "value": "edited text"}` 或 `{"cancelled": true}`

#### `notify`（即发即弃）

```json
{
  "type": "extension_ui_request",
  "id": "uuid-5",
  "method": "notify",
  "message": "Command blocked by user",
  "notifyType": "warning"
}
```

`notifyType`：`"info"`（默认）、`"warning"`、`"error"`

#### `setStatus`（即发即弃）

```json
{
  "type": "extension_ui_request",
  "id": "uuid-6",
  "method": "setStatus",
  "statusKey": "my-ext",
  "statusText": "Turn 3 running..."
}
```

> 通过 `statusText: undefined` 或省略该字段来清除状态。

#### `setWidget`（即发即弃）

```json
{
  "type": "extension_ui_request",
  "id": "uuid-7",
  "method": "setWidget",
  "widgetKey": "my-ext",
  "widgetLines": ["--- My Widget ---", "Line 1", "Line 2"],
  "widgetPlacement": "aboveEditor"
}
```

- `widgetPlacement`：`"aboveEditor"`（默认）或 `"belowEditor"`
- RPC 模式下仅支持字符串数组，组件工厂被忽略
- 通过 `widgetLines: undefined` 清除

#### `setTitle`（即发即弃）

```json
{
  "type": "extension_ui_request",
  "id": "uuid-8",
  "method": "setTitle",
  "title": "pi - my project"
}
```

#### `set_editor_text`（即发即弃）

```json
{
  "type": "extension_ui_request",
  "id": "uuid-9",
  "method": "set_editor_text",
  "text": "prefilled text for the user"
}
```

### 扩展 UI 响应（stdin 输入）

仅对对话框方法（`select`、`confirm`、`input`、`editor`）发送。`id` 必须匹配请求。

| 类型 | 格式 |
|------|------|
| 值响应（select/input/editor） | `{"type": "extension_ui_response", "id": "uuid-1", "value": "Allow"}` |
| 确认响应（confirm） | `{"type": "extension_ui_response", "id": "uuid-2", "confirmed": true}` |
| 取消响应（所有对话框） | `{"type": "extension_ui_response", "id": "uuid-3", "cancelled": true}` |

---

## 七、错误处理

命令失败时返回 `success: false` 的响应：

```json
{
  "type": "response",
  "command": "set_model",
  "success": false,
  "error": "Model not found: invalid/model"
}
```

解析错误：

```json
{
  "type": "response",
  "command": "parse",
  "success": false,
  "error": "Failed to parse command: Unexpected token..."
}
```

---

## 八、JSON 事件流模式（`--mode json`）

作为补充，Pi 也支持 `--mode json` 模式，输出所有会话事件到 stdout 的 JSON 行。

```bash
pi --mode json "Your prompt"
```

输出格式（首行为 session header）：

```json
{"type":"session","version":3,"id":"uuid","timestamp":"...","cwd":"/path"}
```

后续为事件行：

```json
{"type":"agent_start"}
{"type":"turn_start"}
{"type":"message_start","message":{"role":"assistant","content":[],...}}
{"type":"message_update","message":{...},"assistantMessageEvent":{"type":"text_delta","delta":"Hello",...}}
{"type":"message_end","message":{...}}
{"type":"turn_end","message":{...},"toolResults":[]}
{"type":"agent_end","messages":[...]}
```

与 RPC 模式的区别：

| | RPC 模式 | JSON 模式 |
|--|---------|----------|
| 交互性 | 双向命令-响应（stdin/stdout） | 单向事件流（stdout only） |
| 适用场景 | 进程集成、IDE 插件 | 简单集成、管道处理 |
| 扩展 UI | 支持（request/response 子协议） | 不支持 |
| 会话控制 | 完整命令集（切换模型/压缩/管理等） | 无 |

---

## 九、类型定义

### Model

```typescript
{
  id: string;           // "claude-sonnet-4-20250514"
  name: string;         // "Claude Sonnet 4"
  api: string;          // "anthropic-messages"
  provider: string;     // "anthropic"
  baseUrl: string;      // "https://api.anthropic.com"
  reasoning: boolean;   // true
  input: string[];      // ["text", "image"]
  contextWindow: number; // 200000
  maxTokens: number;    // 16384
  cost: {
    input: number;      // 3.0
    output: number;     // 15.0
    cacheRead: number;  // 0.3
    cacheWrite: number; // 3.75
  };
}
```

### UserMessage

```typescript
{
  role: "user";
  content: string | TextContent[] | ImageContent[];
  timestamp: number;
  attachments: Attachment[];
}
```

### AssistantMessage

```typescript
{
  role: "assistant";
  content: Array<{
    type: "text" | "thinking" | "toolCall";
    text?: string;
    thinking?: string;
    id?: string;
    name?: string;
    arguments?: object;
  }>;
  api: string;
  provider: string;
  model: string;
  usage: { input: number; output: number; cacheRead: number; cacheWrite: number; cost: object };
  stopReason: "stop" | "length" | "toolUse" | "error" | "aborted";
  timestamp: number;
}
```

### ToolResultMessage

```typescript
{
  role: "toolResult";
  toolCallId: string;
  toolName: string;
  content: Array<{ type: "text"; text: string }>;
  isError: boolean;
  timestamp: number;
}
```

### BashExecutionMessage

```typescript
{
  role: "bashExecution";
  command: string;
  output: string;
  exitCode: number;
  cancelled: boolean;
  truncated: boolean;
  fullOutputPath: string | null;
  timestamp: number;
}
```

### Attachment

```typescript
{
  id: string;
  type: "image";
  fileName: string;
  mimeType: string;
  size: number;
  content: string; // base64-encoded
  extractedText: string | null;
  preview: string | null;
}
```

---

## 十、客户端示例

### Python 基础客户端

```python
import subprocess
import json

proc = subprocess.Popen(
    ["pi", "--mode", "rpc", "--no-session"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True
)

def send(cmd):
    proc.stdin.write(json.dumps(cmd) + "\n")
    proc.stdin.flush()

def read_events():
    for line in proc.stdout:
        yield json.loads(line)

send({"type": "prompt", "message": "Hello!"})
for event in read_events():
    if event.get("type") == "message_update":
        delta = event.get("assistantMessageEvent", {})
        if delta.get("type") == "text_delta":
            print(delta["delta"], end="", flush=True)
    if event.get("type") == "agent_end":
        print()
        break
```

### Node.js 基础客户端

```javascript
const { spawn } = require("child_process");
const { StringDecoder } = require("string_decoder");

const agent = spawn("pi", ["--mode", "rpc", "--no-session"]);

function attachJsonlReader(stream, onLine) {
    const decoder = new StringDecoder("utf8");
    let buffer = "";
    stream.on("data", (chunk) => {
        buffer += typeof chunk === "string" ? chunk : decoder.write(chunk);
        while (true) {
            const newlineIndex = buffer.indexOf("\n");
            if (newlineIndex === -1) break;
            let line = buffer.slice(0, newlineIndex);
            buffer = buffer.slice(newlineIndex + 1);
            if (line.endsWith("\r")) line = line.slice(0, -1);
            onLine(line);
        }
    });
    stream.on("end", () => {
        buffer += decoder.end();
        if (buffer.length > 0) onLine(buffer.endsWith("\r") ? buffer.slice(0, -1) : buffer);
    });
}

attachJsonlReader(agent.stdout, (line) => {
    const event = JSON.parse(line);
    if (event.type === "message_update") {
        const { assistantMessageEvent } = event;
        if (assistantMessageEvent.type === "text_delta") {
            process.stdout.write(assistantMessageEvent.delta);
        }
    }
});

agent.stdin.write(JSON.stringify({ type: "prompt", message: "Hello" }) + "\n");

process.on("SIGINT", () => {
    agent.stdin.write(JSON.stringify({ type: "abort" }) + "\n");
});
```

---

## 十一、相关资源

| 资源 | 链接 |
|------|------|
| 官方 RPC 文档 | <https://pi.dev/docs/latest/rpc> |
| 官方 JSON 模式文档 | <https://pi.dev/docs/latest/json> |
| 源码: RPC 类型定义 | `src/modes/rpc/rpc-types.ts` |
| 源码: RPC 客户端（TypeScript） | `src/modes/rpc/rpc-client.ts` |
| 源码: API 类型（Model/Message） | `packages/ai/src/types.ts` |
| 源码: Agent 类型（AgentMessage/AgentEvent） | `packages/agent/src/types.ts` |
| 源码: 消息类型（BashExecutionMessage） | `src/core/messages.ts` |
| 示例: 扩展 UI 协议 | `examples/rpc-extension-ui.ts` |
| Node.js SDK | `@earendil-works/pi-coding-agent` |
| GitHub 仓库 | <https://github.com/earendil-works/pi> |
