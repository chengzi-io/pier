# pier

用可复用的 agent Markdown 驱动 [Pi](https://pi.dev) coding agent：**一个命名 md → 一次 `pi` 调用**。

- 借用 [pi-subagents](https://github.com/nicobailon/pi-subagents) 的 agent md 思路，**不依赖**该扩展，也不实现它的运行时（无嵌套 subagent、无 launcher、无 acceptance）。
- 身份只用 frontmatter `name`（无 `package` / `aliases`）。仅映射可移植字段；其余字段忽略且不生效（stderr 一行提示）。
- 与宿主无关：任何能跑 shell 的主会话直接调用；默认同步。
- 不安装 Pi、不管理 API Key、不修改 Pi / pi-subagents 配置。
- 结构化输出默认 [TOON 4.4](https://github.com/toon-format/spec)；各命令加 `--json` 输出 JSON。`pier result` 默认仍是原文。

版本：**0.3.0**

## 安装与调用

依赖：Python 3.9+，Pi CLI（`pi` 在 PATH 上，或 `PI_BIN=/abs/path/pi`）。建议 Pi **1.4.0+**（`--system-prompt` / `--append-system-prompt` / `--skill` / `--extension` / `--no-skills` / `--no-extensions` / `--no-context-files`）。

```bash
npx skills add <path-or-repo> -y --skill pier
python3 <skill>/scripts/pier.py agents list
<skill>/scripts/pier agents list
```

`--cwd` **只**决定 Pi 的工作目录，以及项目级 agent 查找根（`<cwd>/.pier/agents`）。Pier 自己的 jobs / batches / sessions 默认写在用户私有 `~/.pier/`（`PIER_HOME`），**不会**写进项目目录，因此也不会污染 Pi `/resume`。只有 `pier resume` 会续上一次 Pier 会话。

## 常用命令

| 命令 | 作用 |
|---|---|
| `pier run AGENT [task…]` | 同步运行；默认 TOON；退出码 = Pi 退出码 |
| `pier run AGENT --file task.md` | 任务从文件读（`--file -` = stdin；`--task-file` 同义） |
| `pier run --agent-file role.md --file task.md` | 直接用某个 md |
| `pier run --instructions "…" task…` | 全自定义（无 agent）；配 `--model/--provider/--thinking/--tools…` |
| `pier run … --dry-run` | 只打印解析后的 Pi argv 与 prompt，不调用 pi、不建 job |
| `pier start …` | 后台运行，返回 `job_id` |
| `pier status/await/result/cancel JOB…` | 也接受 `--jobs a,b` 或 `--batch ID` |
| `pier resume JOB "追问"` | 用该 job 的 Pi 会话继续（不重发 system prompt） |
| `pier batch run\|start --file jobs.json` | 批量；`batch await\|status\|result\|cancel ID` |
| `pier agents list\|show NAME [--resolved]\|paths` | 列表默认仅 name+description |
| `pier agents validate FILE` / `pier agents new NAME --file F [--user\|--dir D]` | 校验 / 落盘 |

run/start 主要参数：`--cwd`、`--model`、`--provider`、`--thinking`、`--tools CSV`、`--no-tools`、`--exclude-tools CSV`、`--name`、`--mode json|print`、`--session`、`--session-id`、`--session-dir`、`--no-session`、`--handoff-file`、`--no-agent-body`、`--agent-dir`、`--json`、`--preview-chars`、`--full`、`--extra …`（必须放最后）。CLI 总是覆盖 frontmatter。

## Agent 查找顺序（低 → 高，后者同名覆盖）

1. `<skill>/agents/*.md`：内置示例/模板（不要修改）
2. `--agent-dir`、`PIER_AGENT_DIRS`
3. 用户级 `~/.pier/agents`（`PIER_USER_AGENTS_DIR` 可改）
4. 项目级 `<cwd>/.pier/agents`：`pier agents new` 默认目标

Pier **从不**读取 pi-subagents 目录、`~/.pi/agent/agents`、`<cwd>/.agents`、`<cwd>/.pi/agents`。`pier agents paths` 打印实际路径。编写规范见 [references/agent-authoring.md](references/agent-authoring.md)。

## Frontmatter → Pi argv 映射表

| 字段 | Pi 参数 / 去向 |
|---|---|
| `name` | `--name`（CLI `--name` 覆盖）；缺省用文件名；**查找键** |
| `description`、`advertise`、`async` | 元数据，不映射 |
| `model` | `--model` 原样；`inherit`/空 → 不传 |
| `thinking` | `--thinking`；model 带 `:level` 后缀则不传；CLI `--thinking` 总是传 |
| `defaultProvider` | `--provider`，仅当有 model 且不含 `/` |
| `tools` | `--tools a,b`；空 → `--no-tools`；扩展专用工具名提示后丢弃 |
| `excludeTools` | `--exclude-tools a,b` |
| `systemPromptMode` | 默认 `replace` → `--system-prompt`；`append` → `--append-system-prompt` |
| `skills` / `skill` | 按名解析后重复 `--skill <path>`；空或 `inheritSkills: false` → `--no-skills`；未找到报错 |
| `skillPath` | 重复 `--skill`（相对 agent md 目录） |
| `extensions` | 空 → `--no-extensions`；非空 → `--no-extensions` + 重复 `--extension`（相对 agent md；`builtin:`/`npm:`/`git:`/`http(s):`/`file:`/绝对路径透传） |
| `subagentOnlyExtensions` | 并入 `--extension` allowlist |
| `inheritProjectContext: false` | `--no-context-files` |
| `inheritGlobalContext` 及其他（launcher、timeoutMs、acceptance*、memory、output、嵌套…） | 不映射、无效果 |
| 正文 body | Pi `--system-prompt` / `--append-system-prompt`（`--instructions` 覆盖；`--no-agent-body` 省略） |

pier 固定附加：`--mode json`（或 `--print`）、`--session-dir <$PIER_HOME/sessions>` + `--session-id <uuid>`（或 `--no-session` / `--session REF`），最后 `-- <prompt>`。Pi 子进程 stdin 为 `/dev/null`。

### prompt 精确组合（用户消息）

角色正文走 system-prompt flag，**不**再拼进用户 prompt。非空段用一个空行连接，末尾一个换行：

```text
## Context handoff        ← --handoff-file（可选）

<handoff>

## Task

<task>
```

例：正文 `ROLE LINE\n\nSecond para.`、任务 `DO THE THING` →
- argv 含 `--system-prompt` + `ROLE LINE\n\nSecond para.`
- 用户 prompt 精确为 `"## Task\n\nDO THE THING\n"`

`pier resume` 不重新附加 agent / system-prompt（沿用 Pi 会话原 system prompt）。

prompt 超过 100 000 字节时改用 `@<job>/prompt.txt`；超大 system prompt 写入 `<job>/system_prompt.txt` 后把路径传给对应 flag。

## 批量

```json
{
  "defaults": {"agent": "reviewer", "model": "anthropic/claude-sonnet-4-5"},
  "concurrency": 3,
  "jobs": [
    {"id": "auth", "task": "review src/auth.py"},
    {"id": "db", "task_file": "tasks/db.md", "thinking": "high"},
    {"id": "free", "agent": null, "instructions": "You are a release-notes writer.", "task": "…"}
  ]
}
```

- 也可直接给数组；`--file -` 从 stdin 读；`--concurrency` 覆盖文件（1–64，默认 4）。
- 条目键：`id agent agent_file instructions role instructions_file task task_file handoff_file cwd provider model thinking tools exclude_tools no_tools mode name session_dir no_session no_agent_body extra agent_dirs`。
- 原子校验：任一条无效 → 都不启动，退出 2。

## 存储

**运行态**在 `$PIER_HOME`（默认 `~/.pier/`）：`jobs/`、`batches/`、`sessions/`。
**项目目录**只放 `<cwd>/.pier/agents/`。

## 环境变量

| 变量 | 含义 |
|---|---|
| `PI_BIN` | Pi 可执行文件（默认 `pi`） |
| `PIER_HOME` | 运行态根（默认 `~/.pier`） |
| `PIER_AGENT_DIRS` | 额外 agent 目录（`:` 分隔） |
| `PIER_USER_AGENTS_DIR` | 用户级 agent 目录（默认 `~/.pier/agents`） |
| `PIER_QUIET` | 设为 1 关闭 stderr 提示 |

## 测试

```bash
python3 -m unittest discover -s tests -v
PIER_TEST_REAL_PI=/path/to/pi python3 -m unittest discover -s tests
shellcheck scripts/pier scripts/pier.sh
```

## 版本说明（0.3.0）

相对 0.2.x：name-only 身份；移除 `--include-pi-subagents` 与一切 pi-subagents 目录读取；正文改走 Pi system-prompt flags；映射 skills/extensions/context flags（按 Pi 1.4.0 `--help` 实测）；默认 TOON 输出。

本机 Mac 已核实 Pi 1.4.0 支持上述 flags（`--skill` 接 skill 文件或目录）。0.3.0 的端到端模型联调未在本轮重跑；以 mock 单测与 dry-run 映射为准。字段研究见 [docs/RESEARCH.md](docs/RESEARCH.md)。

## 许可

MIT
