"""Agent Markdown: frontmatter parsing, discovery, and validation."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

from pi_state import PierError, pier_home, skill_root

# ---------------------------------------------------------------------------
# Frontmatter field catalog (keep in sync with README "Frontmatter mapping")
# ---------------------------------------------------------------------------

# Portable fields that change the Pi invocation.
MAPPED_FIELDS = (
    "name", "tools", "excludeTools", "model", "thinking", "defaultProvider",
    "systemPromptMode", "skills", "skillPath",
    "extensions", "subagentOnlyExtensions",
    "inheritProjectContext", "inheritSkills",
)
# Selection / display only; never changes the Pi invocation.
META_FIELDS = ("description",)
# Identity is `name` only (no package/aliases). Other fields (advertise, async,
# launcher, timeoutMs, acceptance*, memory, output, nested*, inheritGlobalContext,
# ...) are ignored; `validate` and `--dry-run` report them.

THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
THINKING_SUFFIX_RE = re.compile(rf":({'|'.join(THINKING_LEVELS)})\s*$", re.I)
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

TRUE_RE = re.compile(r"^(1|true|yes|on)$", re.I)
FALSE_RE = re.compile(r"^(0|false|no|off)$", re.I)


def ignored_fields(fm: Dict[str, str]) -> List[str]:
    return sorted(k for k in fm if k not in MAPPED_FIELDS and k not in META_FIELDS)


def parse_bool(val: Optional[str]) -> Optional[bool]:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    if TRUE_RE.match(s):
        return True
    if FALSE_RE.match(s):
        return False
    return None


# ---------------------------------------------------------------------------
# Frontmatter parser (mirrors pi-subagents src/agents/frontmatter.ts closely
# enough for agent files: flat keys, block scalars, `- item` lists)
# ---------------------------------------------------------------------------

_LIST_KEYS = {"tools", "excludeTools", "extensions", "subagentOnlyExtensions"}


def parse_frontmatter(content: str) -> Tuple[Dict[str, str], str]:
    fm: Dict[str, str] = {}
    text = content.replace("\r\n", "\n")
    if text.startswith("\ufeff"):
        text = text[1:]
    if not text.startswith("---"):
        return fm, text
    end = text.find("\n---", 3)
    if end == -1:
        return fm, text
    block = text[4:end]
    body = text[end + 4:].lstrip("\n").rstrip()

    key: Optional[str] = None
    buf: Optional[List[str]] = None
    indent0 = 0
    folded = literal = False

    def flush() -> None:
        nonlocal key, buf, folded, literal
        if key is not None and buf is not None:
            fm[key] = _flush_block(buf, folded, literal)
        key, buf, folded, literal = None, None, False, False

    for line in block.split("\n"):
        m_ind = re.search(r"\S|$", line)
        indent = m_ind.start() if m_ind else 0
        trimmed = line.strip()
        if trimmed.startswith("#") and not folded and not literal:
            continue
        if key is not None and buf is not None and (indent > indent0 or trimmed == ""):
            buf.append(line)
            continue
        flush()
        m = re.match(r"^([\w-]+):\s*(.*)$", line)
        if not m:
            continue
        k, raw = m.group(1), m.group(2).strip()
        q = raw[:1]
        quoted_list = k in _LIST_KEYS and q in {'"', "'"} and (f"{q}," in raw or f", {q}" in raw)
        is_quoted = not quoted_list and q in {'"', "'"} and raw.endswith(q) and len(raw) >= 2
        value = raw[1:-1] if is_quoted else raw
        is_folded = not is_quoted and raw in {">", ">-"}
        is_literal = not is_quoted and raw in {"|", "|-"}
        if value == "" or is_folded or is_literal:
            key, buf, indent0, folded, literal = k, [], indent, is_folded, is_literal
        else:
            fm[k] = value
    flush()
    return fm, body


def _flush_block(lines: List[str], folded: bool, literal: bool) -> str:
    lines = list(lines)
    if not folded and not literal:
        while lines and lines[0] == "":
            lines.pop(0)
        last = max((i for i, l in enumerate(lines) if l.strip()), default=-1)
        lines = lines[: last + 1] if last >= 0 else []
    raw = "\n".join(lines)
    m = re.search(r"^[ \t]+(?=\S)", raw, re.M)
    if m:
        raw = re.sub(rf"^{re.escape(m.group(0))}", "", raw, flags=re.M)
    if folded:
        return _fold(raw)
    return raw.rstrip("\n") if literal else raw


def _fold(block: str) -> str:
    out, has, prev_more, blanks = "", False, False, 0
    for line in block.split("\n"):
        cur = line.rstrip()
        if not cur.strip():
            if has:
                blanks += 1
            continue
        more = len(cur) > len(cur.lstrip())
        if has:
            if blanks:
                out += "\n" * (blanks + (1 if prev_more or more else 0))
            else:
                out += "\n" if prev_more or more else " "
        out += cur
        has, prev_more, blanks = True, more, 0
    return out.strip()


def parse_list(raw: Optional[str]) -> Optional[List[str]]:
    if raw is None:
        return None
    out: List[str] = []
    for line in raw.split("\n"):
        v = line.strip()
        m = re.match(r"^-\s+(.+)$", v)
        item = m.group(1) if m else v
        if m and not re.match(r"^['\"]", item):
            item = re.sub(r"(?:^|\s)#.*$", "", item)
        for part in item.split(","):
            part = part.strip()
            if len(part) >= 2 and part[0] == part[-1] and part[0] in "'\"":
                part = part[1:-1]
            if part:
                out.append(part)
    return out

# ---------------------------------------------------------------------------
# Agent discovery
# ---------------------------------------------------------------------------


@dataclass
class AgentDef:
    name: str
    path: Path
    description: str = ""
    body: str = ""
    tools: Optional[List[str]] = None  # None = key absent; [] = empty -> --no-tools
    exclude_tools: Optional[List[str]] = None
    model: Optional[str] = None
    thinking: Optional[str] = None
    default_provider: Optional[str] = None
    system_prompt_mode: str = "replace"  # replace | append
    skills: Optional[List[str]] = None  # None=omitted; []=empty -> --no-skills
    skill_paths: Optional[List[str]] = None  # relative to agent md dir
    extensions: Optional[List[str]] = None  # None=omitted; []=empty -> --no-extensions
    subagent_only_extensions: Optional[List[str]] = None
    inherit_project_context: Optional[bool] = None
    inherit_skills: Optional[bool] = None
    ignored: List[str] = field(default_factory=list)  # frontmatter keys with no effect


def discover_md_files(root: Path) -> List[Path]:
    """Agent files directly in an agents dir (subdirs may hold skills or notes);
    dotfiles, `*.chain.md` and README.md skipped."""
    if not root.is_dir():
        return []
    return [p for p in sorted(root.glob("*.md"))
            if not p.name.startswith(".") and not p.name.endswith(".chain.md")
            and p.name != "README.md"]


def load_agent_file(path: Path) -> AgentDef:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PierError(f"cannot read agent file {str(path)!r}: {exc}") from None
    fm, body = parse_frontmatter(text)
    name = (fm.get("name") or "").strip() or path.stem
    tools = parse_list(fm["tools"]) if "tools" in fm else None
    model = (fm.get("model") or "").strip()
    if model.lower() == "inherit":
        model = ""
    thinking = (fm.get("thinking") or "").strip() or None
    if thinking and thinking.lower() == "false":
        thinking = "off"
    spm = (fm.get("systemPromptMode") or "replace").strip().lower() or "replace"
    if spm not in ("replace", "append"):
        spm = "replace"
    # skills are names resolved by Pi's skill search; skillPath is path-based
    skills = None
    if "skills" in fm:
        skills = [a for a in (parse_list(fm["skills"]) or []) if a]
    skill_paths = parse_list(fm["skillPath"]) if "skillPath" in fm else None
    extensions = parse_list(fm["extensions"]) if "extensions" in fm else None
    soe = parse_list(fm["subagentOnlyExtensions"]) if "subagentOnlyExtensions" in fm else None
    return AgentDef(
        name=name,
        path=path.resolve(),
        description=(fm.get("description") or "").strip(),
        body=body,
        tools=tools,
        exclude_tools=parse_list(fm.get("excludeTools")),
        model=model or None,
        thinking=thinking,
        default_provider=(fm.get("defaultProvider") or "").strip() or None,
        system_prompt_mode=spm,
        skills=skills,
        skill_paths=skill_paths,
        extensions=extensions,
        subagent_only_extensions=soe,
        inherit_project_context=parse_bool(fm.get("inheritProjectContext")),
        inherit_skills=parse_bool(fm.get("inheritSkills")),
        ignored=ignored_fields(fm),
    )


def user_agents_dir() -> Path:
    return pier_home() / "agents"


def project_agents_dir(cwd: Path) -> Path:
    return cwd / ".pier" / "agents"


def agent_layers(cwd: Path) -> List[Tuple[str, Path]]:
    """Lookup layers, priority low -> high (later wins on the same name).

    1. skill   <skill>/agents            bundled examples/templates (read-only)
    2. user    $PIER_HOME/agents (default ~/.pier/agents)
    3. project <cwd>/.pier/agents        project agents

    Pier never reads pi-subagents dirs, ~/.pi/agent/agents, .pi/agents, or .agents.
    """
    return [
        ("skill", skill_root() / "agents"),
        ("user", user_agents_dir()),
        ("project", project_agents_dir(cwd)),
    ]


def collect_agents(cwd: Path) -> Dict[str, AgentDef]:
    """Collect agents keyed by `name` only. Later layers overwrite same name."""
    agents: Dict[str, AgentDef] = {}
    for _, root in agent_layers(cwd):
        for path in discover_md_files(root):
            try:
                a = load_agent_file(path)
            except PierError as exc:
                print(f"pier: note: skipping agent: {exc}", file=sys.stderr)
                continue
            agents[a.name] = a
    return agents


def resolve_agent(name: str, agents: Dict[str, AgentDef]) -> AgentDef:
    if name in agents:
        return agents[name]
    raise PierError(f"unknown agent {name!r}. Available: "
                    + (", ".join(sorted(agents)) or "(none)"))


# pi-subagents runtime tool names; not Pi tools. Dropped from tools lists.
# MCP tools are real Pi tool names (`mcp__<server>__<tool>`, or a glob such as
# `mcp__radius__*`), so they pass through untouched.
EXTENSION_ONLY_TOOLS = frozenset(
    {"contact_supervisor", "intercom", "bg_wait", "structured_output", "watchdog_diff", "subagent"}
)


def filter_tool_list(items: List[str], field_name: str, notes: List[str]) -> List[str]:
    kept, dropped = [], []
    for t in items:
        if t in EXTENSION_ONLY_TOOLS or t.startswith("mcp:") or "/" in t \
                or t.endswith((".ts", ".js", ".mjs", ".cjs")):
            dropped.append(t)
        else:
            kept.append(t)
    if dropped:
        notes.append(f"{field_name}: ignored entries not native Pi tool names (no effect): "
                     + ", ".join(dropped))
    return kept


def validate_agent_text(text: str, fallback_name: Optional[str] = None) -> Dict[str, Any]:
    """Check an agent file; `fallback_name` (the file stem) stands in for a missing `name`."""
    errors: List[str] = []
    warnings: List[str] = []
    norm = text.replace("\r\n", "\n").lstrip("﻿")
    fm, body = parse_frontmatter(text)
    if not norm.startswith("---") or norm.find("\n---", 3) == -1:
        errors.append("missing frontmatter block (--- ... ---)")
    name = (fm.get("name") or fallback_name or "").strip()
    if not name:
        errors.append("missing name (frontmatter name or file name)")
    elif not NAME_RE.match(name):
        errors.append(f"invalid name {name!r} (letters, digits, . _ -; max 64)")
    if not body.strip():
        errors.append("empty body (the body is the agent's role instructions)")
    if not (fm.get("description") or "").strip():
        warnings.append("missing description (recommended for selection)")
    th = (fm.get("thinking") or "").strip().lower()
    if th and th not in THINKING_LEVELS + ("false",):
        errors.append(f"thinking {th!r} not one of {'|'.join(THINKING_LEVELS)}")
    mode = (fm.get("systemPromptMode") or "").strip()
    if mode and mode.lower() not in ("replace", "append"):
        errors.append(f"systemPromptMode {mode!r} must be 'replace' or 'append'")
    for key in ("inheritProjectContext", "inheritSkills"):
        raw = (fm.get(key) or "").strip()
        if raw and parse_bool(raw) is None:
            errors.append(f"{key} {raw!r} is not a boolean (true/false)")
    if (fm.get("defaultProvider") or "").strip() and not (fm.get("model") or "").strip():
        warnings.append("defaultProvider has no effect without model")
    ignored = ignored_fields(fm)
    if ignored:
        warnings.append("ignored frontmatter (not mapped, no effect): " + ", ".join(ignored))
    for key in ("tools", "excludeTools"):
        if key in fm:
            filter_tool_list(parse_list(fm[key]) or [], key, warnings)
    return {"ok": not errors, "name": name or None, "errors": errors, "warnings": warnings}
