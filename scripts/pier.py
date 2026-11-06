#!/usr/bin/env python3
"""pier — run named Pi subagents from agent Markdown + frontmatter.

Borrows the agent Markdown format of nicobailon/pi-subagents (portable fields
only). One named agent file -> one `pi` invocation. No nesting, no extension
runtime, no dependency on the pi-subagents extension. Does not install Pi,
manage API keys, or rewrite Pi config.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from shutil import which
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

VERSION = "0.3.0"

DEFAULT_PREVIEW_CHARS = 400
DEFAULT_BATCH_PREVIEW_CHARS = 200
DEFAULT_BATCH_CONCURRENCY = 4
MAX_BATCH_CONCURRENCY = 64
# Linux MAX_ARG_STRLEN is 128 KiB per argv element; stay well below it.
INLINE_PROMPT_MAX_BYTES = 100_000
STDERR_TAIL_CHARS = 600
TERMINAL_STATES = ("completed", "failed", "cancelled")
ACTIVE_STATES = ("queued", "starting", "running")


class PierError(Exception):
    """User-facing error; CLI prints `error: ...` and exits 2."""


# ---------------------------------------------------------------------------
# Frontmatter field catalog (keep in sync with README "字段映射表")
# ---------------------------------------------------------------------------

# Portable fields that change the Pi invocation.
MAPPED_FIELDS = (
    "name", "tools", "excludeTools", "model", "thinking", "defaultProvider",
    "systemPromptMode", "skills", "skill", "skillPath",
    "extensions", "subagentOnlyExtensions",
    "inheritProjectContext", "inheritSkills",
)
# Selection / display only; never change the Pi invocation.
META_FIELDS = ("description", "advertise", "async")
# package/aliases removed in 0.3.0 — identity is `name` only.
# Other fields (launcher, timeoutMs, acceptance*, memory, output, nested*,
# inheritGlobalContext, ...) are ignored: not mapped, no effect.

# pi-subagents runtime tool names; not native Pi tools. Dropped from tools lists.
EXTENSION_ONLY_TOOLS = frozenset(
    {"contact_supervisor", "intercom", "bg_wait", "structured_output", "watchdog_diff", "subagent"}
)

THINKING_SUFFIX_RE = re.compile(r":(off|minimal|low|medium|high|xhigh|max)\s*$", re.I)

TRUE_RE = re.compile(r"^(1|true|yes|on)$", re.I)
FALSE_RE = re.compile(r"^(0|false|no|off)$", re.I)

# ---------------------------------------------------------------------------
# TOON 4.4 encoder (deterministic; no decoder). Spec: https://toonformat.dev
# Spec §9.1 empty arrays are `key: []` / `[]` (NOT legacy `key[0]:`).
# Spec §7.1 newlines in strings are quoted `\n` (TOON has no YAML block scalars).
# ---------------------------------------------------------------------------

_TOON_NUMERIC_LIKE = re.compile(r"^[+-]?[0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?$", re.I)
_TOON_UNQUOTED_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_TOON_INDENT = 2


def _toon_escape(s: str) -> str:
    out: List[str] = []
    for ch in s:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 0x20:
            out.append(f"\\u{o:04x}")
        else:
            out.append(ch)
    return "".join(out)


def _toon_needs_quote(s: str) -> bool:
    if s == "" or s in ("true", "false", "null"):
        return True
    if s[0] in "-#" or s[0] in " \t" or s[-1] in " \t":
        return True
    if _TOON_NUMERIC_LIKE.match(s):
        return True
    for ch in s:
        if ch in ':"\\[]{},' or ord(ch) < 0x20:
            return True
    return False


def _toon_key(k: str) -> str:
    return k if _TOON_UNQUOTED_KEY.match(k) else '"' + _toon_escape(k) + '"'


def _toon_prim(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int) and not isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return "null"
        if v == int(v) and abs(v) < 1e21:
            return str(int(v))
        return format(v, ".15g")
    s = str(v)
    if _toon_needs_quote(s):
        return '"' + _toon_escape(s) + '"'
    return s


def _toon_is_prim(v: Any) -> bool:
    return not isinstance(v, (dict, list))


def _toon_column_uniform(vals: List[Any]) -> bool:
    """§9.3: uniform-primitive or nested-uniform column."""
    if all(_toon_is_prim(v) for v in vals):
        return True
    if all(isinstance(v, dict) and v for v in vals):
        keys0 = list(vals[0].keys())
        if not keys0:
            return False
        if not all(set(v.keys()) == set(keys0) for v in vals):
            return False
        for k in keys0:
            if not _toon_column_uniform([v[k] for v in vals]):
                return False
        return True
    return False


def _toon_uniform_objects(arr: List[Any]) -> bool:
    """§9.3 tabular detection for an array of objects (supports nested-uniform)."""
    if not arr or not all(isinstance(x, dict) and x for x in arr):
        return False
    keys0 = list(arr[0].keys())
    if not all(set(o.keys()) == set(keys0) for o in arr):
        return False
    for k in keys0:
        if not _toon_column_uniform([o[k] for o in arr]):
            return False
    return True


def _toon_keyed_tabular_ok(obj: Dict[str, Any]) -> bool:
    """§9.5: ≥2 entries, every value a non-empty object, columns uniform."""
    if len(obj) < 2:
        return False
    vals = list(obj.values())
    if not all(isinstance(v, dict) and v for v in vals):
        return False
    return _toon_uniform_objects(vals)


def _toon_field_entry(key: str, sample: Any) -> str:
    """One field list entry, with nested field group when sample is nested-uniform object."""
    ks = _toon_key(str(key))
    if isinstance(sample, dict) and sample:
        inner = ",".join(_toon_field_entry(str(sk), sample[sk]) for sk in sample.keys())
        return f"{ks}{{{inner}}}"
    return ks


def _toon_fields_seg(sample_obj: Dict[str, Any]) -> str:
    return "{" + ",".join(_toon_field_entry(str(k), sample_obj[k]) for k in sample_obj.keys()) + "}"


def _toon_leaf_cells(obj: Dict[str, Any], sample: Dict[str, Any]) -> List[str]:
    """Depth-first leaf primitives matching header field order from sample."""
    cells: List[str] = []
    for k in sample.keys():
        sv = sample[k]
        v = obj[k]
        if isinstance(sv, dict) and sv:
            cells.extend(_toon_leaf_cells(v, sv))
        else:
            cells.append(_toon_prim(v))
    return cells


def _toon_encode_keyed(key: Optional[str], obj: Dict[str, Any], indent: int) -> List[str]:
    """Keyed tabular form §9.5. key=None for root."""
    pad = " " * (_TOON_INDENT * indent)
    vals = list(obj.values())
    sample = vals[0]
    n = len(obj)
    head = (f"{pad}{_toon_key(key)}[{n}:]" if key else f"{pad}[{n}:]") + _toon_fields_seg(sample) + ":"
    lines = [head]
    ipad = " " * (_TOON_INDENT * (indent + 1))
    for ek, ev in obj.items():
        cells = ",".join(_toon_leaf_cells(ev, sample))
        lines.append(f"{ipad}{_toon_key(str(ek))}: {cells}")
    return lines


def _toon_encode_list(key: str, arr: List[Any], indent: int) -> List[str]:
    """Encode an array. key="" for root/keyless; indent = header line depth."""
    pad = " " * (_TOON_INDENT * indent)
    n = len(arr)
    # §9.1 empty arrays: key: [] / root []
    if n == 0:
        if key:
            return [f"{pad}{_toon_key(key)}: []"]
        return [f"{pad}[]"] if indent else ["[]"]
    head = f"{pad}{_toon_key(key)}[{n}]" if key else f"{pad}[{n}]"
    # inline primitive array
    if all(_toon_is_prim(x) for x in arr):
        return [head + ": " + ",".join(_toon_prim(x) for x in arr)]
    # tabular (§9.3) including nested-uniform columns
    if _toon_uniform_objects(arr):
        sample = arr[0]
        lines = [head + _toon_fields_seg(sample) + ":"]
        ipad = " " * (_TOON_INDENT * (indent + 1))
        for obj in arr:
            lines.append(ipad + ",".join(_toon_leaf_cells(obj, sample)))
        return lines
    # list form (§9.2 / §9.4)
    lines = [head + ":"]
    item_depth = indent + 1
    ipad = " " * (_TOON_INDENT * item_depth)
    for item in arr:
        if isinstance(item, dict):
            lines.extend(_toon_encode_list_item_object(item, item_depth))
        elif isinstance(item, list):
            # keyless header as list item (§9.2 / §9.4)
            sub = _toon_encode_list("", item, item_depth)
            # merge onto "- " prefix: header stands at item_depth
            lines.append(ipad + "- " + sub[0][len(ipad):])
            lines.extend(sub[1:])
        else:
            lines.append(ipad + "- " + _toon_prim(item))
    return lines


def _toon_encode_list_item_object(obj: Dict[str, Any], item_depth: int) -> List[str]:
    """§10: first field on hyphen line; siblings at item_depth+1; nested content at +2 from hyphen."""
    ipad = " " * (_TOON_INDENT * item_depth)
    if not obj:
        return [ipad + "-"]
    lines: List[str] = []
    first = True
    for k, v in obj.items():
        ks = _toon_key(str(k))
        if first:
            first = False
            # First field stands at depth item_depth+1 for scope; content at item_depth+2.
            if isinstance(v, dict):
                if _toon_keyed_tabular_ok(v):
                    sub = _toon_encode_keyed(str(k), v, item_depth + 1)
                    hdr_pad = " " * (_TOON_INDENT * (item_depth + 1))
                    lines.append(ipad + "- " + sub[0][len(hdr_pad):])
                    lines.extend(sub[1:])
                elif not v:
                    lines.append(ipad + f"- {ks}:")
                else:
                    lines.append(ipad + f"- {ks}:")
                    lines.extend(_toon_encode(v, item_depth + 2))
            elif isinstance(v, list):
                # Array header on hyphen; logical header depth = item_depth+1; rows at item_depth+2
                sub = _toon_encode_list(str(k), v, item_depth + 1)
                hdr_pad = " " * (_TOON_INDENT * (item_depth + 1))
                lines.append(ipad + "- " + sub[0][len(hdr_pad):])
                lines.extend(sub[1:])
            else:
                lines.append(ipad + f"- {ks}: {_toon_prim(v)}")
        else:
            # Sibling fields at depth item_depth+1
            field_depth = item_depth + 1
            fpad = " " * (_TOON_INDENT * field_depth)
            if isinstance(v, dict):
                if _toon_keyed_tabular_ok(v):
                    lines.extend(_toon_encode_keyed(str(k), v, field_depth))
                elif not v:
                    lines.append(f"{fpad}{ks}:")
                else:
                    lines.append(f"{fpad}{ks}:")
                    lines.extend(_toon_encode(v, field_depth + 1))
            elif isinstance(v, list):
                lines.extend(_toon_encode_list(str(k), v, field_depth))
            else:
                lines.append(f"{fpad}{ks}: {_toon_prim(v)}")
    return lines


def _toon_encode(value: Any, indent: int = 0) -> List[str]:
    pad = " " * (_TOON_INDENT * indent)
    if isinstance(value, dict):
        if not value:
            return []  # empty root/nested object body (§8); caller emits `key:` for fields
        # Root object that itself is keyed-tabular (§9.5)
        if indent == 0 and _toon_keyed_tabular_ok(value):
            return _toon_encode_keyed(None, value, 0)
        lines: List[str] = []
        for k, v in value.items():
            key = _toon_key(str(k))
            if isinstance(v, dict):
                if _toon_keyed_tabular_ok(v):
                    lines.extend(_toon_encode_keyed(str(k), v, indent))
                elif not v:
                    lines.append(f"{pad}{key}:")
                else:
                    lines.append(f"{pad}{key}:")
                    lines.extend(_toon_encode(v, indent + 1))
            elif isinstance(v, list):
                lines.extend(_toon_encode_list(str(k), v, indent))
            else:
                lines.append(f"{pad}{key}: {_toon_prim(v)}")
        return lines
    if isinstance(value, list):
        return _toon_encode_list("", value, indent)
    return [pad + _toon_prim(value)]


def encode_toon(value: Any) -> str:
    """Encode JSON-compatible value as compact TOON 4.4 (no trailing newline)."""
    lines = _toon_encode(value, 0)
    return "\n".join(lines)



def classify_fields(fm: Dict[str, str]) -> Dict[str, List[str]]:
    mapped = [k for k in fm if k in MAPPED_FIELDS]
    meta = [k for k in fm if k in META_FIELDS]
    ignored = sorted(k for k in fm if k not in MAPPED_FIELDS and k not in META_FIELDS)
    return {"mapped": mapped, "meta": meta, "ignored": ignored}


def truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def notice(msg: str) -> None:
    if not truthy_env("PIER_QUIET"):
        print(f"pier: {msg}", file=sys.stderr)


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
# Paths / small helpers
# ---------------------------------------------------------------------------

def skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def pier_home() -> Path:
    """Pier runtime state root (jobs/batches/sessions).

    Default: ~/.pier. Override with PIER_HOME. Independent of --cwd, so Pi
    project sessions and pier jobs never share a store.
    """
    env = os.environ.get("PIER_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".pier").resolve()


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + f".tmp{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # Treat zombies as dead (Linux /proc check; best effort elsewhere).
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text()
        if stat.rsplit(")", 1)[1].split()[0] == "Z":
            return False
    except (OSError, IndexError):
        pass
    return True


# ---------------------------------------------------------------------------
# Frontmatter parser (mirrors pi-subagents src/agents/frontmatter.ts closely
# enough for agent files: flat keys, block scalars, `- item` lists)
# ---------------------------------------------------------------------------

_LIST_KEYS = {"tools", "excludeTools", "mutationTools", "extensions", "subagentOnlyExtensions"}


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
    source: str  # skill | scan | user | project | file
    description: str = ""
    frontmatter: Dict[str, str] = field(default_factory=dict)
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
    fields: Dict[str, List[str]] = field(default_factory=dict)


_SKIP_DIRS = frozenset({"skills", "node_modules", "__pycache__", ".git", ".pier",
                        "docs", "tests", "scripts", "mock", "dist", "build"})
_SKIP_FILES = frozenset({"SKILL.md", "README.md", "CHANGELOG.md", "LICENSE.md", "CONTRIBUTING.md"})


def discover_md_files(root: Path) -> List[Path]:
    if not root.is_dir():
        return []
    files = []
    for p in sorted(root.rglob("*.md")):
        if p.name.startswith(".") or p.name.endswith(".chain.md") or p.name in _SKIP_FILES:
            continue
        if any(part in _SKIP_DIRS for part in p.relative_to(root).parts[:-1]):
            continue
        files.append(p)
    return files


def load_agent_file(path: Path, source: str) -> Optional[AgentDef]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    fm, body = parse_frontmatter(text)
    name = (fm.get("name") or path.stem).strip()
    if not name:
        return None
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
    # skills / skill (synonym); skillPath is path-based private candidates
    skills = None
    if "skills" in fm or "skill" in fm:
        skills = []
        for key in ("skills", "skill"):
            if key in fm:
                skills.extend(a for a in (parse_list(fm.get(key)) or []) if a)
    skill_paths = parse_list(fm["skillPath"]) if "skillPath" in fm else None
    extensions = parse_list(fm["extensions"]) if "extensions" in fm else None
    soe = parse_list(fm["subagentOnlyExtensions"]) if "subagentOnlyExtensions" in fm else None
    return AgentDef(
        name=name,
        path=path.resolve(),
        source=source,
        description=(fm.get("description") or "").strip(),
        frontmatter=fm,
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
        fields=classify_fields(fm),
    )


def user_agents_dir() -> Path:
    env = os.environ.get("PIER_USER_AGENTS_DIR")
    return Path(env).expanduser() if env else Path.home() / ".pier" / "agents"


def project_agents_dir(cwd: Path) -> Path:
    return cwd / ".pier" / "agents"


def agent_layers(cwd: Path, *, extra_dirs: Sequence[Path] = ()) -> List[Tuple[str, Path]]:
    """Lookup layers, priority low -> high (later wins on the same name).

    1. skill   <skill>/agents            bundled examples/templates (read-only)
    2. scan    --agent-dir / $PIER_AGENT_DIRS
    3. user    ~/.pier/agents  ($PIER_USER_AGENTS_DIR)
    4. project <cwd>/.pier/agents        default target of `pier agents new`

    Pier never reads pi-subagents dirs, ~/.pi/agent/agents, .pi/agents, or .agents.
    """
    layers: List[Tuple[str, Path]] = [("skill", skill_root() / "agents")]
    for d in extra_dirs:
        layers.append(("scan", Path(d).expanduser()))
    for part in os.environ.get("PIER_AGENT_DIRS", "").split(os.pathsep):
        if part.strip():
            layers.append(("scan", Path(part.strip()).expanduser()))
    layers.append(("user", user_agents_dir()))
    layers.append(("project", project_agents_dir(cwd)))
    return layers


def collect_agents(cwd: Path, *, extra_dirs: Sequence[Path] = ()) -> Dict[str, AgentDef]:
    """Collect agents keyed by `name` only. Later layers overwrite same name."""
    layers = agent_layers(cwd, extra_dirs=extra_dirs)
    agents: Dict[str, AgentDef] = {}
    for source, root in layers:
        for path in discover_md_files(root):
            a = load_agent_file(path, source)
            if a is None:
                continue
            agents[a.name] = a
    return agents


def load_agent_path(path: Path) -> AgentDef:
    if not path.is_file():
        raise PierError(f"agent file not found: {path}")
    a = load_agent_file(path, "file")
    if a is None:
        raise PierError(f"cannot load agent file: {path}")
    return a


def resolve_agent(name: str, agents: Dict[str, AgentDef]) -> AgentDef:
    if name in agents:
        return agents[name]
    raise PierError(f"unknown agent {name!r}. Available: "
                    + (", ".join(sorted(agents)) or "(none)"))


# ---------------------------------------------------------------------------
# Run spec -> Pi argv
# ---------------------------------------------------------------------------

@dataclass
class RunSpec:
    """Everything needed to build one Pi call. CLI / batch values override agent."""
    cwd: Path
    task: str = ""
    agent: Optional[AgentDef] = None
    instructions: Optional[str] = None  # replaces agent body when set
    handoff: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    thinking: Optional[str] = None
    tools: Optional[str] = None  # CSV for Pi; "" -> --no-tools
    exclude_tools: Optional[str] = None
    mode: str = "json"
    name: Optional[str] = None
    session_id: Optional[str] = None
    session_ref: Optional[str] = None
    session_dir: Optional[Path] = None
    no_session: bool = False
    no_agent_body: bool = False
    extra: List[str] = field(default_factory=list)
    label: Optional[str] = None  # batch item id
    notes: List[str] = field(default_factory=list)  # ignored-field notices etc.


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


def resolve_spec(spec: RunSpec) -> RunSpec:
    """Apply agent frontmatter defaults where the caller did not override."""
    a = spec.agent
    cli_thinking = spec.thinking is not None
    if a:
        if a.fields.get("ignored"):
            spec.notes.append("ignored frontmatter (not mapped, no effect): "
                              + ", ".join(a.fields["ignored"]))
        if spec.model is None and a.model:
            spec.model = a.model
        if spec.tools is None and a.tools is not None:
            kept = filter_tool_list(a.tools, "tools", spec.notes)
            spec.tools = ",".join(kept)  # "" -> --no-tools
        if spec.exclude_tools is None and a.exclude_tools:
            kept = filter_tool_list(a.exclude_tools, "excludeTools", spec.notes)
            spec.exclude_tools = ",".join(kept) or None
        if not cli_thinking and a.thinking:
            # pi-subagents: a ':level' suffix on model wins over `thinking`.
            if not (spec.model and THINKING_SUFFIX_RE.search(spec.model)):
                spec.thinking = a.thinking
        if spec.provider is None and a.default_provider and spec.model and "/" not in spec.model:
            spec.provider = a.default_provider
        if spec.name is None:
            spec.name = a.name
    if spec.provider and not spec.model:
        raise PierError("--provider requires --model (Pi constraint)")
    if spec.mode not in ("json", "print"):
        raise PierError(f"unsupported mode {spec.mode!r} (json|print)")
    if spec.no_session and (spec.session_ref or spec.session_id):
        raise PierError("--no-session cannot combine with --session/--session-id")
    if spec.session_ref and spec.session_id:
        raise PierError("--session-id cannot combine with --session")
    return spec


def role_text(spec: RunSpec) -> str:
    if spec.instructions is not None:
        return spec.instructions.strip()
    if spec.agent and not spec.no_agent_body:
        return spec.agent.body.strip()
    return ""


def build_prompt(spec: RunSpec) -> str:
    """User prompt only (role body goes to Pi --system-prompt / --append-system-prompt).

    Exact composition (sections omitted when empty, joined by one blank line):

    ## Context handoff\n\n<handoff>
    ## Task\n\n<task>
    """
    parts = []
    if spec.handoff and spec.handoff.strip():
        parts.append("## Context handoff\n\n" + spec.handoff.strip())
    if spec.task.strip():
        parts.append("## Task\n\n" + spec.task.strip())
    if not spec.task.strip():
        raise PierError("empty task (give task words, --task, --task-file, or stdin)")
    return "\n\n".join(parts) + "\n"


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
# Pi skills / extensions resource resolution (Pi 1.4+ CLI flags)
# ---------------------------------------------------------------------------

_PKG_OR_URL_RE = re.compile(r"^(builtin:|npm:|git:|https?://|file:)", re.I)


def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _skill_dirs_from_package_json(pkg_json: Path) -> List[Path]:
    data = _read_json_file(pkg_json)
    if not isinstance(data, dict):
        return []
    pi = data.get("pi")
    if not isinstance(pi, dict):
        return []
    skills = pi.get("skills")
    out: List[Path] = []
    base = pkg_json.parent
    if isinstance(skills, str):
        skills = [skills]
    if isinstance(skills, list):
        for s in skills:
            if isinstance(s, str) and s.strip():
                out.append((base / s).resolve())
    return out


def _settings_skill_paths(settings: Path) -> List[Path]:
    data = _read_json_file(settings)
    if not isinstance(data, dict):
        return []
    out: List[Path] = []
    base = settings.parent
    skills = data.get("skills")
    if isinstance(skills, str):
        skills = [skills]
    if isinstance(skills, list):
        for s in skills:
            if isinstance(s, str) and s.strip():
                p = Path(s).expanduser()
                out.append((p if p.is_absolute() else (base / p)).resolve())
    packages = data.get("packages")
    if isinstance(packages, list):
        for entry in packages:
            if isinstance(entry, str) and not _PKG_OR_URL_RE.match(entry) and not entry.startswith("@"):
                pkg = Path(entry).expanduser()
                if not pkg.is_absolute():
                    pkg = (base / pkg)
                pj = pkg / "package.json" if pkg.is_dir() else None
                if pj and pj.is_file():
                    out.extend(_skill_dirs_from_package_json(pj))
    return out


def pi_skill_search_roots(cwd: Path) -> List[Path]:
    """Ordered skill discovery roots for name resolution (project -> packages -> user).

    Pier resolves names to concrete paths and passes repeated `--skill <path>`
    (Pi 1.4+ accepts a skill file or directory).
    """
    home = Path.home()
    roots: List[Path] = [
        cwd / ".pi" / "skills",
        cwd / ".agents" / "skills",
    ]
    roots.extend(_skill_dirs_from_package_json(cwd / "package.json"))
    nm = cwd / "node_modules"
    if nm.is_dir():
        for pkg_json in sorted(nm.glob("*/package.json")) + sorted(nm.glob("@*/*/package.json")):
            roots.extend(_skill_dirs_from_package_json(pkg_json))
    roots.extend(_settings_skill_paths(cwd / ".pi" / "settings.json"))
    roots.append(home / ".pi" / "agent" / "skills")
    roots.append(home / ".agents" / "skills")
    roots.extend(_settings_skill_paths(home / ".pi" / "agent" / "settings.json"))
    seen = set()
    out: List[Path] = []
    for r in roots:
        key = str(r)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


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
        if root.name == name and (root / "SKILL.md").is_file():
            return (root / "SKILL.md").resolve()
        if root.name == name and root.is_dir():
            return root.resolve()
    raise PierError(
        f"skill {name!r} not found in Pi skill locations under {cwd} "
        f"(checked .pi/skills, .agents/skills, packages, settings, ~/.pi/agent/skills)"
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


def resource_argv(spec: RunSpec) -> List[str]:
    """Build --skill / --no-skills / --extension / --no-extensions / --no-context-files."""
    argv: List[str] = []
    a = spec.agent
    agent_dir = a.path.parent if a else spec.cwd

    want_no_skills = False
    skill_paths: List[str] = []
    if a:
        if a.inherit_skills is False:
            want_no_skills = True
        if a.skills is not None and len(a.skills) == 0:
            want_no_skills = True
        if a.skills:
            for name in a.skills:
                skill_paths.append(str(resolve_named_skill(name, spec.cwd)))
        if a.skill_paths:
            for raw in a.skill_paths:
                skill_paths.append(str(resolve_skill_path_entry(raw, agent_dir)))
    seen: set = set()
    uniq: List[str] = []
    for p in skill_paths:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    if want_no_skills:
        argv.append("--no-skills")
    for p in uniq:
        argv += ["--skill", p]

    ext_values: List[str] = []
    ext_key_present = False
    if a:
        if a.extensions is not None:
            ext_key_present = True
            for raw in a.extensions:
                ext_values.append(resolve_extension_entry(raw, agent_dir))
        if a.subagent_only_extensions:
            for raw in a.subagent_only_extensions:
                ext_values.append(resolve_extension_entry(raw, agent_dir))
    seen_e: set = set()
    uniq_e: List[str] = []
    for e in ext_values:
        if e not in seen_e:
            seen_e.add(e)
            uniq_e.append(e)
    if (ext_key_present and a is not None and a.extensions is not None
            and len(a.extensions) == 0 and not a.subagent_only_extensions):
        argv.append("--no-extensions")
    elif uniq_e:
        # allowlist: disable ambient discovery, then add explicit paths
        argv.append("--no-extensions")
        for e in uniq_e:
            argv += ["--extension", e]

    if a and a.inherit_project_context is False:
        argv.append("--no-context-files")
    return argv


def prompt_transport(prompt: str) -> str:
    return "argv" if len(prompt.encode("utf-8")) <= INLINE_PROMPT_MAX_BYTES else "file"


def build_argv(spec: RunSpec, prompt: str, *, binary: str, home: Path,
               prompt_path: Path, system_prompt_path: Optional[Path] = None) -> List[str]:
    argv = [binary]
    argv += ["--mode", "json"] if spec.mode == "json" else ["--print"]
    if spec.provider:
        argv += ["--provider", spec.provider]
    if spec.model:
        argv += ["--model", spec.model]
    if spec.thinking:
        argv += ["--thinking", spec.thinking]
    if spec.tools == "":
        argv += ["--no-tools"]
    elif spec.tools:
        argv += ["--tools", spec.tools]
    if spec.exclude_tools:
        argv += ["--exclude-tools", spec.exclude_tools]
    if spec.name:
        argv += ["--name", spec.name]

    # System prompt: role body / --instructions via Pi flags (not spliced into user prompt).
    # Resume keeps the session's original system prompt: callers leave role_text empty
    # (no agent / no_agent_body) so we do not re-pass --system-prompt.
    role = role_text(spec)
    if role:
        mode = (spec.agent.system_prompt_mode if spec.agent else "replace")
        flag = "--system-prompt" if mode != "append" else "--append-system-prompt"
        if system_prompt_path is not None and len(role.encode("utf-8")) > INLINE_PROMPT_MAX_BYTES:
            system_prompt_path.write_text(role if role.endswith("\n") else role + "\n", encoding="utf-8")
            argv += [flag, str(system_prompt_path)]
        else:
            argv += [flag, role]

    argv += resource_argv(spec)

    if spec.no_session:
        argv += ["--no-session"]
    else:
        sdir = spec.session_dir or (home / "sessions")
        argv += ["--session-dir", str(sdir)]
        if spec.session_ref:
            argv += ["--session", spec.session_ref]
        else:
            if not spec.session_id:
                spec.session_id = str(uuid.uuid4())
            argv += ["--session-id", spec.session_id]
    argv += list(spec.extra)
    if prompt_transport(prompt) == "argv":
        argv += ["--", prompt]
    else:
        # Too large for one argv element: Pi `@path` includes the file in the first prompt.
        argv += ["@" + str(prompt_path)]
    return argv
def safe_shell(argv: Sequence[str]) -> str:
    shown, redact = [], False
    for a in argv:
        if redact:
            shown.append("'<redacted>'")
            redact = False
            continue
        if a == "--api-key":
            redact = True
        elif a.startswith("--api-key="):
            a = "--api-key=<redacted>"
        shown.append(shlex.quote(a))
    return " ".join(shown)


def mapping_rows(spec: RunSpec) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    def row(fld: str, src: str, effect: str, value: Any) -> None:
        rows.append({"field": fld, "source": src, "effect": effect, "value": value})

    a = spec.agent
    if a:
        fm = a.frontmatter
        row("name", "frontmatter" if "name" in fm else "file stem", "--name (unless CLI --name)", a.name)
        for k in ("description", "advertise", "async"):
            if k in fm:
                row(k, "frontmatter", "metadata only (selection/display; no Pi flag)", fm[k])
        if "tools" in fm:
            row("tools", "frontmatter", "--tools CSV ('' -> --no-tools)", a.tools)
        if "excludeTools" in fm:
            row("excludeTools", "frontmatter", "--exclude-tools CSV", a.exclude_tools)
        if "model" in fm:
            row("model", "frontmatter", "--model verbatim (inherit/empty -> omitted)", fm["model"])
        if "thinking" in fm:
            row("thinking", "frontmatter", "--thinking (skipped if model has :level suffix)", fm["thinking"])
        if "defaultProvider" in fm:
            row("defaultProvider", "frontmatter", "--provider only if model set without 'provider/'", fm["defaultProvider"])
        if "systemPromptMode" in fm:
            row("systemPromptMode", "frontmatter",
                "--system-prompt (replace, default) or --append-system-prompt", fm["systemPromptMode"])
        if a.skills is not None:
            row("skills", "frontmatter",
                "--no-skills if empty; else repeated --skill <resolved path>", a.skills)
        if a.skill_paths:
            row("skillPath", "frontmatter", "repeated --skill <path relative to agent md>", a.skill_paths)
        if a.extensions is not None:
            row("extensions", "frontmatter",
                "--no-extensions if empty; else --no-extensions + repeated --extension", a.extensions)
        if a.subagent_only_extensions:
            row("subagentOnlyExtensions", "frontmatter",
                "merged into --extension allowlist with extensions", a.subagent_only_extensions)
        if a.inherit_project_context is False:
            row("inheritProjectContext", "frontmatter", "--no-context-files", False)
        if a.inherit_skills is False:
            row("inheritSkills", "frontmatter", "--no-skills", False)
        if "inheritGlobalContext" in fm:
            row("inheritGlobalContext", "frontmatter", "ignored (Pier does not map it)", fm["inheritGlobalContext"])
        for k in a.fields.get("ignored", []):
            row(k, "frontmatter", "ignored: not mapped, no effect", fm.get(k))
        if a.body.strip():
            eff = ("not used (--instructions overrides)" if spec.instructions is not None
                   else "not used (--no-agent-body)" if spec.no_agent_body
                   else ("Pi --append-system-prompt" if a.system_prompt_mode == "append" else "Pi --system-prompt"))
            row("(body)", "markdown body", eff, a.body.strip()[:120])
    if spec.instructions is not None:
        row("--instructions/--role", "CLI", "Pi --system-prompt (or --append-system-prompt per systemPromptMode)", spec.instructions.strip()[:120])
    if spec.handoff:
        row("--handoff-file", "CLI", "prompt section ## Context handoff", spec.handoff.strip()[:120])
    row("task", "CLI/stdin/batch", "prompt section ## Task", spec.task.strip()[:120])
    row("effective", "resolved", "final Pi flags", {
        "provider": spec.provider, "model": spec.model, "thinking": spec.thinking,
        "tools": spec.tools, "exclude_tools": spec.exclude_tools, "name": spec.name, "mode": spec.mode})
    return rows


def dry_run_payload(spec: RunSpec, home: Path) -> Dict[str, Any]:
    spec = resolve_spec(spec)
    prompt = build_prompt(spec)
    if not spec.session_id and not spec.session_ref and not spec.no_session:
        spec.session_id = "<generated-uuid-at-run>"
    binary = resolve_pi_binary(required=False)
    placeholder = home / "jobs" / "<job_id>" / "prompt.txt"
    argv = build_argv(spec, prompt, binary=binary, home=home, prompt_path=placeholder)
    transport = prompt_transport(prompt)
    out: Dict[str, Any] = {
        "dry_run": True,
        "agent": spec.agent.name if spec.agent else None,
        "agent_path": str(spec.agent.path) if spec.agent else None,
        "cwd": str(spec.cwd),
        "pier_home": str(home),
        "provider": spec.provider, "model": spec.model, "thinking": spec.thinking,
        "tools": spec.tools, "exclude_tools": spec.exclude_tools, "name": spec.name,
        "mode": spec.mode,
        "ignored_fields": spec.agent.fields.get("ignored", []) if spec.agent else [],
        "notes": spec.notes,
        "mapping": mapping_rows(spec),
        "prompt_transport": transport,
        "prompt_chars": len(prompt),
        "argv": argv,
        "argv_shell": safe_shell(argv),
    }
    if transport == "argv":
        out["prompt"] = prompt
    else:
        out["prompt_path"] = str(placeholder) + " (written at run time)"
        out["prompt_preview"] = prompt[:DEFAULT_PREVIEW_CHARS]
    if label := spec.label:
        out["id"] = label
    return out


# ---------------------------------------------------------------------------
# Jobs: prepare (queued) -> execute (running) -> finalize (terminal)
# ---------------------------------------------------------------------------

def jobs_root(home: Path) -> Path:
    return ensure_dir(home / "jobs")


def batches_root(home: Path) -> Path:
    return ensure_dir(home / "batches")


def new_id(prefix: str) -> str:
    return f"{prefix}{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


@contextmanager
def job_lock(job_dir: Path) -> Iterator[None]:
    with open(job_dir / ".lock", "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def update_status(job_dir: Path, fn: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]) -> Dict[str, Any]:
    with job_lock(job_dir):
        st = read_json(job_dir / "status.json")
        new = fn(st)
        if new is not None:
            st = new
        st["updated_at"] = iso_now()
        write_json(job_dir / "status.json", st)
        return st


def prepare_job(spec: RunSpec, home: Path, *, batch_id: Optional[str] = None,
                index: Optional[int] = None) -> Path:
    """Resolve, write prompt/cmd/status (state=queued). Raises PierError before writing."""
    spec = resolve_spec(spec)
    prompt = build_prompt(spec)
    binary = resolve_pi_binary(required=True)
    job_id = new_id("j")
    job_dir = ensure_dir(jobs_root(home) / job_id)
    prompt_path = job_dir / "prompt.txt"
    system_prompt_path = job_dir / "system_prompt.txt"
    argv = build_argv(spec, prompt, binary=binary, home=home,
                      prompt_path=prompt_path, system_prompt_path=system_prompt_path)
    prompt_path.write_text(prompt, encoding="utf-8")
    cmd = {"argv": argv, "cwd": str(spec.cwd), "mode": spec.mode}
    write_json(job_dir / "cmd.json", cmd)
    sdir = None if spec.no_session else str(spec.session_dir or (home / "sessions"))
    status = {
        "job_id": job_id,
        "batch_id": batch_id,
        "index": index,
        "id": spec.label,
        "state": "queued",
        "agent": spec.agent.name if spec.agent else None,
        "agent_path": str(spec.agent.path) if spec.agent else None,
        "custom_instructions": spec.instructions is not None,
        "cwd": str(spec.cwd),
        "mode": spec.mode,
        "provider": spec.provider, "model": spec.model, "thinking": spec.thinking,
        "tools": spec.tools, "exclude_tools": spec.exclude_tools, "name": spec.name,
        "session_id": spec.session_id,
        "session_ref": spec.session_ref,
        "session_dir": sdir,
        "session_file": None,
        "no_session": spec.no_session,
        "notes": spec.notes,
        "prompt_transport": prompt_transport(prompt),
        "prompt_chars": len(prompt),
        "pid": None, "pgid": None, "runner_pid": None,
        "exit_code": None,
        "created_at": iso_now(),
        "started_at": None, "finished_at": None, "updated_at": iso_now(),
        "stdout_path": str(job_dir / "stdout.jsonl" if spec.mode == "json" else job_dir / "stdout.txt"),
        "stderr_path": str(job_dir / "stderr.log"),
        "result_path": str(job_dir / "result.md"),
        "prompt_path": str(prompt_path),
    }
    write_json(job_dir / "status.json", status)
    return job_dir


def extract_result(job_dir: Path, mode: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Return (final_text, session_id, session_file) from Pi output."""
    if mode != "json":
        p = job_dir / "stdout.txt"
        return (p.read_text(encoding="utf-8", errors="replace").strip() if p.exists() else ""), None, None
    p = job_dir / "stdout.jsonl"
    if not p.exists():
        return "", None, None
    final, sid, sfile = "", None, None
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "session":
            sid = ev.get("id") or sid
            sfile = ev.get("session_file") or ev.get("sessionFile") or sfile
        if ev.get("type") in ("message_end", "turn_end"):
            msg = ev.get("message") or {}
            if msg.get("role") == "assistant":
                text = _message_text(msg)
                if text.strip():
                    final = text
        if ev.get("type") == "agent_end":
            for msg in reversed(ev.get("messages") or []):
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    text = _message_text(msg)
                    if text.strip():
                        final = final or text
                    break
    return final.strip(), sid, sfile


def _message_text(msg: Dict[str, Any]) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    out = []
    for part in content or []:
        if isinstance(part, dict) and part.get("type") == "text":
            out.append(part.get("text") or "")
    return "".join(out)


def find_session_file(session_dir: Optional[str], session_id: Optional[str]) -> Optional[str]:
    if not session_dir or not session_id:
        return None
    root = Path(session_dir)
    if not root.is_dir():
        return None
    hits = sorted(root.rglob(f"*{session_id}*.jsonl"))
    return str(hits[0]) if len(hits) == 1 else None


def tail_text(path: Path, n: int = STDERR_TAIL_CHARS) -> str:
    if not path.exists():
        return ""
    data = path.read_text(encoding="utf-8", errors="replace").strip()
    return data[-n:]


def execute_job(job_dir: Path) -> Dict[str, Any]:
    """Run Pi for a prepared job in the current process. Never raises for Pi failures."""
    cmd = read_json(job_dir / "cmd.json")
    st = read_json(job_dir / "status.json")
    if st.get("state") == "cancelled":
        return st
    out_path = Path(st["stdout_path"])
    err_path = Path(st["stderr_path"])

    def mark_starting(s: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if s.get("state") == "cancelled":
            return None
        s.update(state="starting", runner_pid=os.getpid(), started_at=iso_now())
        return s

    st = update_status(job_dir, mark_starting)
    if st.get("state") == "cancelled":
        return st
    try:
        with open(out_path, "wb") as so, open(err_path, "wb") as se:
            proc = subprocess.Popen(cmd["argv"], cwd=cmd["cwd"], stdin=subprocess.DEVNULL,
                                    stdout=so, stderr=se, start_new_session=True)
    except OSError as exc:
        err_path.write_text(f"pier: failed to start Pi: {exc}\n", encoding="utf-8")

        def mark_fail(s: Dict[str, Any]) -> Dict[str, Any]:
            s.update(state="failed", exit_code=127, finished_at=iso_now(), error=str(exc))
            return s
        return update_status(job_dir, mark_fail)

    cancelled_early = {"v": False}

    def mark_running(s: Dict[str, Any]) -> Dict[str, Any]:
        s.update(pid=proc.pid, pgid=proc.pid)
        if s.get("state") == "cancelled":
            cancelled_early["v"] = True
        else:
            s["state"] = "running"
        return s

    update_status(job_dir, mark_running)
    if cancelled_early["v"]:
        _kill_group(proc.pid)
    code = proc.wait()
    (job_dir / "exit_code").write_text(f"{code}\n")
    final, sid, sfile = extract_result(job_dir, cmd.get("mode", "json"))
    Path(st["result_path"]).write_text(final + ("\n" if final else ""), encoding="utf-8")

    def finalize(s: Dict[str, Any]) -> Dict[str, Any]:
        s["exit_code"] = code
        s["finished_at"] = iso_now()
        if sid:
            s["session_id"] = sid
        s["session_file"] = sfile or s.get("session_file") or find_session_file(
            s.get("session_dir"), s.get("session_id"))
        s["result_chars"] = len(final)
        if s.get("state") != "cancelled":
            s["state"] = "completed" if code == 0 else "failed"
        return s

    return update_status(job_dir, finalize)


def _kill_group(pgid: int) -> bool:
    if not pgid or pgid == os.getpgid(0):
        return False
    try:
        os.killpg(pgid, signal.SIGTERM)
        return True
    except ProcessLookupError:
        return False


def spawn_detached(args: List[str], log: Path) -> int:
    with open(log, "ab") as fh:
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve())] + args,
                                stdin=subprocess.DEVNULL, stdout=fh, stderr=fh,
                                start_new_session=True, close_fds=True)
    return proc.pid


def load_job(home: Path, job_id: str) -> Path:
    d = home / "jobs" / job_id
    if (d / "status.json").exists():
        return d
    raise PierError(f"unknown job {job_id!r} (searched {home})")


def refresh_job(job_dir: Path) -> Dict[str, Any]:
    st = read_json(job_dir / "status.json")
    if st.get("state") not in ACTIVE_STATES:
        return st
    if st.get("state") == "queued" and not st.get("runner_pid"):
        return st
    if pid_alive(st.get("runner_pid")) or pid_alive(st.get("pid")):
        return st

    def mark_lost(s: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if s.get("state") not in ACTIVE_STATES:
            return None
        code_file = job_dir / "exit_code"
        s["exit_code"] = int(code_file.read_text().strip()) if code_file.exists() else None
        s["state"] = "failed"
        s["error"] = "runner exited before finalizing the job"
        s["finished_at"] = iso_now()
        return s
    return update_status(job_dir, mark_lost)


def cancel_job(job_dir: Path) -> Dict[str, Any]:
    target = {"pgid": None}

    def mark(s: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if s.get("state") in TERMINAL_STATES:
            return None
        s["state"] = "cancelled"
        s["finished_at"] = iso_now()
        target["pgid"] = s.get("pgid")
        return s

    st = update_status(job_dir, mark)
    if target["pgid"]:
        st["signalled"] = _kill_group(int(target["pgid"]))
    return st


def compact(st: Dict[str, Any], preview_chars: int, full: bool) -> Dict[str, Any]:
    keys = ("job_id", "batch_id", "index", "id", "state", "exit_code", "agent", "model",
            "session_id", "session_file", "result_path", "stderr_path", "notes")
    out = {k: st.get(k) for k in keys if st.get(k) not in (None, [], "")}
    if out.get("state") is None:
        out["state"] = st.get("state")
    rp = Path(st.get("result_path") or "")
    text = rp.read_text(encoding="utf-8", errors="replace").strip() if rp.is_file() else ""
    out["result_chars"] = len(text)
    if full:
        out["result"] = text
    else:
        out["result_preview"] = text[:preview_chars]
        out["result_truncated"] = len(text) > preview_chars
    if st.get("state") == "failed" or (st.get("exit_code") not in (None, 0)):
        out["stderr_tail"] = tail_text(Path(st.get("stderr_path") or "/nonexistent"))
        if st.get("error"):
            out["error"] = st["error"]
    return out


def wait_jobs(job_dirs: List[Path], timeout: Optional[float]) -> List[Dict[str, Any]]:
    deadline = None if timeout is None else time.time() + timeout
    while True:
        sts = [refresh_job(d) for d in job_dirs]
        if all(s.get("state") in TERMINAL_STATES for s in sts):
            return sts
        if deadline is not None and time.time() >= deadline:
            return sts
        time.sleep(0.2)


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------

BATCH_TOP_KEYS = {"defaults", "jobs", "concurrency"}
BATCH_ITEM_KEYS = {
    "id", "agent", "agent_file", "task", "task_file", "instructions", "instructions_file", "role",
    "handoff_file", "cwd", "provider", "model", "thinking", "tools", "exclude_tools",
    "no_tools", "mode", "name", "session_dir", "no_session", "no_agent_body", "extra",
    "agent_dirs",
}
_STR_KEYS = {"id", "agent", "agent_file", "task", "task_file", "instructions", "instructions_file", "role",
             "handoff_file", "cwd", "provider", "model", "thinking", "mode", "name", "session_dir"}
_BOOL_KEYS = {"no_tools", "no_session", "no_agent_body"}


class AgentCache:
    def __init__(self) -> None:
        self._c: Dict[Tuple[str, Tuple[str, ...]], Dict[str, AgentDef]] = {}

    def get(self, cwd: Path, dirs: Sequence[Path]):
        key = (str(cwd), tuple(str(d) for d in dirs))
        if key not in self._c:
            self._c[key] = collect_agents(cwd, extra_dirs=dirs)
        return self._c[key]


def _csv(v: Any) -> str:
    return ",".join(str(x).strip() for x in v if str(x).strip()) if isinstance(v, list) else str(v)


def _read_text(path: Path, what: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PierError(f"cannot read {what} {str(path)!r}: {exc}") from None


def spec_from_item(item: Dict[str, Any], base_dir: Path, default_cwd: Path,
                   cache: AgentCache) -> RunSpec:
    unknown = sorted(set(item) - BATCH_ITEM_KEYS)
    if unknown:
        raise PierError("unknown key(s): " + ", ".join(unknown))
    for k, v in item.items():
        if v is None:
            continue
        if k in _STR_KEYS and not isinstance(v, str):
            raise PierError(f"{k} must be a string")
        if k in _BOOL_KEYS and not isinstance(v, bool):
            raise PierError(f"{k} must be true/false")
        if k in ("tools", "exclude_tools") and not isinstance(v, (str, list)):
            raise PierError(f"{k} must be a string or list")
        if k in ("extra", "agent_dirs") and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            raise PierError(f"{k} must be a list of strings")
    g = lambda k: item.get(k)  # noqa: E731

    def rel(p: str) -> Path:
        q = Path(p).expanduser()
        return q if q.is_absolute() else (base_dir / q)

    cwd = rel(g("cwd")).resolve() if g("cwd") else default_cwd
    if not cwd.is_dir():
        raise PierError(f"cwd does not exist: {cwd}")
    instr = None
    n_instr = sum(1 for k in ("instructions", "instructions_file", "role") if g(k) is not None)
    if n_instr > 1:
        raise PierError("use only one of instructions, instructions_file, role")
    if g("instructions") is not None:
        instr = g("instructions")
    elif g("role") is not None:
        instr = g("role")
    elif g("instructions_file"):
        instr = _read_text(rel(g("instructions_file")), "instructions_file")
    if g("task") is not None and g("task_file"):
        raise PierError("use only one of task, task_file")
    task = g("task") if g("task") is not None else (
        _read_text(rel(g("task_file")), "task_file") if g("task_file") else "")
    if not task.strip():
        raise PierError("missing task (task or task_file)")
    agent = None
    if g("agent") and g("agent_file"):
        raise PierError("use only one of agent, agent_file")
    if g("agent_file"):
        agent = load_agent_path(rel(g("agent_file")))
    elif g("agent"):
        agents = cache.get(cwd, [rel(d) for d in (g("agent_dirs") or [])])
        agent = resolve_agent(g("agent"), agents)
    elif instr is None:
        raise PierError("item needs agent, agent_file or instructions/role")
    if g("no_tools") and g("tools") not in (None, ""):
        raise PierError("no_tools conflicts with tools")
    tools = "" if g("no_tools") else (_csv(g("tools")) if g("tools") is not None else None)
    return RunSpec(
        cwd=cwd, task=task, agent=agent, instructions=instr,
        handoff=_read_text(rel(g("handoff_file")), "handoff_file") if g("handoff_file") else None,
        provider=g("provider"), model=g("model"), thinking=g("thinking"),
        tools=tools,
        exclude_tools=_csv(g("exclude_tools")) if g("exclude_tools") is not None else None,
        mode=g("mode") or "json", name=g("name"),
        session_dir=rel(g("session_dir")).resolve() if g("session_dir") else None,
        no_session=bool(g("no_session")), no_agent_body=bool(g("no_agent_body")),
        extra=list(g("extra") or []), label=g("id"),
    )


def load_batch_file(path_arg: str) -> Tuple[Any, Path]:
    if path_arg == "-":
        raw = sys.stdin.read()
        base = Path.cwd()
        src = "<stdin>"
    else:
        p = Path(path_arg).expanduser().resolve()
        raw = _read_text(p, "batch file")
        base = p.parent
        src = str(p)
    try:
        return json.loads(raw), base
    except json.JSONDecodeError as exc:
        raise PierError(f"batch file {src} is not valid JSON: {exc}") from None


def validate_batch(data: Any, base: Path, default_cwd: Path,
                   cli_concurrency: Optional[int]) -> Tuple[List[RunSpec], int, List[Dict[str, Any]]]:
    errors: List[Dict[str, Any]] = []
    if isinstance(data, list):
        data = {"jobs": data}
    if not isinstance(data, dict):
        return [], 0, [{"index": None, "error": "batch must be an object or an array of jobs"}]
    unknown = sorted(set(data) - BATCH_TOP_KEYS)
    if unknown:
        errors.append({"index": None, "error": "unknown top-level key(s): " + ", ".join(unknown)})
    defaults = data.get("defaults") or {}
    jobs = data.get("jobs")
    if not isinstance(defaults, dict):
        errors.append({"index": None, "error": "defaults must be an object"})
        defaults = {}
    if "id" in defaults:
        errors.append({"index": None, "error": "defaults cannot set id"})
    if not isinstance(jobs, list) or not jobs:
        errors.append({"index": None, "error": "jobs must be a non-empty array"})
        jobs = []
    conc = cli_concurrency if cli_concurrency is not None else data.get("concurrency", DEFAULT_BATCH_CONCURRENCY)
    if not isinstance(conc, int) or isinstance(conc, bool) or not 1 <= conc <= MAX_BATCH_CONCURRENCY:
        errors.append({"index": None, "error": f"concurrency must be an integer 1..{MAX_BATCH_CONCURRENCY}"})
        conc = 1
    cache = AgentCache()
    specs: List[RunSpec] = []
    seen: Dict[str, int] = {}
    for i, item in enumerate(jobs):
        ident = item.get("id") if isinstance(item, dict) else None
        if not isinstance(item, dict):
            errors.append({"index": i, "id": None, "error": "job must be an object"})
            continue
        merged = dict(defaults)
        merged.update(item)
        merged = {k: v for k, v in merged.items() if v is not None}
        try:
            if ident is not None:
                if ident in seen:
                    raise PierError(f"duplicate id (also index {seen[ident]})")
                seen[ident] = i
            spec = spec_from_item(merged, base, default_cwd, cache)
            spec = resolve_spec(spec)
            build_prompt(spec)
            specs.append(spec)
        except PierError as exc:
            errors.append({"index": i, "id": ident, "error": str(exc)})
    if not errors:
        try:
            resolve_pi_binary(required=True)
        except PierError as exc:
            errors.append({"index": None, "error": str(exc)})
    return specs, conc, errors


def create_batch(specs: List[RunSpec], home: Path, conc: int) -> Path:
    batch_id = new_id("b")
    bdir = ensure_dir(batches_root(home) / batch_id)
    job_dirs = [prepare_job(s, home, batch_id=batch_id, index=i) for i, s in enumerate(specs)]
    write_json(bdir / "batch.json", {
        "batch_id": batch_id, "concurrency": conc, "created_at": iso_now(),
        "state": "queued", "runner_pid": None, "cancel_requested": False,
        "jobs": [d.name for d in job_dirs], "ids": [s.label for s in specs],
    })
    return bdir


def run_batch_dir(bdir: Path) -> None:
    b = read_json(bdir / "batch.json")
    home = bdir.parent.parent
    dirs = [jobs_root(home) / j for j in b["jobs"]]
    me = os.getpid()
    for d in dirs:
        update_status(d, lambda s: s.update(runner_pid=me) or s if s.get("state") == "queued" else None)
    b.update(state="running", runner_pid=me, started_at=iso_now())
    write_json(bdir / "batch.json", b)

    def work(d: Path) -> None:
        if read_json(bdir / "batch.json").get("cancel_requested"):
            cancel_job(d)
            return
        execute_job(d)

    with ThreadPoolExecutor(max_workers=int(b.get("concurrency") or 1)) as ex:
        list(ex.map(work, dirs))
    b = read_json(bdir / "batch.json")
    b.update(state="finished", finished_at=iso_now())
    write_json(bdir / "batch.json", b)


def load_batch(home: Path, batch_id: str) -> Tuple[Path, List[Path]]:
    bdir = home / "batches" / batch_id
    if not (bdir / "batch.json").exists():
        raise PierError(f"unknown batch {batch_id!r} (searched {home})")
    b = read_json(bdir / "batch.json")
    return bdir, [home / "jobs" / j for j in b["jobs"]]


def batch_summary(bid: str, sts: List[Dict[str, Any]], preview: int, full: bool) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    for s in sts:
        counts[s.get("state", "?")] = counts.get(s.get("state", "?"), 0) + 1
    done = all(s.get("state") in TERMINAL_STATES for s in sts)
    ok = done and all(s.get("state") == "completed" for s in sts)
    return {"batch_id": bid, "done": done, "ok": ok, "counts": counts,
            "jobs": [compact(s, preview, full) for s in sts]}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_EMIT_JSON = False  # set True by main when --json is present on the root parser


def emit(obj: Any) -> None:
    if _EMIT_JSON:
        print(json.dumps(obj, indent=2, ensure_ascii=False))
    else:
        # Document itself has no trailing newline (TOON 4.4); print adds the line terminator.
        print(encode_toon(obj))


def add_run_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("words", nargs="*", help="AGENT then task words (task words only in custom mode)")
    p.add_argument("--agent", help="agent name (alternative to first positional)")
    p.add_argument("--no-agent", action="store_true", help="custom mode: no agent file")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--instructions", help="custom role/instructions text (replaces agent body)")
    g.add_argument("--role", help="alias of --instructions")
    g.add_argument("--instructions-file", help="read instructions from file")
    p.add_argument("--no-agent-body", action="store_true", help="do not include the agent body")
    p.add_argument("--task", help="task text")
    p.add_argument("--file", "--task-file", dest="task_file",
                   help="read task from file ('-' = stdin); keeps long tasks out of argv/chat")
    p.add_argument("--agent-file", help="use this agent .md directly (no install needed)")
    p.add_argument("--handoff-file", help="context handoff file, prepended to the prompt")
    p.add_argument("--cwd", help="Pi working directory + project agent lookup root "
                   "(default: current). Does not change where pier stores jobs/sessions")
    p.add_argument("--provider")
    p.add_argument("--model")
    p.add_argument("--thinking")
    p.add_argument("--tools", help="Pi --tools CSV, passed verbatim")
    p.add_argument("--no-tools", action="store_true")
    p.add_argument("--exclude-tools", help="Pi --exclude-tools CSV, passed verbatim")
    p.add_argument("--mode", choices=["json", "print"], default="json")
    p.add_argument("--name", help="Pi session display name (default: agent name)")
    p.add_argument("--session", dest="session_ref", help="Pi --session (path or id) to continue")
    p.add_argument("--session-id", help="explicit Pi --session-id")
    p.add_argument("--session-dir",
                   help="Pi --session-dir (default: $PIER_HOME/sessions, usually ~/.pier/sessions; "
                        "keeps pier sessions out of Pi /resume)")
    p.add_argument("--no-session", action="store_true")
    p.add_argument("--agent-dir", action="append", default=[], help="extra agent directory (repeatable)")
    p.add_argument("--dry-run", action="store_true", help="print resolved Pi argv + prompt; never runs Pi")
    p.add_argument("--full", action="store_true", help="include full result text in JSON")
    p.add_argument("--preview-chars", type=int, default=DEFAULT_PREVIEW_CHARS)
    p.add_argument("--extra", nargs=argparse.REMAINDER, default=[], help="raw Pi args (must be last)")


def spec_from_args(args: argparse.Namespace) -> RunSpec:
    cwd = Path(args.cwd).expanduser().resolve() if args.cwd else Path.cwd()
    if not cwd.is_dir():
        raise PierError(f"--cwd does not exist: {cwd}")
    words = list(args.words)
    instr = args.instructions if args.instructions is not None else args.role
    if args.instructions_file:
        instr = _read_text(Path(args.instructions_file).expanduser(), "--instructions-file")
    custom = args.no_agent or instr is not None or bool(args.agent_file)
    agent_name = args.agent
    if args.agent_file and args.agent:
        raise PierError("--agent-file conflicts with --agent")
    if args.agent_file and args.no_agent:
        raise PierError("--agent-file conflicts with --no-agent")
    if agent_name is None and not custom:
        if not words:
            raise PierError("missing AGENT (or use --no-agent / --instructions)")
        agent_name = words.pop(0)
    if args.no_agent and args.agent:
        raise PierError("--no-agent conflicts with --agent")
    if args.no_agent and instr is None:
        raise PierError("--no-agent needs --instructions, --role or --instructions-file")
    task_parts = []
    if args.task is not None:
        task_parts.append(args.task)
    if args.task_file:
        task_parts.append(sys.stdin.read() if args.task_file == "-" else
                          _read_text(Path(args.task_file).expanduser(), "--task-file"))
    if words:
        task_parts.append(" ".join(words))
    if not task_parts and not sys.stdin.isatty():
        data = sys.stdin.read()
        if data.strip():
            task_parts.append(data)
    agent = None
    if args.agent_file:
        agent = load_agent_path(Path(args.agent_file).expanduser())
    elif agent_name:
        agents = collect_agents(cwd, extra_dirs=[Path(d) for d in args.agent_dir])
        agent = resolve_agent(agent_name, agents)
    if args.no_tools and args.tools:
        raise PierError("--no-tools conflicts with --tools")
    return RunSpec(
        cwd=cwd, task="\n\n".join(t.strip() for t in task_parts if t.strip()), agent=agent,
        instructions=instr,
        handoff=_read_text(Path(args.handoff_file).expanduser(), "--handoff-file") if args.handoff_file else None,
        provider=args.provider, model=args.model, thinking=args.thinking,
        tools="" if args.no_tools else args.tools, exclude_tools=args.exclude_tools,
        mode=args.mode, name=args.name, session_id=args.session_id, session_ref=args.session_ref,
        session_dir=Path(args.session_dir).expanduser().resolve() if args.session_dir else None,
        no_session=args.no_session, no_agent_body=args.no_agent_body, extra=list(args.extra or []),
    )


def exit_for(states: List[Dict[str, Any]]) -> int:
    if all(s.get("state") == "completed" for s in states):
        return 0
    if len(states) == 1 and states[0].get("state") == "failed":
        code = states[0].get("exit_code")
        return code if isinstance(code, int) and 0 < code < 256 else 1
    if any(s.get("state") in ACTIVE_STATES for s in states):
        return 124
    return 1


def cmd_run(args: argparse.Namespace, background: bool) -> int:
    spec = spec_from_args(args)
    home = pier_home()
    if args.dry_run:
        payload = dry_run_payload(spec, home)
        for n in payload["notes"]:
            notice(n)
        emit(payload)
        return 0
    job_dir = prepare_job(spec, home)
    for n in spec.notes:
        notice(n)
    if background:
        pid = spawn_detached(["_exec", str(job_dir)], job_dir / "runner.log")
        update_status(job_dir, lambda s: s.update(runner_pid=pid) or s)
        st = read_json(job_dir / "status.json")
        emit({"job_id": st["job_id"], "state": st["state"], "agent": st.get("agent"),
              "session_id": st.get("session_id"), "result_path": st["result_path"],
              "next": f"pier await {st['job_id']}"})
        return 0
    st = execute_job(job_dir)
    emit(compact(st, args.preview_chars, args.full))
    return exit_for([st])


def job_dirs_from(args: argparse.Namespace, home: Path) -> Tuple[Optional[str], List[Path]]:
    ids = list(getattr(args, "job_ids", []) or [])
    for chunk in (getattr(args, "jobs", None) or "").split(","):
        if chunk.strip():
            ids.append(chunk.strip())
    if getattr(args, "batch", None):
        _, dirs = load_batch(home, args.batch)
        return args.batch, dirs + [load_job(home, j) for j in ids]
    if not ids:
        raise PierError("give JOB_ID(s), --jobs a,b or --batch ID")
    return None, [load_job(home, j) for j in ids]


def cmd_status(args: argparse.Namespace) -> int:
    home = pier_home()
    bid, dirs = job_dirs_from(args, home)
    sts = [refresh_job(d) for d in dirs]
    keys = ("job_id", "batch_id", "index", "id", "state", "exit_code", "agent", "model",
            "session_id", "started_at", "finished_at", "result_path")
    rows = [{k: s.get(k) for k in keys if s.get(k) is not None} for s in sts]
    emit({"batch_id": bid, "jobs": rows} if bid else (rows[0] if len(rows) == 1 else rows))
    return 0


def cmd_await(args: argparse.Namespace) -> int:
    home = pier_home()
    bid, dirs = job_dirs_from(args, home)
    sts = wait_jobs(dirs, args.timeout)
    if bid or len(sts) > 1:
        emit(batch_summary(bid or "-", sts, args.preview_chars, args.full))
    else:
        emit(compact(sts[0], args.preview_chars, args.full))
    return exit_for(sts)


def cmd_result(args: argparse.Namespace) -> int:
    home = pier_home()
    bid, dirs = job_dirs_from(args, home)
    sts = [refresh_job(d) for d in dirs]
    if args.json:
        emit([compact(s, 0, True) for s in sts] if (bid or len(sts) > 1) else compact(sts[0], 0, True))
        return 0
    multi = bid is not None or len(sts) > 1
    for s in sts:
        rp = Path(s["result_path"])
        text = rp.read_text(encoding="utf-8", errors="replace").rstrip() if rp.exists() else ""
        if multi:
            print(f"### {s.get('id') or s.get('index')} ({s['job_id']}) [{s.get('state')}]\n")
        print(text)
        if multi:
            print()
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    home = pier_home()
    if getattr(args, "batch", None):
        bdir, _ = load_batch(home, args.batch)
        b = read_json(bdir / "batch.json")
        b["cancel_requested"] = True
        write_json(bdir / "batch.json", b)
    _, dirs = job_dirs_from(args, home)
    out = []
    for d in dirs:
        st = cancel_job(d)
        out.append({"job_id": st["job_id"], "state": st["state"], "signalled": st.get("signalled", False)})
    emit(out)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    home = pier_home()
    rows = []
    for d in sorted(jobs_root(home).iterdir(), reverse=True):
        if (d / "status.json").exists():
            s = refresh_job(d)
            rows.append({k: s.get(k) for k in ("job_id", "batch_id", "id", "state", "exit_code", "agent", "created_at")})
    emit(rows[: args.limit])
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    home = pier_home()
    old = refresh_job(load_job(home, args.job_id))
    if old.get("state") in ACTIVE_STATES:
        raise PierError(f"job {args.job_id} is still {old.get('state')}")
    if old.get("no_session"):
        raise PierError(f"job {args.job_id} ran with --no-session; nothing to resume")
    sfile = old.get("session_file") or find_session_file(old.get("session_dir"), old.get("session_id"))
    ref = sfile or old.get("session_id") or old.get("session_ref")
    if not ref:
        raise PierError(f"job {args.job_id} has no recorded session id/file")
    task = " ".join(args.words).strip() if args.words else ""
    if args.task:
        task = (args.task + ("\n\n" + task if task else "")).strip()
    if not task and not sys.stdin.isatty():
        task = sys.stdin.read().strip()
    # Resume reuses Pi's session: do not re-attach agent body / --system-prompt.
    # The original system prompt stays whatever Pi stored with the session.
    spec = RunSpec(cwd=Path(old["cwd"]), task=task, provider=old.get("provider"),
                   model=old.get("model"), thinking=old.get("thinking"), tools=old.get("tools"),
                   exclude_tools=old.get("exclude_tools"), mode=old.get("mode") or "json",
                   name=old.get("name"), session_ref=ref,
                   session_dir=Path(old["session_dir"]) if old.get("session_dir") else None,
                   label=old.get("id"), no_agent_body=True)
    if args.dry_run:
        emit(dry_run_payload(spec, home))
        return 0
    job_dir = prepare_job(spec, home)
    update_status(job_dir, lambda s: s.update(resumed_from=old["job_id"]) or s)
    if args.background:
        pid = spawn_detached(["_exec", str(job_dir)], job_dir / "runner.log")
        update_status(job_dir, lambda s: s.update(runner_pid=pid) or s)
        emit({"job_id": job_dir.name, "state": "queued", "resumed_from": old["job_id"]})
        return 0
    st = execute_job(job_dir)
    emit(compact(st, args.preview_chars, args.full))
    return exit_for([st])


NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
THINKING_LEVELS = {"off", "minimal", "low", "medium", "high", "xhigh", "max"}


def validate_agent_text(text: str, name_hint: Optional[str] = None) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    norm = text.replace("\r\n", "\n").lstrip("\ufeff")
    fm, body = parse_frontmatter(text)
    if not norm.startswith("---") or norm.find("\n---", 3) == -1:
        errors.append("missing frontmatter block (--- ... ---)")
    name = (fm.get("name") or name_hint or "").strip()
    if not name:
        errors.append("missing name (frontmatter name or file name)")
    elif not NAME_RE.match(name):
        errors.append(f"invalid name {name!r} (letters, digits, . _ -; max 64)")
    if name_hint and fm.get("name") and fm["name"].strip() != name_hint:
        errors.append(f"frontmatter name {fm['name'].strip()!r} != requested name {name_hint!r}")
    if not body.strip():
        errors.append("empty body (the body is the agent's role instructions)")
    if not (fm.get("description") or "").strip():
        warnings.append("missing description (recommended for selection)")
    th = (fm.get("thinking") or "").strip().lower()
    if th and th not in THINKING_LEVELS | {"false"}:
        errors.append(f"thinking {th!r} not one of {sorted(THINKING_LEVELS)}")
    fields = classify_fields(fm)
    if fields["ignored"]:
        warnings.append("ignored frontmatter (not mapped, no effect): " + ", ".join(fields["ignored"]))
    for key, label in (("tools", "tools"), ("excludeTools", "excludeTools")):
        if key in fm:
            filter_tool_list(parse_list(fm[key]) or [], label, warnings)
    return {"ok": not errors, "name": name or None, "errors": errors, "warnings": warnings,
            "fields": fields}


def cmd_agents(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).expanduser().resolve() if args.cwd else Path.cwd()
    if args.agents_cmd == "paths":
        layers = agent_layers(cwd, extra_dirs=[Path(d) for d in args.agent_dir])
        emit({"order": "low -> high priority (later wins)",
              "layers": [{"source": s, "path": str(p), "exists": p.is_dir()} for s, p in layers],
              "new_default": str(project_agents_dir(cwd)), "new_user": str(user_agents_dir())})
        return 0
    if args.agents_cmd == "validate":
        text = sys.stdin.read() if args.file == "-" else _read_text(Path(args.file).expanduser(), "agent file")
        hint = None if args.file == "-" else Path(args.file).stem
        if args.file != "-" and parse_frontmatter(text)[0].get("name"):
            hint = None
        rep_ = validate_agent_text(text, hint)
        rep_["file"] = args.file
        emit(rep_)
        return 0 if rep_["ok"] else 1
    if args.agents_cmd == "new":
        if not NAME_RE.match(args.name):
            raise PierError(f"invalid agent name {args.name!r}")
        text = sys.stdin.read() if args.file == "-" else _read_text(Path(args.file).expanduser(), "definition file")
        rep_ = validate_agent_text(text, args.name)
        if not rep_["ok"]:
            emit(dict(rep_, written=None))
            return 1
        if args.dir:
            target_dir = Path(args.dir).expanduser()  # explicit, caller-chosen
        elif args.user:
            target_dir = user_agents_dir()
        else:
            target_dir = project_agents_dir(cwd)
        target = target_dir.resolve() / f"{args.name}.md"
        if target.exists() and not args.force:
            raise PierError(f"{target} exists (use --force to overwrite)")
        ensure_dir(target.parent)
        target.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        emit(dict(rep_, written=str(target)))
        return 0
    agents = collect_agents(cwd, extra_dirs=[Path(d) for d in args.agent_dir])
    if args.agents_cmd == "list":
        # Default listing: name + description only (identity is name).
        emit([{"name": a.name, "description": a.description}
              for a in sorted(agents.values(), key=lambda x: x.name)])
        return 0
    a = resolve_agent(args.name, agents)
    if not args.resolved:
        emit({"name": a.name, "source": a.source, "path": str(a.path),
              "frontmatter": a.frontmatter, "fields": a.fields, "body": a.body})
        return 0
    spec = RunSpec(cwd=cwd, task=args.task or "<task>", agent=a)
    emit(dry_run_payload(spec, pier_home()))
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    if args.batch_cmd in ("run", "start"):
        default_cwd = Path(args.cwd).expanduser().resolve() if args.cwd else Path.cwd()
        data, base = load_batch_file(args.file)
        specs, conc, errors = validate_batch(data, base, default_cwd, args.concurrency)
        if errors:
            emit({"ok": False, "started": 0, "errors": errors})
            return 2
        home = pier_home()
        if args.dry_run:
            emit({"dry_run": True, "concurrency": conc,
                  "jobs": [dict(dry_run_payload(s, home), index=i) for i, s in enumerate(specs)]})
            return 0
        for i, s in enumerate(specs):
            for n in s.notes:
                notice(f"job {s.label or i}: {n}")
        bdir = create_batch(specs, home, conc)
        bid = bdir.name
        if args.batch_cmd == "start":
            pid = spawn_detached(["_batch-exec", str(bdir)], bdir / "runner.log")
            b = read_json(bdir / "batch.json")
            b["runner_pid"] = pid
            write_json(bdir / "batch.json", b)
            for d in [jobs_root(home) / j for j in b["jobs"]]:
                update_status(d, lambda s: s.update(runner_pid=pid) or s if s.get("state") == "queued" else None)
            emit({"batch_id": bid, "concurrency": conc, "job_ids": b["jobs"], "ids": b["ids"],
                  "next": f"pier batch await {bid}"})
            return 0
        run_batch_dir(bdir)
        _, dirs = load_batch(home, bid)
        sts = [refresh_job(d) for d in dirs]
        emit(batch_summary(bid, sts, args.preview_chars, args.full))
        return 0 if all(s.get("state") == "completed" for s in sts) else 1
    args.batch = args.batch_id
    args.job_ids = []
    args.jobs = None
    return {"status": cmd_status, "await": cmd_await, "result": cmd_result,
            "cancel": cmd_cancel}[args.batch_cmd](args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pier", description="Run named Pi subagents from agent Markdown files.")
    p.add_argument("--version", action="version", version=f"pier {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("run", "run one Pi call and wait (default)"),
                        ("start", "start one Pi call in the background")):
        add_run_args(sub.add_parser(name, help=help_))

    def job_sel(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("job_ids", nargs="*")
        sp.add_argument("--jobs", help="comma-separated job ids")
        sp.add_argument("--batch", help="batch id")

    sp = sub.add_parser("status"); job_sel(sp)
    sp = sub.add_parser("await"); job_sel(sp)
    sp.add_argument("--timeout", type=float)
    sp.add_argument("--full", action="store_true")
    sp.add_argument("--preview-chars", type=int, default=DEFAULT_PREVIEW_CHARS)
    sp = sub.add_parser("result", help="print result text (on demand)"); job_sel(sp)
    sp.add_argument("--json", action="store_true")
    sp = sub.add_parser("cancel"); job_sel(sp)
    sp = sub.add_parser("list")
    sp.add_argument("--limit", type=int, default=20)

    sp = sub.add_parser("resume", help="continue a finished job's Pi session")
    sp.add_argument("job_id")
    sp.add_argument("words", nargs="*")
    sp.add_argument("--task")
    sp.add_argument("--background", action="store_true")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--full", action="store_true")
    sp.add_argument("--preview-chars", type=int, default=DEFAULT_PREVIEW_CHARS)

    ap = sub.add_parser("agents")
    asub = ap.add_subparsers(dest="agents_cmd", required=True)
    for n in ("list", "show", "paths", "validate", "new"):
        x = asub.add_parser(n)
        if n == "show":
            x.add_argument("name")
            x.add_argument("--resolved", action="store_true", help="show frontmatter -> Pi argv mapping")
            x.add_argument("--task", help="sample task for --resolved")
        if n == "validate":
            x.add_argument("file", help="agent .md ('-' = stdin)")
        if n == "new":
            x.add_argument("name")
            x.add_argument("--file", required=True, help="definition .md ('-' = stdin)")
            w = x.add_mutually_exclusive_group()
            w.add_argument("--user", action="store_true", help="write to ~/.pier/agents instead of <cwd>/.pier/agents")
            w.add_argument("--dir", help="explicit target directory (only one you were told to use)")
            x.add_argument("--force", action="store_true")
        x.add_argument("--cwd")
        x.add_argument("--agent-dir", action="append", default=[])

    bp = sub.add_parser("batch", help="many Pi calls from one JSON file")
    bsub = bp.add_subparsers(dest="batch_cmd", required=True)
    for n in ("run", "start"):
        x = bsub.add_parser(n)
        x.add_argument("--file", required=True, help="batch JSON ('-' = stdin)")
        x.add_argument("--concurrency", type=int)
        x.add_argument("--cwd")
        x.add_argument("--dry-run", action="store_true")
        x.add_argument("--full", action="store_true")
        x.add_argument("--preview-chars", type=int, default=DEFAULT_BATCH_PREVIEW_CHARS)
    for n in ("status", "await", "result", "cancel"):
        x = bsub.add_parser(n)
        x.add_argument("batch_id")
        if n == "await":
            x.add_argument("--timeout", type=float)
            x.add_argument("--full", action="store_true")
            x.add_argument("--preview-chars", type=int, default=DEFAULT_BATCH_PREVIEW_CHARS)
        if n == "result":
            x.add_argument("--json", action="store_true")

    for internal in ("_exec", "_batch-exec"):
        x = sub.add_parser(internal)
        x.add_argument("dir")

    # Structured main-session output defaults to TOON; --json selects JSON.
    # `result` keeps raw text by default; its --json still emits structured payloads.
    def _add_json(parser: argparse.ArgumentParser) -> None:
        if any("--json" in getattr(a, "option_strings", []) for a in parser._actions):
            return
        parser.add_argument("--json", action="store_true",
                            help="emit structured output as JSON instead of TOON")

    def _walk(parser: argparse.ArgumentParser) -> None:
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, sp_ in action.choices.items():
                    if name.startswith("_"):
                        continue
                    _add_json(sp_)
                    _walk(sp_)

    _walk(p)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    global _EMIT_JSON
    args = build_parser().parse_args(argv)
    _EMIT_JSON = bool(getattr(args, "json", False))
    try:
        if args.cmd == "run":
            return cmd_run(args, background=False)
        if args.cmd == "start":
            return cmd_run(args, background=True)
        if args.cmd == "_exec":
            execute_job(Path(args.dir))
            return 0
        if args.cmd == "_batch-exec":
            run_batch_dir(Path(args.dir))
            return 0
        return {"status": cmd_status, "await": cmd_await, "result": cmd_result,
                "cancel": cmd_cancel, "list": cmd_list, "resume": cmd_resume,
                "agents": cmd_agents, "batch": cmd_batch}[args.cmd](args)
    except PierError as exc:
        print(f"pier: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
