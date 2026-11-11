#!/usr/bin/env python3
"""Claude Code plugin hooks for pier orchestrator mode.

Inactive unless PIER_ORCHESTRATOR is `shadow` or `enforce`: any other value
(including unset) exits 0 with no output and no log, so normal sessions are
untouched. Hook input is JSON on stdin; output is the Claude Code hook JSON.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MODES = ("shadow", "enforce")
PROMPT_NAME = "orchestrator-prompt.md"
EVENTS_NAME = "orchestrator-events.jsonl"

SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|\n]")
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

ALLOWED_GIT = ("status", "log", "show", "diff", "branch")
SIMPLE_ALLOW = ("ls", "pwd", "cat", "head", "tail", "wc", "echo", "which")
SEARCH_TOOLS = ("grep", "rg", "find", "fd", "ag", "ack")
PLAIN_CHECKS = ("pytest", "npx", "make", "cargo", "tsc", "eslint", "ruff",
                "mypy", "golangci-lint", "xcodebuild")
CHECK_SUBCOMMANDS = {
    "go": ("test", "build", "vet", "run"),
    "npm": ("test", "run", "build", "install", "exec"),
    "pnpm": ("test", "run", "build", "install", "exec"),
    "yarn": ("test", "run", "build", "install", "exec"),
    "bun": ("test", "run", "build", "install", "exec"),
    "swift": ("build", "test"),
}
WEB_TOOLS = ("curl", "wget")

# The worst segment wins: these beat `allow`/`other` and produce a denial.
DELEGATIONS = {
    "search": 'pier run librarian "<question>"',
    "checks": 'pier run chore "<commands>"',
    "vcs": 'pier run chore "<commands>"',
    "web": 'pier run researcher "<question>"',
}


def mode() -> Optional[str]:
    value = os.environ.get("PIER_ORCHESTRATOR", "")
    return value if value in MODES else None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _words(segment: str) -> List[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        # Unbalanced quotes still classify, and the fallback can only widen `other`.
        return segment.split()


def _strip_prefixes(words: List[str]) -> List[str]:
    """Drop leading `VAR=value` assignments and `cd <dir>`."""
    while words:
        if ASSIGN.match(words[0]):
            words = words[1:]
        elif words[0] == "cd":
            words = words[2:]
        else:
            break
    return words


def _classify(words: List[str]) -> str:
    first = words[0]
    name = os.path.basename(first)
    if first.endswith("/scripts/pier"):
        return "allow"
    if name == "git":
        return "allow" if words[1:2] and words[1] in ALLOWED_GIT else "vcs"
    if name in SIMPLE_ALLOW:
        return "allow"
    if name == "sed":
        return "allow" if words[1:2] == ["-n"] else "other"
    if name == "command":
        return "allow" if words[1:2] == ["-v"] else "other"
    if name in SEARCH_TOOLS:
        return "search"
    if name in PLAIN_CHECKS:
        return "checks"
    if name in ("python", "python3") and words[1:2] == ["-m"]:
        return "checks"
    if name in CHECK_SUBCOMMANDS and words[1:2] and words[1] in CHECK_SUBCOMMANDS[name]:
        return "checks"
    if name == "gh":
        return "vcs"
    if name in WEB_TOOLS:
        return "web"
    return "other"


def classify_bash(command: str) -> Tuple[str, str]:
    """Return (category, head) for a whole command line: the first segment
    worth denying wins; head is that segment's first two words, or the first
    non-empty segment's when nothing is denied."""
    category = "allow"
    head = ""
    for segment in SEGMENT_SPLIT.split(command):
        words = _strip_prefixes(_words(segment))
        if not words:
            continue
        segment_category = _classify(words)
        if not head:
            head = " ".join(words[:2])
        if category == "allow" and segment_category not in ("allow", "other"):
            category = segment_category
            head = " ".join(words[:2])
    return category, head


def _first_words(text: str, count: int = 2) -> str:
    return " ".join(text.split()[:count])


def classify(tool: str, tool_input: Dict[str, Any], subagent: bool) -> Tuple[str, str]:
    if subagent:
        # Claude subagents are the fallback and must keep working.
        return "subagent", ""
    if tool == "Bash":
        return classify_bash(str(tool_input.get("command") or ""))
    if tool in ("Grep", "Glob"):
        return "search", _first_words(str(tool_input.get("pattern") or ""))
    if tool == "Read":
        return "read", str(tool_input.get("file_path") or "")
    return "other", _first_words(tool)


# ---------------------------------------------------------------------------
# Output: log line and hook JSON
# ---------------------------------------------------------------------------


def events_path() -> Path:
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    base = Path(data) if data else Path.home() / ".claude" / "pier-orchestrator"
    return base / EVENTS_NAME


def log_event(event: Dict[str, Any]) -> None:
    path = events_path()
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as exc:
        # A verdict must still reach Claude Code, so report and carry on.
        print(f"orchestrator: cannot write {str(path)!r}: {exc}", file=sys.stderr)


def deny_reason(category: str) -> str:
    return f"orchestrator: delegate {category}: {DELEGATIONS[category]}"


def emit(obj: Dict[str, Any]) -> None:
    print(json.dumps(obj))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def session_start() -> int:
    prompt = Path(__file__).resolve().parent / PROMPT_NAME
    try:
        text = prompt.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"orchestrator: cannot read prompt {str(prompt)!r}: {exc}", file=sys.stderr)
        return 1
    emit({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                 "additionalContext": text}})
    return 0


def pre_tool_use(current_mode: str) -> int:
    try:
        data = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        print(f"orchestrator: invalid hook input: {exc}", file=sys.stderr)
        return 1
    if not isinstance(data, dict):
        print("orchestrator: invalid hook input: expected a JSON object", file=sys.stderr)
        return 1

    tool = str(data.get("tool_name") or "")
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    category, head = classify(tool, tool_input, subagent="agent_id" in data)

    deny = category in DELEGATIONS
    if not deny:
        verdict = "allow"
    elif current_mode == "enforce":
        verdict = "deny"
    else:
        verdict = "would-deny"

    log_event({
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_id": str(data.get("session_id") or ""),
        "mode": current_mode,
        "tool": tool,
        "category": category,
        "verdict": verdict,
        "head": head,
    })

    if verdict == "deny":
        emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                     "permissionDecision": "deny",
                                     "permissionDecisionReason": deny_reason(category)}})
    return 0


def main(argv: List[str]) -> int:
    current_mode = mode()
    if current_mode is None:
        return 0
    if not argv:
        print("orchestrator: missing command (session-start|pre-tool-use)", file=sys.stderr)
        return 1
    if argv[0] == "session-start":
        return session_start()
    if argv[0] == "pre-tool-use":
        return pre_tool_use(current_mode)
    print(f"orchestrator: unknown command {argv[0]!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
