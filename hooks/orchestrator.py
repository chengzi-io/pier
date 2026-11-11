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

SEGMENT_BREAKS = (";", "|", "\n")
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SUBCOMMAND_WORD = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")

ALLOWED_GIT = ("status", "log", "show", "diff", "branch")
GIT_GLOBAL_VALUE_FLAGS = ("-C", "-c", "--git-dir", "--work-tree")
GIT_GLOBAL_PREFIXES = ("--git-dir=", "--work-tree=")
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
SUBCOMMAND_TOOLS = ("git", "gh", "go", "npm", "pnpm", "yarn", "bun", "cargo",
                    "swift", "make")
SHELLS = ("bash", "sh", "zsh")
# Wrappers drop their own flags before the wrapped command is classified.
WRAPPER_VALUE_FLAGS = {
    "env": ("-u", "--unset", "-C", "--chdir"),
    "timeout": ("-s", "--signal", "-k", "--kill-after"),
    "nice": ("-n", "--adjustment"),
    "sudo": ("-u", "--user", "-g", "--group", "-p", "--prompt", "-C", "--close-from",
             "-h", "--host", "-r", "--role", "-t", "--type", "-U", "--other-user",
             "-T", "--command-timeout"),
    "xargs": ("-n", "--max-args", "-I", "--replace", "-d", "--delimiter", "-L",
              "--max-lines", "-P", "--max-procs", "-s", "--max-chars", "-a",
              "--arg-file", "-E", "-e", "--eof"),
}
FLAGLESS_WRAPPERS = ("nohup", "time", "exec")
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


def _skip_quote(text: str, start: int) -> int:
    """Return the index just past the quoted string starting at `start`."""
    quote = text[start]
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\" and quote == '"':
            index += 2
        elif char == quote:
            return index + 1
        else:
            index += 1
    return len(text)


def _matching_paren(text: str, start: int) -> int:
    """Return the index of the `)` closing the group that starts at `start`."""
    depth = 1
    index = start
    while index < len(text):
        char = text[index]
        if char in ("'", '"'):
            index = _skip_quote(text, index)
        elif char == "\\":
            index += 2
        elif char == "(":
            depth += 1
            index += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
            index += 1
        else:
            index += 1
    return -1


def _split_segments(command: str) -> List[str]:
    """Split on unquoted `&&`, `||`, `;`, `|` and newlines."""
    segments: List[str] = []
    start = 0
    index = 0
    while index < len(command):
        char = command[index]
        if char in ("'", '"'):
            index = _skip_quote(command, index)
        elif char == "\\":
            index += 2
        elif command.startswith("&&", index) or command.startswith("||", index):
            segments.append(command[start:index])
            index += 2
            start = index
        elif char in SEGMENT_BREAKS:
            segments.append(command[start:index])
            index += 1
            start = index
        else:
            index += 1
    segments.append(command[start:])
    return segments


def _substitutions(segment: str) -> List[str]:
    """Return the contents of every `$(...)` and backtick substitution."""
    found: List[str] = []
    index = 0
    while index < len(segment):
        char = segment[index]
        if char in ("'", '"'):
            index = _skip_quote(segment, index)
        elif char == "\\":
            index += 2
        elif segment.startswith("$(", index):
            end = _matching_paren(segment, index + 2)
            if end == -1:
                index += 2
            else:
                found.append(segment[index + 2:end])
                index = end + 1
        elif char == "`":
            end = segment.find("`", index + 1)
            if end == -1:
                break
            found.append(segment[index + 1:end])
            index = end + 1
        else:
            index += 1
    return found


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


def _drop_flags(words: List[str], value_flags: Tuple[str, ...]) -> List[str]:
    """Drop leading flags, plus the value of each flag that takes one."""
    while words and words[0].startswith("-"):
        takes_value = words[0] in value_flags
        words = words[1:]
        if takes_value and words:
            words = words[1:]
    return words


def _strip_wrappers(words: List[str]) -> List[str]:
    """Drop wrappers and their flags so the wrapped command is classified."""
    while words:
        name = os.path.basename(words[0])
        if name == "env":
            words = words[1:]
            while words and (words[0].startswith("-") or ASSIGN.match(words[0])):
                if words[0].startswith("-"):
                    words = _drop_flags(words, WRAPPER_VALUE_FLAGS["env"])
                else:
                    words = words[1:]
        elif name == "timeout":
            words = _drop_flags(words[1:], WRAPPER_VALUE_FLAGS["timeout"])[1:]
        elif name in ("nice", "sudo", "xargs"):
            words = _drop_flags(words[1:], WRAPPER_VALUE_FLAGS[name])
        elif name in FLAGLESS_WRAPPERS:
            words = _drop_flags(words[1:], ())
        elif name == "command":
            rest = _drop_flags(words[1:], ())
            flags = words[1:len(words) - len(rest)]
            # `command -v X` looks X up, exactly like `which X`.
            words = ["which"] + rest if "-v" in flags or "-V" in flags else rest
        else:
            break
    return words


def _prepared(segment: str) -> List[str]:
    """The words of the command a segment runs, wrappers and prefixes removed."""
    return _strip_wrappers(_strip_prefixes(_words(segment)))


def _shell_string(words: List[str]) -> Optional[str]:
    """The script of `bash|sh|zsh -c <string>` (also `-lc`, `-ec`)."""
    if not words or os.path.basename(words[0]) not in SHELLS:
        return None
    for index, word in enumerate(words[1:], 1):
        if word.startswith("-") and not word.startswith("--") and "c" in word:
            return words[index + 1] if index + 1 < len(words) else None
    return None


def _command_words(segments: List[str]):
    """Yield the words of every command line, nested ones included."""
    for segment in segments:
        words = _prepared(segment)
        yield words
        nested = _substitutions(segment)
        script = _shell_string(words)
        if script:
            nested.append(script)
        for text in nested:
            for inner in _command_words(_split_segments(text)):
                yield inner


def _git_subcommand(args: List[str]) -> str:
    """The git subcommand, skipping the global options before it."""
    while args:
        word = args[0]
        if word in GIT_GLOBAL_VALUE_FLAGS:
            args = args[2:]
        elif word == "--no-pager" or word.startswith(GIT_GLOBAL_PREFIXES):
            args = args[1:]
        else:
            return word
    return ""


def _classify(words: List[str]) -> str:
    first = words[0]
    name = os.path.basename(first)
    if first.endswith("/scripts/pier"):
        return "allow"
    if name == "git":
        return "allow" if _git_subcommand(words[1:]) in ALLOWED_GIT else "vcs"
    if name in SIMPLE_ALLOW:
        return "allow"
    if name == "sed":
        return "allow" if words[1:2] == ["-n"] else "other"
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


def _head(words: List[str]) -> str:
    """The command word, plus its subcommand, and never its arguments."""
    name = os.path.basename(words[0])
    if name == "git":
        subcommand = _git_subcommand(words[1:])
        return f"{name} {subcommand}" if subcommand else name
    if name in ("python", "python3") and words[1:2] == ["-m"] \
            and words[2:3] and SUBCOMMAND_WORD.match(words[2]):
        return f"{name} -m {words[2]}"
    if name in SUBCOMMAND_TOOLS and words[1:2] and SUBCOMMAND_WORD.match(words[1]):
        return f"{name} {words[1]}"
    return name


def classify_bash(command: str) -> Tuple[str, str]:
    """Return (category, head) for a whole command line: the first command
    worth denying wins, nested ones included; head names the offending one."""
    segments = _split_segments(command)
    for segment in segments:
        words = _prepared(segment)
        if not words:
            continue
        if words[0].endswith("/scripts/pier"):
            # A pier brief is opaque: heredoc and quoted text is not a command line.
            return "allow", _head(words)
        break

    category = "allow"
    head = ""
    for words in _command_words(segments):
        if not words:
            continue
        segment_category = _classify(words)
        if not head:
            head = _head(words)
        if category == "allow" and segment_category not in ("allow", "other"):
            category = segment_category
            head = _head(words)
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
    line = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, line)
        finally:
            os.close(fd)
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
        text = sys.stdin.buffer.read().decode("utf-8")
    except UnicodeDecodeError as exc:
        print(f"orchestrator: bad hook input: {exc}", file=sys.stderr)
        return 1
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"orchestrator: bad hook input: {exc}", file=sys.stderr)
        return 1
    if not isinstance(data, dict):
        print("orchestrator: bad hook input: expected a JSON object", file=sys.stderr)
        return 1

    tool = str(data.get("tool_name") or "")
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    agent_id = data.get("agent_id")
    subagent = isinstance(agent_id, str) and agent_id != ""
    category, head = classify(tool, tool_input, subagent=subagent)

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
