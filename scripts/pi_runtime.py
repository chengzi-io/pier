"""RunSpec, Pi resources, process execution, and output judgement."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from shutil import which
from typing import Any, List, Optional, Tuple

from pier_agents import AgentDef, THINKING_SUFFIX_RE, filter_tool_list
from pi_state import (FAILED_STOPS, PierError, Turn, ensure_dir,
                      find_session_file, last_turn, patch_sidecar,
                      process_start, sessions_dir, tail_text, write_private)

DEFAULT_MAX_CHARS = 24_000
PREVIEW_CHARS = 2_000
INLINE_PROMPT_MAX_BYTES = 100_000

# ---------------------------------------------------------------------------
# Pi skills / extensions resource resolution (Pi 1.4+ CLI flags)
# ---------------------------------------------------------------------------

_PKG_OR_URL_RE = re.compile(r"^(builtin:|npm:|git:|https?://|file:)", re.I)


def pi_skill_search_roots(cwd: Path) -> List[Path]:
    """Pi's standard skill dirs, project first. Package and settings-declared
    skills are not searched: reference those with `skillPath`."""
    home = Path.home()
    return [cwd / ".pi" / "skills", cwd / ".agents" / "skills",
            home / ".pi" / "agent" / "skills", home / ".agents" / "skills"]


def resolve_named_skill(name: str, cwd: Path) -> Path:
    """Resolve a skill name to a SKILL.md file or skill directory. Error if missing."""
    if not name or "/" in name or name.startswith("."):
        raise PierError(f"invalid skill name {name!r} (use skillPath for paths)")
    for root in pi_skill_search_roots(cwd):
        cand_dir = root / name
        skill_md = cand_dir / "SKILL.md"
        if skill_md.is_file():
            return skill_md.resolve()
        if cand_dir.is_dir():
            return cand_dir.resolve()
        cand_file = root / f"{name}.md"
        if cand_file.is_file():
            return cand_file.resolve()
    raise PierError(
        f"skill {name!r} not found in {', '.join(map(str, pi_skill_search_roots(cwd)))} "
        f"(use skillPath for other locations)"
    )


def resolve_skill_path_entry(raw: str, agent_dir: Path) -> Path:
    """skillPath entry: relative to agent md directory; absolute as-is."""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (agent_dir / p)
    p = p.resolve()
    if not (p.is_file() or p.is_dir()):
        raise PierError(f"skillPath not found: {raw} (resolved {p})")
    return p


def resolve_extension_entry(raw: str, agent_dir: Path) -> str:
    """Map an extensions / subagentOnlyExtensions entry to a Pi --extension value.

    Local relative paths resolve against the agent md directory. Absolute paths,
    builtin:*, npm:*, git:*, http(s):*, file:* pass through unchanged (Pi 1.4+).
    Missing relative local paths that look like files error; bare tokens pass through.
    """
    s = raw.strip()
    if not s:
        raise PierError("empty extension entry")
    if _PKG_OR_URL_RE.match(s) or s.startswith("/"):
        return s
    p = Path(s).expanduser()
    if p.is_absolute():
        return str(p)
    cand = (agent_dir / p)
    if cand.exists():
        return str(cand.resolve())
    if "/" in s or s.endswith((".ts", ".js", ".mjs", ".cjs", ".py")) or s in (".", ".."):
        raise PierError(f"extension path not found: {s} (relative to {agent_dir})")
    return s


def resource_argv(a: AgentDef, cwd: Path) -> List[str]:
    """Build --skill / --no-skills / --extension / --no-extensions / --no-context-files."""
    argv: List[str] = []
    agent_dir = a.path.parent
    if a.inherit_skills is False or a.skills == []:
        argv.append("--no-skills")
    skills = [str(resolve_named_skill(n, cwd)) for n in a.skills or []]
    skills += [str(resolve_skill_path_entry(r, agent_dir)) for r in a.skill_paths or []]
    for p in dict.fromkeys(skills):
        argv += ["--skill", p]

    exts = [resolve_extension_entry(r, agent_dir)
            for r in (a.extensions or []) + (a.subagent_only_extensions or [])]
    if a.extensions is not None or exts:
        # allowlist: disable ambient discovery, then add explicit entries
        argv.append("--no-extensions")
        for e in dict.fromkeys(exts):
            argv += ["--extension", e]

    if a.inherit_project_context is False:
        argv.append("--no-context-files")
    return argv


@dataclass
class RunSpec:
    """Everything needed to build one Pi call: the agent plus the `--model` override."""
    cwd: Path
    agent: AgentDef
    task: str
    model: Optional[str] = None
    provider: Optional[str] = None
    thinking: Optional[str] = None
    tools: Optional[List[str]] = None  # [] -> --no-tools
    exclude_tools: Optional[List[str]] = None
    notes: List[str] = field(default_factory=list)  # ignored-field notices etc.


def resolve_spec(spec: RunSpec) -> RunSpec:
    """Fill the Pi settings from the agent frontmatter; `--model` wins over `model`."""
    a = spec.agent
    if a.ignored:
        spec.notes.append("ignored frontmatter (not mapped, no effect): " + ", ".join(a.ignored))
    spec.model = spec.model or a.model
    if a.tools is not None:
        spec.tools = filter_tool_list(a.tools, "tools", spec.notes)
    if a.exclude_tools:
        spec.exclude_tools = filter_tool_list(a.exclude_tools, "excludeTools", spec.notes) or None
    # pi-subagents: a ':level' suffix on model wins over `thinking`.
    if a.thinking and not (spec.model and THINKING_SUFFIX_RE.search(spec.model)):
        spec.thinking = a.thinking
    if a.default_provider and spec.model and "/" not in spec.model:
        spec.provider = a.default_provider
    return spec


def build_flags(spec: RunSpec) -> List[str]:
    """Pi flags that define the agent. Stored per session and replayed by `send`,
    because Pi does not persist system prompt, tools, skills or extensions."""
    # Print mode: Pi exits non-zero when the final reply errors or is aborted
    # (JSON mode exits 0 then). stdout is discarded; the reply comes from the session file.
    flags = ["--print"]
    if spec.provider:
        flags += ["--provider", spec.provider]
    if spec.model:
        flags += ["--model", spec.model]
    if spec.thinking:
        flags += ["--thinking", spec.thinking]
    if spec.tools == []:
        flags += ["--no-tools"]
    elif spec.tools:
        flags += ["--tools", ",".join(spec.tools)]
    # Pi's --tools keeps every MCP tool unless an entry names one, so an
    # allowlist without mcp__ entries also turns MCP off: strict, and no MCP
    # tool specs in the prompt.
    if spec.tools is not None and not any(t.startswith("mcp__") for t in spec.tools):
        flags += ["--no-mcp"]
    if spec.exclude_tools:
        flags += ["--exclude-tools", ",".join(spec.exclude_tools)]
    flags += ["--name", spec.agent.name]
    role = spec.agent.body.strip()
    if role:
        if len(role.encode("utf-8")) > INLINE_PROMPT_MAX_BYTES:
            raise PierError(f"system prompt exceeds {INLINE_PROMPT_MAX_BYTES} bytes")
        append = spec.agent.system_prompt_mode == "append"
        flags += ["--append-system-prompt" if append else "--system-prompt", role]
    # Report contract: the caller sees only the final message, so it must stand
    # alone. The agent's own append comes first, the contract after.
    flags += ["--append-system-prompt", REPORT_CONTRACT]
    flags += resource_argv(spec.agent, spec.cwd)
    return flags


# ~60 tokens appended to every agent's system prompt (Claude Code does the same
# for its subagents: their prompt + environment rules).
REPORT_CONTRACT = (
    "You run as a delegated subagent. The caller sees only your final message. "
    "Make it self-contained: result first, evidence as path:line, no preamble. "
    "If blocked, state exactly what you need."
)


def prompt_args(prompt: str, prompt_file: Path, *, write: bool) -> List[str]:
    if len(prompt.encode("utf-8")) <= INLINE_PROMPT_MAX_BYTES:
        return ["--", prompt]
    # Too large for one argv element: Pi `@path` includes the file in the first prompt.
    if write:
        write_private(prompt_file, prompt)
    return ["@" + str(prompt_file)]


def pi_bin_name() -> str:
    return os.environ.get("PI_BIN") or "pi"


def resolve_pi_binary(required: bool = True) -> str:
    b = pi_bin_name()
    if os.sep in b or (os.altsep and os.altsep in b):
        p = Path(b).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return str(p.absolute())
        found = None
    else:
        found = which(b)
    if found:
        return found
    if required:
        raise PierError(f"Pi CLI not found as {b!r}. pier does not install Pi or manage API keys. "
                        "Install Pi (https://pi.dev) or set PI_BIN to its absolute path.")
    return b


# ---------------------------------------------------------------------------
# Running Pi
# ---------------------------------------------------------------------------

# Pi stays in pier's process group (no start_new_session) and pier terminates
# it on SIGTERM/SIGHUP/SIGINT, so a host that kills pier (e.g. a tool timeout)
# never leaves an orphaned Pi burning tokens with nobody to read its reply.
_children: List[subprocess.Popen] = []
_children_lock = threading.RLock()
_stopping = threading.Event()


SIGTERM_GRACE_SECONDS = 5


def _on_signal(signum: int, _frame: Any) -> None:
    """Terminate Pi, wait briefly for it to exit, then kill it; a second signal
    skips the grace period. Raising SystemExit unwinds through cmd_run's finally,
    so the worktree is still cleaned up."""
    already = _stopping.is_set()
    _stopping.set()
    with _children_lock:
        procs = list(_children)
    for p in procs:
        if p.poll() is None:
            if already:
                p.kill()
            else:
                p.terminate()
    if not already:
        deadline = time.monotonic() + SIGTERM_GRACE_SECONDS
        for p in procs:
            try:
                p.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                p.kill()
    raise SystemExit(128 + signum)


def run_pi(cwd: Path, argv: List[str]) -> Tuple[int, str]:
    if _stopping.is_set():
        raise PierError("pier is stopping")
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except OSError as exc:
        raise PierError(f"failed to start Pi: {exc}") from None
    with _children_lock:
        _children.append(proc)
        if _stopping.is_set():
            proc.terminate()
    try:
        _, err = proc.communicate()
    finally:
        with _children_lock:
            _children.remove(proc)
    return proc.returncode, err.decode("utf-8", errors="replace")


@dataclass
class Outcome:
    reply: str  # printed on stdout when non-empty
    error: Optional[str]  # None = success
    code: int


def turn_outcome(turn: Optional[Turn], code: int, err: str) -> Outcome:
    """Judge a turn from the session file first: a provider error or abort is a
    failure even when Pi exits 0, and earlier text in the turn is not the reply."""
    exit_code = code if 0 < code < 256 else 1
    if turn and turn.stop == "toolUse":
        return Outcome("", "turn ended at a tool call with no answer after it (Pi exited "
                       "mid-turn; an extension may have stopped it)"
                       + (f"; pi stderr: {tail_text(err)}" if tail_text(err) else ""), exit_code)
    if turn and turn.stop in FAILED_STOPS:
        return Outcome("", f"model run {turn.stop}: {turn.error or tail_text(err) or 'no details'}",
                       exit_code)
    if turn and turn.stop == "length":
        return Outcome(turn.reply, "reply cut off at the model's output token limit"
                       " (continue it: pier send <ref> \"continue\")", exit_code)
    if code != 0:
        return Outcome("", f"pi exited {code}: {tail_text(err) or '(no stderr)'}", exit_code)
    if not turn or not turn.reply:
        return Outcome("", "no reply after the last prompt" + (f"; pi stderr: {tail_text(err)}"
                                                               if tail_text(err) else ""), 1)
    return Outcome(turn.reply, None, 0)


def pi_argv(binary: str, home: Path, flags: List[str], session_args: List[str],
            prompt: List[str]) -> List[str]:
    return [binary, *flags, "--session-dir", str(sessions_dir(home)), *session_args, *prompt]


def run_turn(home: Path, binary: str, sid: str, cwd: Path, flags: List[str],
             session_args: List[str], prompt: str) -> Tuple[Outcome, Turn]:
    with tempfile.TemporaryDirectory(prefix="pier-") as tmp:
        argv = pi_argv(binary, home, flags, session_args,
                       prompt_args(prompt, Path(tmp) / "prompt.md", write=True))
        patch_sidecar(home, sid, pid=os.getpid(), pid_start=process_start(os.getpid()))
        try:
            code, err = run_pi(cwd, argv)
        finally:
            patch_sidecar(home, sid, pid=None, pid_start=None)
    sfile = find_session_file(home, sid)
    turn = last_turn(sfile) if sfile else Turn("", None, "")
    return turn_outcome(turn, code, err), turn


def summary_line(ref: str, out: Outcome, name: Optional[str]) -> str:
    """End-of-run stderr line: `pier: <ref> ok (<agent>)` (or failed)."""
    state = "ok" if out.error is None else "failed"
    line = f"pier: {ref} {state}"
    if name:
        line += f" ({name})"
    return line


def spill_path(home: Path, sid: str) -> Path:
    return home / "out" / f"{sid}.md"


def max_chars() -> int:
    """stdout budget for a reply: PIER_MAX_CHARS, default 24k; 0 = spill path only."""
    raw = (os.environ.get("PIER_MAX_CHARS") or "").strip()
    if not raw:
        return DEFAULT_MAX_CHARS
    try:
        n = int(raw)
    except ValueError:
        raise PierError(f"PIER_MAX_CHARS: {raw!r} is not an integer") from None
    if n < 0:
        raise PierError("PIER_MAX_CHARS: must be >= 0 (0 = print only the spill path)")
    return n


def report(out: Outcome, home: Path, sid: str, budget: int) -> int:
    """Print the reply within the output budget; an over-budget reply is spilled
    to $PIER_HOME/out/<sid>.md and stdout ends with its path."""
    if out.reply and len(out.reply) > budget:
        path = spill_path(home, sid)
        ensure_dir(path.parent)
        write_private(path, out.reply)
        if budget > 0:
            n = min(PREVIEW_CHARS, budget)
            preview = out.reply[:n]
            print(preview, end="")
            if not preview.endswith("\n"):
                print()
            print(f"[... {len(out.reply) - n} more chars; full reply: {path}]")
        else:
            print(f"full reply: {path}")
    elif out.reply:
        print(out.reply)
    if out.error is None:
        return 0
    print(f"pier: error: {out.error}", file=sys.stderr)
    return out.code
