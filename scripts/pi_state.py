"""Filesystem, sessions, locks and git worktrees for pier."""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

STDERR_TAIL_CHARS = 600


class PierError(Exception):
    """User-facing error; the CLI prints `pier: error: ...` and exits 2."""

# ---------------------------------------------------------------------------
# Paths / small helpers
# ---------------------------------------------------------------------------


def skill_root() -> Path:
    """The skill root (this repository): scripts/pi_state.py -> repo root."""
    return Path(__file__).resolve().parent.parent


def pier_home() -> Path:
    """Pier state root: sessions/ and user agents/. Default ~/.pier, override with
    PIER_HOME. Independent of --cwd, so nothing runtime lands in projects."""
    env = os.environ.get("PIER_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".pier").resolve()


def ensure_dir(p: Path) -> Path:
    """mkdir -p with 0700 on every directory pier creates: state, tasks and
    replies belong to the caller's user only."""
    missing: List[Path] = []
    cur = p
    while not cur.exists():
        missing.append(cur)
        cur = cur.parent
    for d in reversed(missing):
        d.mkdir(mode=0o700, exist_ok=True)
    os.chmod(p, 0o700)
    return p


def write_private(path: Path, text: str) -> None:
    """Write a file only the user can read: tasks, replies, session state."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)  # O_CREAT's mode does not apply to an existing file
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + f".tmp{os.getpid()}.{threading.get_ident()}")
    write_private(tmp, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PierError(f"{path} is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise PierError(f"{path} does not hold a JSON object")
    return data

# ---------------------------------------------------------------------------
# Sessions: $PIER_HOME/sessions holds Pi's session files plus one
# <sid>.pier.json (cwd + flags + agent) per session.
#
# The handle is the first 8 hex chars of the session uuid: short enough to
# type, unique in practice (collision -> pier names the alternatives).
# ---------------------------------------------------------------------------


def sessions_dir(home: Path) -> Path:
    return home / "sessions"


def sidecar_path(home: Path, sid: str) -> Path:
    return sessions_dir(home) / f"{sid}.pier.json"


def sid_to_ref(sid: str) -> str:
    return sid[:8]


def load_sidecar(home: Path, sid: str) -> Dict[str, Any]:
    p = sidecar_path(home, sid)
    if not p.is_file():
        raise PierError(f"unknown session {sid!r} (no {p})")
    return read_json(p)


def iter_sidecars(home: Path) -> List[Tuple[str, Dict[str, Any], float]]:
    """All sessions' sidecars, newest mtime first: (sid, data, mtime)."""
    out = []
    for p in sessions_dir(home).glob("*.pier.json"):
        try:
            out.append((p.name[:-len(".pier.json")], read_json(p), p.stat().st_mtime))
        except (PierError, OSError) as exc:
            # Not ours to repair mid-listing, but the caller should know why a
            # session disappeared from `ps` or from ref resolution.
            print(f"pier: note: skipping session file {p.name}: {exc}", file=sys.stderr)
    return sorted(out, key=lambda t: t[2], reverse=True)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def process_start(pid: int) -> Optional[str]:
    """`ps` start time for a pid: tells our process apart from one that later
    reused the same pid. None when ps cannot answer."""
    try:
        r = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True,
                           text=True, timeout=5, env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None


def pid_owned_by(pid: int, started: Optional[str]) -> bool:
    """True when `pid` is alive and is still the process recorded as `started`.
    When ps cannot tell, assume it is ours: a false 'running' is recoverable, a
    second concurrent turn on one session is not."""
    if not pid_alive(pid):
        return False
    if not started:
        return True
    current = process_start(pid)
    return current is None or current == started


def running_pid(data: Dict[str, Any]) -> Optional[int]:
    """The pid in a sidecar if that process is still the session's run."""
    pid = data.get("pid")
    if not isinstance(pid, int):
        return None
    return pid if pid_owned_by(pid, data.get("pid_start")) else None


def ensure_not_running(ref: str, data: Dict[str, Any]) -> None:
    pid = running_pid(data)
    if pid is not None:
        raise PierError(f"session {ref} is running (pid {pid}); wait for it to finish")


def lock_holder(lock: Path) -> Optional[int]:
    """The live pid recorded in a lock file, or None when it is stale."""
    try:
        lines = lock.read_text(encoding="utf-8").splitlines()
        pid = int(lines[0].strip())
    except (OSError, ValueError, IndexError):
        return None
    started = lines[1].strip() if len(lines) > 1 else None
    return pid if pid_owned_by(pid, started) else None


@contextlib.contextmanager
def session_lock(home: Path, sid: str) -> Iterator[None]:
    """Hold `<sid>.lock` for the duration: one `send` per session at a time. A
    lock left by a killed pier is reclaimed once its pid is gone (or reused)."""
    lock = sessions_dir(home) / f"{sid}.lock"
    for _ in range(2):
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            holder = lock_holder(lock)
            if holder is not None:
                raise PierError(f"session {sid_to_ref(sid)} is already in use (pid {holder})") from None
            lock.unlink(missing_ok=True)
            continue
        os.write(fd, f"{os.getpid()}\n{process_start(os.getpid()) or ''}\n".encode())
        os.close(fd)
        try:
            yield
        finally:
            lock.unlink(missing_ok=True)
        return
    raise PierError(f"cannot lock session {sid_to_ref(sid)} ({lock})")


def resolve_ref(home: Path, ref: str) -> str:
    """A ref (8-char uuid prefix, or the full uuid) -> sid."""
    if len(ref) == 36 and ref.count("-") == 4:
        load_sidecar(home, ref)  # raises the unknown-session error if absent
        return ref
    sessions = iter_sidecars(home)  # newest mtime first
    hits = sorted(sid for sid, _, _ in sessions if sid.startswith(ref)) if ref else []
    if len(hits) > 1:
        alts = ", ".join(sid_to_ref(s) for s in hits)
        raise PierError(f"ambiguous session {ref!r} (matches {alts}); use more chars")
    if hits:
        return hits[0]
    recent = ", ".join(f"{sid_to_ref(sid)} ({data.get('agent') or '?'})"
                       for sid, data, _ in sessions[:5])
    raise PierError(f"unknown session {ref!r}" + (f"; recent sessions: {recent}" if recent else ""))


def find_session_file(home: Path, sid: str) -> Optional[Path]:
    """Pi 1.4.0 with --session-dir writes <session-dir>/<time>_<sid>.jsonl
    (observed); a per-cwd `--<cwd>--/` subdir is accepted too."""
    root = sessions_dir(home)
    hits = sorted(root.glob(f"*_{sid}.jsonl")) or sorted(root.glob(f"*/*_{sid}.jsonl"))
    return hits[0] if hits else None


# Stop reasons of a turn's last assistant message that mean it did not finish:
# a provider error, an abort, or a tool call with no answer after it (Pi exited
# mid-turn, e.g. an extension ended the run).
FAILED_STOPS = ("error", "aborted", "toolUse")


@dataclass
class Turn:
    reply: str  # last non-empty assistant text since the last user message
    stop: Optional[str]  # stopReason of the last assistant message
    error: str  # its errorMessage
    usage: Optional[Dict[str, Any]] = None  # summed over the turn's assistant messages


# Pi writes one non-cumulative usage block per assistant message (one per model
# call), so a turn's cost is the sum, not the last block.
USAGE_COUNTERS = ("input", "output", "cacheRead", "cacheWrite", "reasoning", "totalTokens")


def add_usage(acc: Dict[str, Any], u: Dict[str, Any]) -> None:
    for key in USAGE_COUNTERS:
        v = u.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            acc[key] = acc.get(key, 0) + v
    cost = u.get("cost")
    if isinstance(cost, dict):
        total = acc.setdefault("cost", {})
        for key, v in cost.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                total[key] = total.get(key, 0) + v


def last_turn(session_file: Path) -> Turn:
    turn = Turn("", None, "")
    for line in session_file.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue  # a line cut short by a killed Pi
        if not isinstance(ev, dict) or ev.get("type") != "message":
            continue
        msg = ev.get("message") or {}
        if msg.get("role") == "user":
            turn = Turn("", None, "")
        elif msg.get("role") == "assistant":
            turn.stop = msg.get("stopReason")
            turn.error = msg.get("errorMessage") or ""
            u = msg.get("usage")
            if isinstance(u, dict):
                if turn.usage is None:
                    turn.usage = {}
                add_usage(turn.usage, u)
            text = _message_text(msg).strip()
            if text:
                turn.reply = text
    return turn


def _message_text(msg: Dict[str, Any]) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    out = []
    for part in content or []:
        if isinstance(part, dict) and part.get("type") == "text":
            out.append(part.get("text") or "")
    return "".join(out)


def patch_sidecar(home: Path, sid: str, **fields: Any) -> None:
    """Update fields of a session's sidecar in place (drops keys set to None)."""
    p = sidecar_path(home, sid)
    if not p.is_file():
        return  # a killed run between sidecar writes: nothing to patch
    try:
        data = read_json(p)
    except PierError as exc:
        # Bookkeeping must not mask how the run itself ended.
        print(f"pier: cannot update {sid_to_ref(sid)}: {exc}", file=sys.stderr)
        return
    for k, v in fields.items():
        if v is None:
            data.pop(k, None)
        else:
            data[k] = v
    write_json(p, data)


def start_session(home: Path, sid: str, cwd: Path, flags: List[str],
                  agent: str, wt: Optional[Dict[str, str]]) -> None:
    """Persist the sidecar before Pi starts, so a killed run can still be sent
    to or read back. The recorded cwd is the directory Pi runs in: the worktree
    when `wt` is set, so `send` keeps the same isolation."""
    data: Dict[str, Any] = {"cwd": str(cwd), "flags": flags, "agent": agent}
    if wt:
        data.update(worktree=wt["worktree"], start_head=wt["start_head"],
                    origin_cwd=wt["origin_cwd"])
    ensure_dir(sessions_dir(home))
    write_json(sidecar_path(home, sid), data)


def git_run(args: List[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    """Run git in `cwd`; a missing git or a timeout is a PierError."""
    try:
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PierError(f"git {args[0]} failed in {cwd}: {exc}") from None


def make_worktree(home: Path, ref: str, cwd: Path) -> Dict[str, str]:
    """`git worktree add --detach $PIER_HOME/worktrees/<ref>`; a HEAD snapshot,
    without the caller's uncommitted changes (Claude Code isolation semantics)."""
    try:
        inside = git_run(["rev-parse", "--is-inside-work-tree"], cwd, 30)
        head = git_run(["rev-parse", "HEAD"], cwd, 30)
    except PierError as exc:
        raise PierError(f"--worktree: {exc}") from None
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise PierError(f"--worktree: {cwd} is not a git repository")
    if head.returncode != 0:
        raise PierError(f"--worktree: {cwd} has no commits to snapshot")
    wt = home / "worktrees" / ref
    try:
        ensure_dir(wt.parent)
        r = git_run(["worktree", "add", "--detach", str(wt), "HEAD"], cwd, 60)
    except (PierError, OSError) as exc:
        raise PierError(f"--worktree: {exc}") from None
    if r.returncode != 0:
        raise PierError(f"--worktree: git worktree add failed: {tail_text(r.stderr)}")
    return {"worktree": str(wt), "start_head": head.stdout.strip(), "origin_cwd": str(cwd)}


def cleanup_worktree(home: Path, sid: str, ref: str, wt: Dict[str, str]) -> None:
    """Auto-remove a pristine worktree; keep a dirty one and print its path.
    Cleanup never raises: it runs in a finally, including on SIGTERM."""
    path = Path(wt["worktree"])
    if not path.is_dir():
        print(f"pier: {ref} worktree is gone; send runs in {wt['origin_cwd']}", file=sys.stderr)
        restore_project_cwd(home, sid, wt)
        return
    try:
        dirty = git_run(["status", "--porcelain"], path, 30).stdout.strip()
        head = git_run(["rev-parse", "HEAD"], path, 30).stdout.strip()
    except PierError as exc:
        print(f"pier: {ref} kept worktree ({exc}): {path}", file=sys.stderr)
        return
    if dirty or head != wt["start_head"]:
        print(f"pier: {ref} kept worktree (has changes): {path}", file=sys.stderr)
        return
    try:
        r = git_run(["worktree", "remove", str(path)], path, 60)
    except PierError as exc:
        print(f"pier: {ref} worktree remove failed ({exc}); kept: {path}", file=sys.stderr)
        return
    if r.returncode != 0:
        print(f"pier: {ref} worktree remove failed: {tail_text(r.stderr)}; kept: {path}",
              file=sys.stderr)
        return
    restore_project_cwd(home, sid, wt)


def restore_project_cwd(home: Path, sid: str, wt: Dict[str, str]) -> None:
    """Point the sidecar back at the project once its worktree is gone, so
    `send` still has a directory to run in. `worktree` stays as the record of
    where the run happened."""
    try:
        patch_sidecar(home, sid, cwd=wt["origin_cwd"])
    except (PierError, OSError) as exc:
        print(f"pier: cannot update {sid_to_ref(sid)} after worktree removal: {exc}",
              file=sys.stderr)


def tail_text(text: str, n: int = STDERR_TAIL_CHARS) -> str:
    return text.strip()[-n:]


def fmt_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 10_000:
        return f"{n / 1000:.1f}k"
    if n < 1_000_000:
        return f"{n / 1000:.0f}k"
    return f"{n / 1_000_000:.1f}M"


def fmt_age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"
