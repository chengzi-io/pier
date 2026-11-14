#!/usr/bin/env python3
"""Tests for pier. Uses mock/mock_pi only; never calls a model.

Optional: PIER_TEST_REAL_PI=/path/to/pi runs an argv-parse check against a real
Pi binary with an empty HOME (no credentials -> no model call).
"""
from __future__ import annotations

import contextlib
import errno
import io
import json
import os
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "pier"
PIER = SKILL / "scripts" / "pier"
MOCK = ROOT / "mock" / "mock_pi"

sys.path.insert(0, str(SKILL / "scripts"))
import pier_agents  # noqa: E402
import pi_runtime  # noqa: E402
import pi_state  # noqa: E402
import toon  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pier-test-"))
        self.proj = self.tmp / "proj with space"
        self.proj.mkdir()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.rec = self.tmp / "argv.json"
        self.rec_dir = self.tmp / "calls"
        self.env = {
            "PI_BIN": str(MOCK), "HOME": str(self.home), "MOCK_PI_RECORD": str(self.rec),
            "MOCK_PI_RECORD_DIR": str(self.rec_dir),
        }
        for k in ("PIER_HOME", "MOCK_PI_FAIL", "MOCK_PI_FAIL_ON",
                  "MOCK_PI_SLEEP", "MOCK_PI_SLEEP_ON", "MOCK_PI_REPLY",
                  "MOCK_PI_CHILD", "MOCK_PI_CHILD_PID", "CLAUDE_CODE_SESSION_ID",
                  "PIER_MAX_RUNNING", "PIER_MAX_RUNNING_PER_SESSION"):
            self.env[k] = ""

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def full_env(self, env=None):
        e = {k: v for k, v in os.environ.items()}
        e.update(self.env)
        e = {k: v for k, v in e.items() if v != ""}
        if env:
            e.update(env)
        return e

    def pier(self, *args, env=None, input_text=None, cwd=None):
        return subprocess.run([sys.executable, str(PIER), *args], cwd=str(cwd or self.proj),
                              env=self.full_env(env), input=input_text,
                              stdin=None if input_text is not None else subprocess.DEVNULL,
                              text=True, capture_output=True, timeout=60)

    def dry(self, *args, **kw):
        """Run with --dry-run and return the Pi argv parsed from `cd DIR && pi ...`."""
        args = list(args) + ["--dry-run"]
        r = self.pier(*args, **kw)
        self.assertEqual(r.returncode, 0, r.stderr)
        tokens = shlex.split(r.stdout)
        self.assertEqual(tokens[0], "cd")
        self.assertEqual(tokens[2], "&&")
        return tokens[3:]

    def pier_merged(self, *args, env=None, cwd=None):
        """Run pier with stdout and stderr on one file, as Claude Code does."""
        log = self.tmp / "merged.log"
        with open(log, "w", encoding="utf-8") as fh:
            r = subprocess.run([sys.executable, str(PIER), *args], cwd=str(cwd or self.proj),
                               env=self.full_env(env), stdin=subprocess.DEVNULL,
                               stdout=fh, stderr=subprocess.STDOUT, text=True, timeout=60)
        return r.returncode, log.read_text(encoding="utf-8")

    def argv(self):
        return json.loads(self.rec.read_text())

    def calls(self):
        return [json.loads(p.read_text()) for p in sorted(self.rec_dir.glob("*.json"))] if self.rec_dir.exists() else []

    def state(self):
        return self.home / ".pier"

    def sidecar_dir(self):
        return self.state() / "sessions"

    def wait_for_running_pid(self, timeout=10):
        """Poll until a sidecar records a pid (a run or send is under way)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for p in self.sidecar_dir().glob("*.pier.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(data.get("pid"), int):
                    return p.name[:-len(".pier.json")]
            time.sleep(0.05)
        self.fail("no session recorded a running pid")

    def sid_of(self, r, home=None):
        """Full sid from the run's stderr; the 8-char ref is `ref_of`."""
        sessions = (home or self.state()) / "sessions"
        first = r.stderr.splitlines()[0]
        self.assertTrue(first.startswith("pier: "), r.stderr)
        cand = sessions / f"{first.split()[1]}.pier.json"
        if cand.is_file():
            return cand.name[:-len(".pier.json")]
        hits = sorted(sessions.glob(f"{first.split()[1]}*.pier.json"))
        self.assertEqual(len(hits), 1, r.stderr)  # the 8-char ref is unique
        return hits[0].name[:-len(".pier.json")]

    def ref_of(self, r):
        first = r.stderr.splitlines()[0]
        self.assertTrue(first.startswith("pier: "), r.stderr)
        ref = first.split()[1]
        self.assertEqual(len(ref), 8)
        return ref

    def write_agent(self, rel, text):
        p = self.proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p


def flag(argv, name):
    return argv[argv.index(name) + 1]


class FrontmatterTests(unittest.TestCase):
    def setUp(self):
        pass

    def test_simple(self):
        fm, body = pier_agents.parse_frontmatter("---\nname: explorer\ntools: read, grep\n---\n\nHello body\n")
        self.assertEqual(fm, {"name": "explorer", "tools": "read, grep"})
        self.assertEqual(body, "Hello body")

    def test_blocks_and_lists(self):
        fm, _ = pier_agents.parse_frontmatter("---\nname: x\ndescription: |\n  one\n  two\ntools:\n  - read\n  - grep\n---\nB\n")
        self.assertEqual(fm["description"], "one\ntwo")
        self.assertEqual(pier_agents.parse_list(fm["tools"]), ["read", "grep"])
        self.assertEqual(pier_agents.parse_list("- read  # c\n- 'grep'"), ["read", "grep"])

    def test_ignored_fields(self):
        ignored = pier_agents.ignored_fields({"name": "a", "model": "m", "description": "d", "async": "true",
                                              "systemPromptMode": "replace", "launcher": "x", "weird": "1"})
        self.assertEqual(ignored, ["async", "launcher", "weird"])


class LastTurnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pier-reply-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def session(self, *messages, trailer=""):
        p = self.tmp / "s.jsonl"
        lines = [json.dumps({"type": "session", "id": "x"})]
        lines += [json.dumps({"type": "message", "message": m}) for m in messages]
        p.write_text("\n".join(lines) + "\n" + trailer, encoding="utf-8")
        return p

    def test_reply_is_last_assistant_text_of_last_turn(self):
        def text(role, t):
            return {"role": role, "content": [{"type": "text", "text": t}]}
        p = self.session(
            text("user", "q1"), text("assistant", "a1"),
            text("user", "q2"),
            {"role": "assistant", "content": [{"type": "toolCall", "name": "read"}]},
            {"role": "toolResult", "content": [{"type": "text", "text": "file body"}]},
            text("assistant", "a2 final"),
            trailer='{"type":"message","mess',  # line cut short by a killed Pi
        )
        t = pi_state.last_turn(p)
        self.assertEqual((t.reply, t.stop), ("a2 final", None))

    def test_unanswered_turn_has_no_reply(self):
        p = self.session({"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"},
                         {"role": "user", "content": "q2"})
        self.assertEqual(pi_state.last_turn(p).reply, "")

    def test_usage_is_summed_over_the_turn(self):
        # Pi writes one per-call usage block per assistant message; a turn with
        # tool calls has several. Each is non-cumulative.
        def am(u):
            return {"role": "assistant", "content": [{"type": "text", "text": "x"}], "usage": u}
        blocks = [
            {"input": 10, "output": 2, "cacheRead": 100, "cacheWrite": 0, "reasoning": 1,
             "totalTokens": 112, "cost": {"input": 0.1, "output": 0.2, "total": 0.3}},
            {"input": 20, "output": 3, "cacheRead": 200, "cacheWrite": 0, "reasoning": 2,
             "totalTokens": 223, "cost": {"input": 0.4, "output": 0.5, "total": 0.9}},
            {"input": 30, "output": 4, "cacheRead": 300, "cacheWrite": 0, "reasoning": 3,
             "totalTokens": 334, "cost": {"input": 0.6, "output": 0.7, "total": 1.3}},
        ]
        p = self.session({"role": "user", "content": "q"}, *[am(u) for u in blocks])
        usage = pi_state.last_turn(p).usage
        self.assertEqual(usage["input"], 60)
        self.assertEqual(usage["output"], 9)
        self.assertEqual(usage["cacheRead"], 600)
        self.assertEqual(usage["totalTokens"], 669)
        self.assertAlmostEqual(usage["cost"]["total"], 2.5)

    def test_failed_stop_reason_beats_exit_zero_and_earlier_text(self):
        # JSON-mode Pi exits 0 on provider errors; the session entry is authoritative.
        t = pi_state.Turn("let me look", "error", "529 overloaded")
        out = pi_runtime.turn_outcome(t, 0, "", "deadbeef")
        self.assertEqual(out.reply, "")
        self.assertIn("529 overloaded", out.error)
        self.assertEqual(out.code, 1)

    def test_tool_calls_are_counted_and_reset_at_a_new_user_message(self):
        def call(name):
            return {"role": "assistant",
                    "content": [{"type": "toolCall", "name": name,
                                 "arguments": {"path": "/etc/shadow"}}]}
        p = self.session({"role": "user", "content": "q"}, call("grep"), call("read"),
                         {"role": "toolResult", "content": [{"type": "text", "text": "x"}]})
        t = pi_state.last_turn(p)
        self.assertEqual((t.tool_calls, t.last_tool), (2, "read"))
        p = self.session({"role": "user", "content": "q1"}, call("grep"),
                         {"role": "user", "content": "q2"}, call("edit"))
        t = pi_state.last_turn(p)
        self.assertEqual((t.tool_calls, t.last_tool), (1, "edit"))


class HeartbeatTests(Base):
    """The progress line: in-process, because pier has no flag or env var for a
    short interval and the line must still carry the real ref."""

    def session_file(self, sid, *messages):
        sdir = self.state() / "sessions"
        sdir.mkdir(parents=True, exist_ok=True)
        p = sdir / f"2026-01-01T00-00-00_{sid}.jsonl"
        lines = [json.dumps({"type": "session", "id": sid})]
        lines += [json.dumps({"type": "message", "message": m}) for m in messages]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p

    def test_progress_line_names_tools_and_tokens(self):
        def call(name):
            return {"role": "assistant",
                    "content": [{"type": "toolCall", "name": name,
                                 "arguments": {"path": "/etc/shadow"}}]}
        sid = "c0ffee00-0000-0000-0000-000000000000"
        now = time.monotonic()
        self.session_file(sid, {"role": "user", "content": "q"}, call("grep"), call("read"),
                          {"role": "assistant", "content": [{"type": "text", "text": "x"}],
                           "usage": {"input": 1500, "output": 301, "totalTokens": 1801}})
        line = pi_runtime.progress_line(self.state(), sid, sid[:8], now - 370, now + 850)
        self.assertEqual(line, f"pier: {sid[:8]} running 6m · 2 tools, last read · 1.8k tok"
                               " · 14m left")
        self.assertNotIn("/etc/shadow", line)  # tool arguments never reach stderr

    def test_progress_line_without_a_session_file_has_no_tools_or_tokens(self):
        sid = "00000000-0000-0000-0000-000000000000"
        now = time.monotonic()
        line = pi_runtime.progress_line(self.state(), sid, sid[:8], now - 5, now + 60)
        self.assertEqual(line, f"pier: {sid[:8]} running 5s · 59s left")

    def test_progress_line_read_error_is_reported_not_raised(self):
        def boom(_path):  # a malformed line, not only an I/O error
            raise AttributeError("'list' object has no attribute 'get'")
        sid = "deadbeef-0000-0000-0000-000000000000"
        self.session_file(sid, {"role": "user", "content": "q"})
        real, pi_runtime.last_turn = pi_runtime.last_turn, boom
        try:
            line = pi_runtime.progress_line(self.state(), sid, sid[:8], time.monotonic(),
                                            time.monotonic() + 60)
        finally:
            pi_runtime.last_turn = real
        self.assertTrue(line.startswith(f"pier: cannot read {sid[:8]} progress: "), line)

    def test_run_pi_prints_a_progress_line_while_pi_runs(self):
        sid = "abcdef00-1111-2222-3333-444455556666"
        ref = sid[:8]
        argv = [str(MOCK), "--print", "--session-dir", str(self.state() / "sessions"),
                "--session-id", sid, "--", "work"]
        deadline = time.monotonic() + 60
        started = time.monotonic()
        buf = io.StringIO()
        prev_sleep = os.environ.get("MOCK_PI_SLEEP")
        prev_step = pi_runtime.HEARTBEAT_SECONDS
        os.environ["MOCK_PI_SLEEP"], pi_runtime.HEARTBEAT_SECONDS = "1.3", 0.4
        try:
            with contextlib.redirect_stderr(buf):
                code, err = pi_runtime.run_pi(
                    self.proj, argv, deadline,
                    heartbeat=lambda: pi_runtime.progress_line(self.state(), sid, ref,
                                                               started, deadline))
        finally:
            pi_runtime.HEARTBEAT_SECONDS = prev_step
            if prev_sleep is None:
                os.environ.pop("MOCK_PI_SLEEP", None)
            else:
                os.environ["MOCK_PI_SLEEP"] = prev_sleep
        self.assertEqual(code, 0, err)
        lines = buf.getvalue().splitlines()
        self.assertGreaterEqual(len(lines), 2, lines)
        for line in lines:
            self.assertRegex(line, rf"^pier: {ref} running \d+s · \d+s left$")


class DiscoveryTests(Base):
    def test_lookup_order_project_pier_agents_wins(self):
        self.write_agent(".pier/agents/reviewer.md", "---\nname: reviewer\ndescription: p\n---\nPROJECT BODY\n")
        (self.state() / "agents").mkdir(parents=True)
        (self.state() / "agents" / "reviewer.md").write_text("---\nname: reviewer\n---\nUSER BODY\n")
        self.assertEqual(flag(self.dry("run", "reviewer", "t"), "--system-prompt"), "PROJECT BODY")
        shutil.rmtree(self.proj / ".pier" / "agents")
        self.assertEqual(flag(self.dry("run", "reviewer", "t"), "--system-prompt"), "USER BODY")

    def test_pi_dirs_never_read(self):
        self.write_agent(".pi/agents/scout.md", "---\nname: scout\n---\nSCOUT\n")
        self.write_agent(".agents/scout.md", "---\nname: scout\n---\nSCOUT2\n")
        r = self.pier("run", "scout", "t", "--dry-run")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown agent", r.stderr)

    def test_unknown_agent_lists_available(self):
        r = self.pier("run", "nope", "t")
        self.assertEqual(r.returncode, 2)
        for n in ("chore", "debugger", "librarian", "researcher", "reviewer", "worker"):
            self.assertIn(n, r.stderr)
        self.assertEqual(self.calls(), [])

    def test_list_is_toon_table(self):
        r = self.pier("agents", "list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("[6]{name,model,description}:"), r.stdout)
        self.assertIn('\n  reviewer,"",', r.stdout)  # no model: Pi's default
        self.write_agent(".pier/agents/m.md", "---\nname: m\nmodel: zai/glm:low\n---\nB\n")
        self.assertIn('\n  m,"zai/glm:low",', self.pier("agents", "list").stdout)

    def test_unreadable_agent_is_skipped_with_a_note(self):
        self.write_agent(".pier/agents/ok.md", "---\nname: ok\n---\nB\n")
        (self.proj / ".pier" / "agents" / "bad.md").write_bytes(b"\xff\xfe bad")
        r = self.pier("agents", "list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("skipping agent", r.stderr)
        self.assertIn("\n  ok,", r.stdout)

    def test_show_prints_path_and_file(self):
        r = self.pier("agents", "show", "reviewer")
        self.assertEqual(r.stdout.splitlines()[0], str(SKILL / "agents" / "reviewer.md"))
        self.assertIn("You review the change specified by the task", r.stdout)


class MappingTests(Base):
    def test_full_mapping(self):
        self.write_agent(".pier/agents/m.md",
                         "---\nname: m\nmodel: anthropic/claude-x\nthinking: high\ntools: read, bash\n"
                         "excludeTools: write\ndefaultProvider: openai\n---\nBODY\n")
        a = self.dry("run", "m", "go")
        self.assertEqual(a[0], str(MOCK))
        self.assertEqual(a[1], "--print")
        self.assertNotIn("--provider", a)  # model already has provider/
        self.assertEqual(flag(a, "--model"), "anthropic/claude-x")
        self.assertEqual(flag(a, "--thinking"), "high")
        self.assertEqual(flag(a, "--tools"), "read,bash")
        self.assertEqual(flag(a, "--exclude-tools"), "write")
        self.assertNotIn("--no-mcp", a)  # MCP reach stays Pi's own semantics
        self.assertEqual(flag(a, "--name"), "m")
        self.assertEqual(Path(flag(a, "--session-dir")).resolve(), (self.state() / "sessions").resolve())
        self.assertIn("--session-id", a)
        self.assertEqual(a[-2:], ["--", "go"])
        self.assertFalse(self.state().exists())  # dry-run writes nothing
        self.assertFalse(self.rec.exists())  # and never runs pi

    def test_default_provider_and_suffix(self):
        self.write_agent(".pier/agents/p.md", "---\nname: p\nmodel: gpt-x:low\nthinking: high\ndefaultProvider: openai\n---\nB\n")
        a = self.dry("run", "p", "go")
        self.assertEqual(flag(a, "--provider"), "openai")
        self.assertEqual(flag(a, "--model"), "gpt-x:low")
        self.assertNotIn("--thinking", a)  # suffix wins over frontmatter thinking
        a = self.dry("run", "p", "go", "--model", "zai/glm:max")  # --model wins, suffix included
        self.assertEqual(flag(a, "--model"), "zai/glm:max")
        self.assertNotIn("--provider", a)
        self.assertNotIn("--thinking", a)

    def test_model_is_the_only_override(self):
        a = self.dry("run", "reviewer", "x", "--model", "zai/glm")
        self.assertEqual(flag(a, "--model"), "zai/glm")
        self.assertEqual(flag(a, "--tools"), "read,grep,find,ls,bash")  # frontmatter kept
        for removed in (["--tools", "read"], ["--no-tools"], ["--exclude-tools", "bash"],
                        ["--provider", "p"], ["--thinking", "high"], ["--name", "n"],
                        ["--max-chars", "1"], ["--cwd", "."]):
            r = self.pier("run", "reviewer", "x", *removed)
            self.assertEqual(r.returncode, 2, removed)
            self.assertIn("unrecognized arguments", r.stderr)
        r = self.pier("send", "deadbeef", "x", "--dry-run")
        self.assertIn("unrecognized arguments", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_unknown_tool_names_pass_through(self):
        # Pi ignores a name that matches no registered tool, and `contact_supervisor`
        # is a real name while pi-subagents is loaded: pier passes both through.
        self.write_agent(".pier/agents/e.md",
                         "---\nname: e\ntools: contact_supervisor, mcp:x/y\nlauncher: tmux\n---\nB\n")
        r = self.pier("run", "e", "go", "--dry-run")
        argv = shlex.split(r.stdout)
        self.assertEqual(flag(argv, "--tools"), "contact_supervisor,mcp:x/y")
        self.assertIn("launcher", r.stderr)
        r = self.pier("run", "e", "go")
        lines = r.stderr.splitlines()
        self.assertTrue(lines[1].startswith("pier: ") and " ok (e)" in lines[1], r.stderr)

    def test_mcp_tools_and_globs_pass_through(self):
        # Pi names MCP tools `mcp__<server>__<tool>`; `*` is a Pi tool pattern.
        self.write_agent(".pier/agents/m.md",
                         "---\nname: m\ntools: read, mcp__radius__search, mcp__radius__*\n---\nB\n")
        a = self.dry("run", "m", "go")
        self.assertEqual(flag(a, "--tools"), "read,mcp__radius__search,mcp__radius__*")
        self.assertNotIn("--no-mcp", a)  # pier never narrows MCP; Pi decides

    def test_no_mcp_is_never_added(self):
        # Pi's --tools keeps MCP tools callable from codemode scripts whatever the
        # list says; turning MCP off is `excludeTools: mcp__*` (tools) or
        # `--no-mcp` (server connections), never an implicit side effect.
        self.assertNotIn("--no-mcp", self.dry("run", "librarian", "go"))
        self.write_agent(".pier/agents/t.md", "---\nname: t\ntools:\n---\nB\n")
        a = self.dry("run", "t", "go")
        self.assertIn("--no-tools", a)
        self.assertNotIn("--no-mcp", a)
        self.write_agent(".pier/agents/d.md", "---\nname: d\ntools: read, bash\n---\nB\n")
        self.assertNotIn("--no-mcp", self.dry("run", "d", "go"))

    def test_prompt_is_task_and_body_is_system_prompt(self):
        self.write_agent(".pier/agents/c.md", "---\nname: c\n---\n\nROLE LINE\n\nSecond para.\n\n")
        a = self.dry("run", "c", "DO THE THING")
        self.assertEqual(a[-1], "DO THE THING")
        self.assertEqual(flag(a, "--system-prompt"), "ROLE LINE\n\nSecond para.")

    def test_quoting_unicode_roundtrip(self):
        task = "it's \"quoted\" $HOME `x` 中文 ✓"
        self.assertEqual(self.dry("run", "reviewer", task)[-1], task)

    def test_extra_option_is_gone(self):
        r = self.pier("run", "reviewer", "x", "--extra", "--offline")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unrecognized arguments", r.stderr)
        self.assertFalse(self.state().exists())

    def test_large_prompt_uses_temp_file(self):
        # large tasks go through a temp @file: feed the big text via stdin
        big = "x" * 120_000
        r = self.pier("run", "reviewer", input_text=big)
        self.assertEqual(r.returncode, 0, r.stderr)
        at = self.argv()[-1]
        self.assertTrue(at.startswith("@"))
        self.assertIn("xxxx", r.stdout)
        self.assertFalse(Path(at[1:]).exists())  # cleaned up after Pi exits


class InputModesTests(Base):
    def test_task_stdin_when_no_words(self):
        r = self.pier("run", "reviewer", input_text="LONG TASK 中文\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.argv()[-1], "LONG TASK 中文")

    def test_words_beat_stdin(self):
        r = self.pier("run", "reviewer", "WORDS", input_text="leak")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.argv()[-1], "WORDS")

    def test_dash_task_needs_separator(self):
        r = self.pier("run", "reviewer", "-hello")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertIn("after '--'", r.stderr)
        r = self.pier("run", "reviewer", "--", "-hello")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.argv()[-1], "-hello")

    def test_empty_stdin_rejected(self):
        r = self.pier("run", "reviewer", input_text="  \n")
        self.assertEqual(r.returncode, 2)
        self.assertIn("empty task", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_agent_file_direct(self):
        p = self.tmp / "tmp agent.md"
        p.write_text("---\nname: adhoc\ntools: read\n---\nADHOC ROLE\n")
        a = self.dry("run", "--agent-file", str(p), "do", "it")
        self.assertEqual(flag(a, "--system-prompt"), "ADHOC ROLE")
        self.assertEqual(flag(a, "--name"), "adhoc")
        self.assertEqual(a[-1], "do it")
        (self.tmp / "role.txt").write_text("FILE ROLE\n")  # no frontmatter: just a role
        a = self.dry("run", "--agent-file", str(self.tmp / "role.txt"), "t")
        self.assertEqual(flag(a, "--system-prompt"), "FILE ROLE")
        self.assertEqual(flag(a, "--name"), "role")

    def test_executable_runs_directly(self):
        r = subprocess.run([str(PIER), "--version"], capture_output=True, text=True)
        self.assertIn("pier 0.1.0", r.stdout)


class AgentAuthoringTests(Base):
    GOOD = "---\nname: sql-reviewer\ndescription: Reviews SQL\ntools: read, grep\nthinking: low\n---\n\nYou review SQL.\n"

    def test_validate(self):
        p = self.tmp / "sql-reviewer.md"
        p.write_text(self.GOOD)
        r = self.pier("agents", "validate", str(p))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("ok: true", r.stdout)
        p.write_text("---\nname: bad name\nthinking: ultra\nlauncher: x\n---\n")
        r = self.pier("agents", "validate", str(p))
        self.assertEqual(r.returncode, 1)
        self.assertIn("errors[3]:", r.stdout)
        self.assertIn("no effect", r.stdout)

    def test_validate_rejects_bad_enum_and_booleans(self):
        p = self.tmp / "x.md"
        p.write_text("---\nname: x\ndescription: d\nsystemPromptMode: prepend\n"
                     "inheritSkills: maybe\ndefaultProvider: anthropic\n---\nROLE\n")
        r = self.pier("agents", "validate", str(p))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("systemPromptMode", r.stdout)
        self.assertIn("inheritSkills", r.stdout)
        self.assertIn("defaultProvider has no effect without model", r.stdout)


class SessionTests(Base):
    def test_run_prints_reply_and_session(self):
        r = self.pier("run", "reviewer", "hello")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "mock-ok: hello\n")
        sid = self.sid_of(r)
        side = json.loads((self.state() / "sessions" / f"{sid}.pier.json").read_text())
        self.assertEqual(side["cwd"], str(self.proj.resolve()))
        self.assertIn("--append-system-prompt", side["flags"])
        self.assertEqual(flag(self.argv(), "--session-id"), sid)

    def test_failure_exit_code_and_stderr(self):
        r = self.pier("run", "reviewer", "x", env={"MOCK_PI_FAIL": "1"})
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout, "")
        self.assertIn("pi exited 3", r.stderr)
        self.assertIn("simulated", r.stderr)

    def test_model_error_is_failure_not_intermediate_text(self):
        for stop in ("error", "aborted"):
            r = self.pier("run", "reviewer", "x", env={"MOCK_PI_STOP": stop})
            self.assertEqual(r.returncode, 1, stop)
            self.assertEqual(r.stdout, "", stop)  # never the "let me look" text
            self.assertIn(f"model run {stop}: mock {stop}: 529 overloaded", r.stderr)

    def test_turn_ending_on_a_tool_call_is_a_failure(self):
        # seen with real Pi: an extension ends a --print run right after the
        # model asks for a tool; the preamble text is not the reply
        r = self.pier("run", "reviewer", "x", env={"MOCK_PI_STOP": "toolUse", "MOCK_PI_REPLY": "I'll look."})
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn("turn ended at a tool call", r.stderr)
        self.assertIn(",failed,", self.pier("ps").stdout)

    def test_length_prints_partial_reply_and_fails(self):
        r = self.pier("run", "reviewer", "x", env={"MOCK_PI_STOP": "length", "MOCK_PI_REPLY": "PARTIAL"})
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "PARTIAL\n")
        self.assertIn("output token limit", r.stderr)
        # a hint the caller can paste: a literal `<ref>` is a shell redirect
        self.assertIn(f'pier send {self.ref_of(r)} "continue"', r.stderr)
        self.assertIn(",failed,", self.pier("ps").stdout)  # a cut reply is not ok

    def test_over_budget_reply_spills_to_file(self):
        reply = "A" * 50_000
        r = self.pier("run", "reviewer", "x", env={"MOCK_PI_REPLY": reply})
        self.assertEqual(r.returncode, 0, r.stderr)
        sid = self.sid_of(r)
        path = self.state() / "out" / f"{sid}.md"
        self.assertEqual(path.read_text(encoding="utf-8"), reply)  # byte-identical spill
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "A" * 2_000)  # 2k preview
        self.assertRegex(lines[-1], r"\[\.\.\. 48000 more chars; full reply: .+\]$")  # last line = path
        self.assertIn(str(path), lines[-1])
        r = self.pier("run", "reviewer", "x", env={"MOCK_PI_REPLY": reply, "PIER_MAX_CHARS": "0"})
        zero_sid = self.sid_of(r)
        zero_path = self.state() / "out" / f"{zero_sid}.md"
        self.assertEqual(r.stdout, f"full reply: {Path(zero_path).resolve()}\n")  # 0 = path only
        r = self.pier("run", "reviewer", "x", env={"MOCK_PI_REPLY": reply, "PIER_MAX_CHARS": "100000"})
        self.assertEqual(r.stdout, reply + "\n")  # a large budget inlines it all

    def test_small_budget_caps_the_preview(self):
        # budget < reply < PREVIEW_CHARS used to print the whole reply and a
        # negative "more chars" count.
        reply = "C" * 1_500
        r = self.pier("run", "reviewer", "x", env={"MOCK_PI_REPLY": reply, "PIER_MAX_CHARS": "1000"})
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "C" * 1_000)
        self.assertRegex(lines[-1], r"\[\.\.\. 500 more chars; full reply: .+\]$")
        self.assertNotIn("more chars: -", r.stdout)

    def test_max_chars_env_default_and_failure_keeps_path_last(self):
        reply = "B" * 30_000
        r = self.pier("run", "reviewer", "x", env={"MOCK_PI_REPLY": reply, "PIER_MAX_CHARS": "5000"})
        self.assertEqual(r.returncode, 0, r.stderr)
        sid = self.sid_of(r)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "B" * 2_000)
        self.assertIn(str(Path(self.state() / "out" / f"{sid}.md").resolve()), lines[-1])
        # a nonzero exit keeps the path as stdout's last line
        r = self.pier("send", sid, "again",
                      env={"MOCK_PI_REPLY": reply, "MOCK_PI_STOP": "length", "PIER_MAX_CHARS": "10"})
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stdout.splitlines()[-1].startswith("[... "), r.stdout)

    def test_missing_pi(self):
        r = self.pier("run", "reviewer", "x", env={"PI_BIN": "/nonexistent/pi"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("does not install Pi", r.stderr)
        self.assertFalse((self.state() / "sessions").exists())
        r = self.pier("run", "reviewer", "x", "--dry-run", env={"PI_BIN": "/nonexistent/pi"})
        self.assertEqual(r.returncode, 0)

    def test_send_replays_agent_setup(self):
        self.write_agent(".pier/agents/r.md",
                         "---\nname: r\ntools: read\ninheritProjectContext: false\n---\nROLE R\n")
        sid = self.sid_of(self.pier("run", "r", "first"))
        r = self.pier("send", sid, "follow", "up", cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, f"mock-ok: follow up [resumed {sid}]\n")
        a = self.argv()
        self.assertEqual(flag(a, "--system-prompt"), "ROLE R")
        self.assertEqual(flag(a, "--tools"), "read")
        self.assertIn("--no-context-files", a)
        self.assertTrue(flag(a, "--session").endswith(f"_{sid}.jsonl"))
        self.assertNotIn("--session-id", a)
        self.assertEqual(self.calls()[-1][-1], "follow up")

    def test_send_unknown_session(self):
        r = self.pier("send", "00000000-0000-0000-0000-000000000000", "x")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown session", r.stderr)
        r = self.pier("send", "deadbeef", "x")
        self.assertEqual(r.returncode, 2)
        # an unknown ref after at least one run hints at the recent sessions
        first = self.pier("run", "reviewer", "hello")
        r = self.pier("send", "deadbeef", "x")
        self.assertIn("recent sessions:", r.stderr)
        self.assertIn(self.ref_of(first), r.stderr)

    def test_run_stderr_is_started_then_summary(self):
        r = self.pier("run", "reviewer", "hello")
        ref = self.ref_of(r)
        self.assertEqual(r.stderr.splitlines(),
                         [f"pier: {ref} started (reviewer)",
                          f"pier: {ref} ok (reviewer)"])
        r = self.pier("run", "reviewer", "x", env={"MOCK_PI_FAIL": "1"})
        ref = self.ref_of(r)
        line = r.stderr.splitlines()[1]
        self.assertTrue(line.startswith(f"pier: {ref} failed (reviewer)"), r.stderr)
        self.assertNotIn(" — ", line)  # the reason is reported once, below
        self.assertEqual(r.stderr.count("pier: error:"), 1, r.stderr)

    def test_one_file_output_keeps_the_reply_before_the_summary(self):
        # Claude Code points stdout and stderr at one file: the reply must come
        # before the summary, and the error stays the very last line.
        code, log = self.pier_merged("run", "reviewer", "hello")
        self.assertEqual(code, 0, log)
        ref = log.splitlines()[0].split()[1]
        self.assertEqual(log.splitlines(), [f"pier: {ref} started (reviewer)",
                                            "mock-ok: hello",
                                            f"pier: {ref} ok (reviewer)"])
        code, log = self.pier_merged("run", "reviewer", "x",
                                     env={"MOCK_PI_STOP": "length", "MOCK_PI_REPLY": "PARTIAL"})
        self.assertEqual(code, 1, log)
        lines = log.splitlines()
        ref = lines[0].split()[1]
        self.assertEqual(lines[:3], [f"pier: {ref} started (reviewer)", "PARTIAL",
                                     f"pier: {ref} failed (reviewer)"])
        self.assertEqual(len(lines), 4, log)  # the error is the last line
        self.assertTrue(lines[3].startswith("pier: error: "), log)
        sid = self.sid_of(self.pier("run", "reviewer", "first"))
        code, log = self.pier_merged("send", sid, "follow")
        self.assertEqual(code, 0, log)
        # send announces itself too: a background send is not blank until it ends
        self.assertEqual(log.splitlines(), [f"pier: {sid[:8]} started (reviewer)",
                                            f"mock-ok: follow [resumed {sid}]",
                                            f"pier: {sid[:8]} ok (reviewer)"])
        code, log = self.pier_merged("send", sid, "again",
                                     env={"MOCK_PI_STOP": "length", "MOCK_PI_REPLY": "PART"})
        self.assertEqual(code, 1, log)
        lines = log.splitlines()
        self.assertEqual(lines[:3], [f"pier: {sid[:8]} started (reviewer)",
                                     f"PART [resumed {sid}]", f"pier: {sid[:8]} failed (reviewer)"])
        self.assertEqual(len(lines), 4, log)  # the error is the last line
        self.assertTrue(lines[3].startswith("pier: error: "), log)

    def test_send_resolves_prefix_and_errors_on_ambiguity(self):
        sid = self.sid_of(self.pier("run", "reviewer", "first"))
        r = self.pier("send", sid[:12], "via prefix", cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[resumed", r.stdout)
        self.assertEqual(r.stderr.splitlines()[0], f"pier: {sid[:8]} started (reviewer)")
        r = self.pier("send", "reviewer", "x")  # an agent name is not a session handle
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown session", r.stderr)
        # an ambiguous prefix names its alternatives
        other = self.state() / "sessions" / f"{sid[:6]}aa{sid[6:].replace('-', '')}.pier.json"
        other.write_text(json.dumps({"cwd": str(self.tmp), "flags": [], "agent": "other"}), encoding="utf-8")
        try:
            r = self.pier("send", sid[:6], "x")
            self.assertEqual(r.returncode, 2)
            self.assertIn("ambiguous", r.stderr)
            self.assertIn(sid[:6], r.stderr)  # the alternatives are named by their refs
        finally:
            other.unlink()

    def test_send_refuses_a_running_session(self):
        slow = subprocess.Popen([sys.executable, str(PIER), "run", "reviewer", "slow"],
                                cwd=str(self.proj), env=self.full_env({"MOCK_PI_SLEEP": "30"}),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        try:
            sid = self.wait_for_running_pid()
            r = self.pier("send", sid, "x")
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("is running", r.stderr)
        finally:
            slow.send_signal(signal.SIGTERM)
            self.assertEqual(slow.wait(timeout=10), 128 + signal.SIGTERM)
            slow.stdout.close()
            slow.stderr.close()

    def test_second_send_is_refused_and_lock_is_released(self):
        sid = self.sid_of(self.pier("run", "reviewer", "first"))
        slow = subprocess.Popen([sys.executable, str(PIER), "send", sid, "slow"],
                                cwd=str(self.proj), env=self.full_env({"MOCK_PI_SLEEP": "30"}),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        try:
            self.wait_for_running_pid()
            r = self.pier("send", sid, "again")
            self.assertEqual(r.returncode, 2)
            self.assertIn("is running", r.stderr)
        finally:
            slow.send_signal(signal.SIGTERM)
            self.assertEqual(slow.wait(timeout=10), 128 + signal.SIGTERM)
            slow.stdout.close()
            slow.stderr.close()
        self.assertFalse((self.sidecar_dir() / f"{sid}.lock").exists())

    def test_stale_lock_is_reclaimed(self):
        sid = self.sid_of(self.pier("run", "reviewer", "first"))
        dead = subprocess.Popen([sys.executable, "-c", ""])
        dead.wait()
        lock = self.sidecar_dir() / f"{sid}.lock"
        lock.write_text(str(dead.pid), encoding="utf-8")
        r = self.pier("send", sid, "after a crash")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(lock.exists())

    def test_stale_lock_with_a_reused_pid_is_reclaimed(self):
        sid = self.sid_of(self.pier("run", "reviewer", "first"))
        decoy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            lock = self.sidecar_dir() / f"{sid}.lock"
            lock.write_text(f"{decoy.pid}\nThu Jan  1 00:00:00 1970\n", encoding="utf-8")
            r = self.pier("send", sid, "x")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(lock.exists())
        finally:
            decoy.terminate()
            decoy.wait(timeout=10)

    def test_a_reused_pid_does_not_block_the_session(self):
        sid = self.sid_of(self.pier("run", "reviewer", "first"))
        decoy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            side_path = self.sidecar_dir() / f"{sid}.pier.json"
            side = json.loads(side_path.read_text(encoding="utf-8"))
            side.update(pid=decoy.pid, pid_start="Thu Jan  1 00:00:00 1970")
            side_path.write_text(json.dumps(side), encoding="utf-8")
            r = self.pier("ps")
            self.assertNotIn("running", r.stdout)  # the pid is someone else's now
            r = self.pier("send", sid, "continue")
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            decoy.terminate()
            decoy.wait(timeout=10)

    def test_corrupt_sidecar_is_skipped_not_fatal(self):
        sid = self.sid_of(self.pier("run", "reviewer", "hello"))
        bad = self.sidecar_dir() / "0badc0de-0000-0000-0000-000000000000.pier.json"
        bad.write_text("{not json", encoding="utf-8")
        r = self.pier("ps")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("skipping session file", r.stderr)
        self.assertIn(sid[:8], r.stdout)  # the healthy session still lists
        r = self.pier("send", "0badc0de", "x")  # skipped, so unknown rather than a traceback
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown session", r.stderr)

    def test_state_and_reply_files_are_user_only(self):
        r = self.pier("run", "reviewer", "x", env={"MOCK_PI_REPLY": "P" * 30_000})
        self.assertEqual(r.returncode, 0, r.stderr)
        sid = self.sid_of(r)
        for d in (self.state(), self.state() / "sessions", self.state() / "out"):
            self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700, d)
        for f in (self.state() / "sessions" / f"{sid}.pier.json",
                  self.state() / "out" / f"{sid}.md"):
            self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600, f)

    def test_bad_max_chars_env_is_rejected_before_pi_runs(self):
        for raw in ("-1", "lots"):
            r = self.pier("run", "reviewer", "x", env={"PIER_MAX_CHARS": raw})
            self.assertEqual(r.returncode, 2)
            self.assertIn("PIER_MAX_CHARS", r.stderr)
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.sidecar_dir().exists())

    def test_dry_run_clips_long_task(self):
        r = self.pier("run", "reviewer", "word " * 4000, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(len(r.stdout), 2000)
        self.assertRegex(r.stdout, r"…<\d+ chars>")

    def test_ps_notes_the_limit(self):
        for i in range(12):
            self.sidecar_dir().mkdir(parents=True, exist_ok=True)
            (self.sidecar_dir() / f"00000000-0000-0000-0000-{i:012d}.pier.json").write_text(
                json.dumps({"cwd": str(self.proj.resolve()), "flags": [], "agent": "a"}),
                encoding="utf-8")
        r = self.pier("ps")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("showing 10 of 12 sessions", r.stderr)

    def test_killed_pier_stops_pi_and_session_survives(self):
        p = subprocess.Popen([sys.executable, str(PIER), "run", "reviewer", "slow"], cwd=str(self.proj),
                             env=self.full_env({"MOCK_PI_SLEEP": "30"}), stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(100):
            if self.rec_dir.exists() and any(self.rec_dir.iterdir()):
                break
            time.sleep(0.05)
        time.sleep(0.2)
        pi_pid = int(next(self.rec_dir.iterdir()).stem.rsplit("-", 1)[1])
        p.send_signal(signal.SIGTERM)  # pier only, as a host timeout might
        self.assertEqual(p.wait(timeout=10), 128 + signal.SIGTERM)
        sid = p.stderr.read().split()[1]  # `pier: <ref> started (...)`
        for _ in range(100):
            try:
                os.kill(pi_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("Pi outlived pier")
        p.stdout.close()
        p.stderr.close()
        r = self.pier("send", sid, "continue")  # the 8-char ref resolves
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[resumed", r.stdout)

    def test_ps_three_states(self):
        ok = self.sid_of(self.pier("run", "reviewer", "first"))
        dead = self.sid_of(self.pier("run", "reviewer", "kill me", env={"MOCK_PI_SLEEP": "30"}))
        # a slow run shows as running while pier is alive; killed -> failed
        slow = subprocess.Popen([sys.executable, str(PIER), "run", "reviewer", "slow"],
                                cwd=str(self.proj), env=self.full_env({"MOCK_PI_SLEEP": "30"}),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        try:
            for _ in range(100):
                if self.rec_dir.exists() and len(list(self.rec_dir.iterdir())) >= 1:
                    break
                time.sleep(0.05)
            time.sleep(0.3)
            r = self.pier("ps")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("running", r.stdout)
            rows = [line for line in r.stdout.splitlines() if line.startswith("sessions") or "," in line]
            self.assertIn("ok", r.stdout)
            slow.send_signal(signal.SIGTERM)
            self.assertEqual(slow.wait(timeout=10), 128 + signal.SIGTERM)
            slow.stdout.close()
            slow.stderr.close()
        finally:
            if slow.poll() is None:
                slow.terminate()
                slow.wait(timeout=10)
                slow.stdout.close()
                slow.stderr.close()
        r = self.pier("ps")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("running", r.stdout)  # killed run: pid dead -> failed
        self.assertIn("failed", r.stdout)
        r = self.pier("ps", self.ref_of(self.pier("run", "reviewer", "again")))
        # ps REF narrows to one row (the TOON header line also holds commas)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = [l for l in r.stdout.splitlines() if "," in l and not l.startswith("sessions")]
        self.assertEqual(len(rows), 1)

    def test_ps_shows_the_first_reply_line(self):
        # the report contract puts the result first
        self.pier("run", "reviewer", "x", env={"MOCK_PI_REPLY": "RESULT LINE\ndetail\nsign-off"})
        r = self.pier("ps")
        self.assertTrue(r.stdout.startswith("sessions[1]{ref,agent,state,age,tok,reply}:"), r.stdout)
        self.assertIn(",RESULT LINE", r.stdout)
        self.assertNotIn("sign-off", r.stdout)

    def test_ps_marks_a_run_pi_never_recorded_as_failed(self):
        # e.g. an unknown model: Pi exits before writing the session file
        self.pier("run", "reviewer", "x", env={"MOCK_PI_FAIL": "1"})
        self.assertIn(",failed,", self.pier("ps").stdout)

    def test_ps_lists_only_this_directory(self):
        self.pier("run", "reviewer", "here")
        other = self.tmp / "other"
        other.mkdir()
        self.assertIn("no sessions", self.pier("ps", cwd=other).stderr)


class RunControlTests(Base):
    """Run deadline (timeoutMs) and the concurrency caps."""

    def slow_agent(self, ms=1500):
        return self.write_agent(".pier/agents/slow.md",
                                f"---\nname: slow\ntimeoutMs: {ms}\n---\nSLOW ROLE\n")

    def spawn_run(self, *args, env=None):
        p = subprocess.Popen([sys.executable, str(PIER), *args], cwd=str(self.proj),
                             env=self.full_env(env), stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, p)
        return p

    def stop(self, p):
        if p.poll() is None:
            p.send_signal(signal.SIGTERM)
            p.wait(timeout=15)
        for f in (p.stdout, p.stderr):
            if f and not f.closed:
                f.close()

    def child_pid(self, path, timeout=15):
        """Wait for mock Pi's grandchild to write its pid."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if path.is_file() and path.read_text(encoding="utf-8").strip():
                return int(path.read_text(encoding="utf-8"))
            time.sleep(0.05)
        self.fail("mock Pi never spawned its grandchild")

    def assert_gone(self, pid, timeout=15):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        self.fail(f"pid {pid} is still alive")

    def test_timeout_kills_pi_tree_and_keeps_session_sendable(self):
        self.slow_agent(1000)
        kid_pid = self.tmp / "kid.pid"
        r = self.pier("run", "slow", "x", env={"MOCK_PI_SLEEP": "30", "MOCK_PI_CHILD": "1",
                                                 "MOCK_PI_CHILD_PID": str(kid_pid)})
        self.assertEqual(r.returncode, 124, r.stderr)
        self.assertEqual(r.stdout, "")
        ref = self.ref_of(r)
        self.assertEqual(r.stderr.splitlines()[:2],
                         [f"pier: {ref} started (slow)", f"pier: {ref} failed (slow)"])
        self.assertEqual(r.stderr.splitlines()[-1],
                         f'pier: error: timed out after 0.02m '
                         f'(continue it: pier send {ref} "continue")')
        self.assert_gone(self.child_pid(kid_pid))
        # the sidecar keeps the timeout, and the session is still usable
        sid = self.sid_of(r)
        side = json.loads((self.sidecar_dir() / f"{sid}.pier.json").read_text())
        self.assertEqual(side["timeout_ms"], 1000)
        r = self.pier("send", sid, "continue")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[resumed", r.stdout)

    def test_timeout_kill_does_not_wait_out_the_grace(self):
        # Pi is pier's unreaped child: once SIGTERM killed it, it is a zombie and
        # os.kill(pid, 0) still says alive, so the kill must not wait the full 5 s.
        self.slow_agent(1000)
        start = time.monotonic()
        r = self.pier("run", "slow", "x", env={"MOCK_PI_SLEEP": "30"})
        elapsed = time.monotonic() - start
        self.assertEqual(r.returncode, 124, r.stderr)
        self.assertLess(elapsed, 3.0)  # before: ~1 s + the 5 s grace

    def test_timeout_is_stored_and_replayed_by_send(self):
        self.write_agent(".pier/agents/t.md", "---\nname: t\ntimeoutMs: 1000\n---\nT\n")
        sid = self.sid_of(self.pier("run", "t", "x"))
        side = json.loads((self.sidecar_dir() / f"{sid}.pier.json").read_text())
        self.assertEqual(side["timeout_ms"], 1000)
        r = self.pier("send", sid, "slow again", env={"MOCK_PI_SLEEP": "30"})
        self.assertEqual(r.returncode, 124, r.stderr)
        self.assertIn("timed out after 0.02m", r.stderr)

    def test_default_timeout_is_twenty_minutes(self):
        sid = self.sid_of(self.pier("run", "reviewer", "x"))
        side = json.loads((self.sidecar_dir() / f"{sid}.pier.json").read_text())
        self.assertEqual(side["timeout_ms"], pi_runtime.DEFAULT_TIMEOUT_MS)
        self.assertEqual(pi_runtime.DEFAULT_TIMEOUT_MS, 20 * 60 * 1000)

    def test_sigterm_takes_the_grandchild_too(self):
        kid_pid = self.tmp / "kid.pid"
        p = self.spawn_run("run", "reviewer", "x",
                           env={"MOCK_PI_SLEEP": "30", "MOCK_PI_CHILD": "1",
                                "MOCK_PI_CHILD_PID": str(kid_pid)})
        kid = self.child_pid(kid_pid)
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(timeout=15), 128 + signal.SIGTERM)
        self.assert_gone(kid)

    def test_sigterm_still_ends_with_the_failed_summary(self):
        p = self.spawn_run("run", "reviewer", "x", env={"MOCK_PI_SLEEP": "30"})
        self.wait_for_running_pid()
        p.send_signal(signal.SIGTERM)
        out, err = p.communicate(timeout=20)
        self.assertEqual(p.returncode, 128 + signal.SIGTERM, err)
        self.assertEqual(out, "")
        lines = err.splitlines()
        ref = lines[0].split()[1]
        self.assertEqual(lines[0], f"pier: {ref} started (reviewer)")
        self.assertEqual(lines[-2:], [f"pier: {ref} failed (reviewer)",
                                      "pier: error: interrupted by SIGTERM"])

    def test_validate_rejects_a_bad_timeout(self):
        p = self.tmp / "bad-timeout.md"
        for raw in ("0", "-5", "soon", "1.5"):
            p.write_text(f"---\nname: x\ndescription: d\ntimeoutMs: {raw}\n---\nROLE\n")
            r = self.pier("agents", "validate", str(p))
            self.assertEqual(r.returncode, 1, raw)
            self.assertIn("timeoutMs", r.stdout)
        p.write_text("---\nname: x\ndescription: d\ntimeoutMs: 1500\n---\nROLE\n")
        r = self.pier("agents", "validate", str(p))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("ignored", r.stdout)

    def test_global_cap_makes_the_second_run_wait(self):
        p = self.spawn_run("run", "reviewer", "first",
                           env={"MOCK_PI_SLEEP": "3", "PIER_MAX_RUNNING": "1"})
        self.wait_for_running_pid()  # the slow run holds the only slot
        r = self.pier("run", "reviewer", "second", env={"PIER_MAX_RUNNING": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        ref = self.ref_of(r)
        lines = r.stderr.splitlines()
        self.assertEqual(lines[0], f"pier: {ref} started (reviewer)")
        self.assertRegex(lines[1], rf"^pier: {ref} waiting for a slot \(1 running\)$")
        self.assertEqual(lines[-1], f"pier: {ref} ok (reviewer)")
        self.assertEqual(r.stdout, "mock-ok: second\n")
        self.assertEqual(p.wait(timeout=20), 0)
        p.stdout.close()
        p.stderr.close()

    def test_per_session_cap_keys_on_claude_code_session_id(self):
        env = {"MOCK_PI_SLEEP": "3", "CLAUDE_CODE_SESSION_ID": "caller-a",
               "PIER_MAX_RUNNING_PER_SESSION": "1"}
        p = self.spawn_run("run", "reviewer", "first", env=env)
        self.wait_for_running_pid()
        r = self.pier("run", "reviewer", "second", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("waiting for a slot", r.stderr)
        # another caller session has its own slots: no wait
        r = self.pier("run", "reviewer", "third",
                      env={"CLAUDE_CODE_SESSION_ID": "caller-b",
                           "PIER_MAX_RUNNING_PER_SESSION": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("waiting for a slot", r.stderr)
        self.assertEqual(p.wait(timeout=20), 0)
        p.stdout.close()
        p.stderr.close()

    def test_global_cap_applies_without_a_caller_session(self):
        env = {"MOCK_PI_SLEEP": "2", "PIER_MAX_RUNNING_PER_SESSION": "1"}
        p = self.spawn_run("run", "reviewer", "first", env=env)
        self.wait_for_running_pid()
        # no CLAUDE_CODE_SESSION_ID: the per-session cap cannot apply
        r = self.pier("run", "reviewer", "second", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("waiting for a slot", r.stderr)
        self.assertEqual(p.wait(timeout=20), 0)
        p.stdout.close()
        p.stderr.close()

    def test_bad_cap_env_is_rejected_before_pi_runs(self):
        for key, raw in (("PIER_MAX_RUNNING", "0"), ("PIER_MAX_RUNNING", "lots"),
                         ("PIER_MAX_RUNNING_PER_SESSION", "-1")):
            r = self.pier("run", "reviewer", "x", env={key: raw})
            self.assertEqual(r.returncode, 2, raw)
            self.assertIn(key, r.stderr)
            r = self.pier("send", "deadbeef", "x", env={key: raw})
            self.assertEqual(r.returncode, 2, raw)
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.sidecar_dir().exists())

    def test_dry_run_takes_no_slot_and_sets_no_timeout(self):
        self.slow_agent(1000)
        a = self.dry("run", "slow", "x")
        self.assertEqual(a[0], str(MOCK))
        self.assertFalse(self.state().exists())  # no lock files, no sidecar

    def test_waiting_for_a_slot_counts_toward_the_timeout(self):
        self.slow_agent(1000)
        p = self.spawn_run("run", "reviewer", "first",
                           env={"MOCK_PI_SLEEP": "5", "PIER_MAX_RUNNING": "1"})
        self.wait_for_running_pid()
        r = self.pier("run", "slow", "queued", env={"PIER_MAX_RUNNING": "1"})
        self.assertEqual(r.returncode, 124, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("waiting for a slot", r.stderr)
        # Pi never started, so there is no Pi session for a send hint to reach
        self.assertEqual(r.stderr.splitlines()[-1],
                         "pier: error: timed out after 0.02m waiting for a slot")
        self.assertEqual(p.wait(timeout=20), 0)
        p.stdout.close()
        p.stderr.close()

    def test_a_queued_run_reads_as_running(self):
        busy = self.spawn_run("run", "reviewer", "first",
                              env={"MOCK_PI_SLEEP": "6", "PIER_MAX_RUNNING": "1"})
        self.wait_for_running_pid()
        queued = self.spawn_run("run", "reviewer", "second",
                                env={"MOCK_PI_SLEEP": "1", "PIER_MAX_RUNNING": "1"})
        first = queued.stderr.readline()
        self.assertIn("waiting for a slot", queued.stderr.readline())  # it is queued now
        ref = first.split()[1]
        r = self.pier("ps", ref)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("running", r.stdout)  # not `failed` while it queues
        r = self.pier("send", ref, "x")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("is running", r.stderr)
        self.assertEqual(queued.wait(timeout=30), 0)
        self.assertEqual(busy.wait(timeout=30), 0)


class SlotLockTests(Base):
    """Run slot flocks, in-process: release order and flock error handling."""

    def caller_session(self, sid="caller-a"):
        prev = os.environ.get("CLAUDE_CODE_SESSION_ID")
        os.environ["CLAUDE_CODE_SESSION_ID"] = sid

        def restore():
            if prev is None:
                os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            else:
                os.environ["CLAUDE_CODE_SESSION_ID"] = prev
        self.addCleanup(restore)

    def test_slots_are_released_in_reverse_order(self):
        # session slot first in, last out: otherwise a waiter takes the session
        # slot while the global one is still held and waits (prints) twice
        self.caller_session()
        taken = []
        closed = []
        real_try, real_os = pi_state._try_slot, pi_state.os

        def spy_try(files):
            fd = real_try(files)
            if fd is not None:
                taken.append(fd)
            return fd

        class Os:
            def __getattr__(self, name):
                return getattr(real_os, name)

            def close(self, fd):
                closed.append(fd)
                real_os.close(fd)

        pi_state._try_slot, pi_state.os = spy_try, Os()
        try:
            with pi_state.run_slots(self.state(), "abc12345", time.monotonic() + 30):
                pass
        finally:
            pi_state._try_slot, pi_state.os = real_try, real_os
        self.assertEqual(len(taken), 2, "session slot then global slot")
        self.assertEqual(closed, taken[::-1])

    def test_a_held_slot_reads_as_taken(self):
        home = self.state()
        files = pi_state.slot_files(home, "global", 1)
        with pi_state.run_slots(home, "abc12345", time.monotonic() + 30):
            self.assertIsNone(pi_state._try_slot(files))

    def test_a_flock_error_is_not_a_taken_slot(self):
        files = pi_state.slot_files(self.state(), "global", 1)
        real_fcntl = pi_state.fcntl

        class Fcntl:
            def __getattr__(self, name):
                return getattr(real_fcntl, name)

            def flock(self, fd, op):
                raise OSError(errno.ENOLCK, "No locks available")

        pi_state.fcntl = Fcntl()
        try:
            with self.assertRaises(pi_state.PierError) as ctx:
                pi_state._try_slot(files)
        finally:
            pi_state.fcntl = real_fcntl
        self.assertIn(str(files[0]), str(ctx.exception))


class WorktreeTests(Base):
    def git(self, *args, cwd=None):
        r = subprocess.run(["git", *args], cwd=str(cwd or self.proj), capture_output=True,
                           text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def setUp(self):
        super().setUp()
        self.git("init", "-q")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        (self.proj / "f.txt").write_text("base\n", encoding="utf-8")
        self.git("add", "f.txt")
        self.git("commit", "-qm", "base")

    def run_with_worktree_mutation(self, mutate):
        """Start a slow --worktree run in the background, mutate its worktree
        while Pi runs, and return (worktree path, run stderr)."""
        slow = subprocess.Popen(
            [sys.executable, str(PIER), "run", "reviewer", "x", "--worktree"],
            cwd=str(self.proj), env=self.full_env({"MOCK_PI_SLEEP": "3"}),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            wt = None
            wtdir = self.state() / "worktrees"
            for _ in range(100):
                if wtdir.is_dir() and any(wtdir.iterdir()):
                    wt = next(iter(wtdir.iterdir()))
                    break
                time.sleep(0.05)
            self.assertIsNotNone(wt, "worktree never appeared")
            for _ in range(100):  # wait for the checkout to finish before mutating
                if (wt / "f.txt").is_file() and \
                        (wt / "f.txt").read_text(encoding="utf-8") == "base\n":
                    break
                time.sleep(0.05)
            mutate(wt)
            for _ in range(200):
                if slow.poll() is not None:
                    break
                time.sleep(0.1)
            stderr = slow.stderr.read()
            self.assertEqual(slow.wait(timeout=10), 0, stderr)
            return wt, stderr
        finally:
            if slow.poll() is None:
                slow.terminate()
                slow.wait(timeout=10)
            slow.stdout.close()
            slow.stderr.close()

    def test_clean_worktree_is_auto_removed(self):
        r = self.pier("run", "reviewer", "hello", "--worktree")
        self.assertEqual(r.returncode, 0, r.stderr)
        ref = self.ref_of(r)
        wt = self.state() / "worktrees" / ref
        side = json.loads((self.state() / "sessions" / f"{self.sid_of(r)}.pier.json").read_text())
        self.assertEqual(Path(side["worktree"]).resolve(), wt.resolve())
        self.assertFalse(wt.exists())  # clean + HEAD unmoved -> removed
        self.assertNotIn("kept worktree", r.stderr)

    def test_dirty_worktree_is_kept_with_path_on_stderr(self):
        wt, stderr = self.run_with_worktree_mutation(
            lambda w: (w / "f.txt").write_text("edited\n", encoding="utf-8"))
        self.assertIn("kept worktree", stderr)
        self.assertIn(str(wt), stderr)
        self.assertTrue(wt.is_dir())
        # cleanup runs before the summary, so the last stderr line is the summary
        self.assertRegex(stderr.splitlines()[-1], r"^pier: \w+ ok \(reviewer\)$")
        # a worktree session still belongs to this project in `ps`
        self.assertIn(stderr.split()[1], self.pier("ps").stdout)
        # pier does not touch the caller's checkout
        self.assertEqual((self.proj / "f.txt").read_text(), "base\n")

    def test_send_runs_in_the_kept_worktree(self):
        wt, stderr = self.run_with_worktree_mutation(
            lambda w: (w / "f.txt").write_text("edited\n", encoding="utf-8"))
        ref = stderr.split()[1]
        cwd_rec = self.tmp / "pi-cwd"
        r = self.pier("send", ref, "continue", env={"MOCK_PI_RECORD_CWD": str(cwd_rec)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(Path(cwd_rec.read_text(encoding="utf-8")).resolve(), wt.resolve())

    def test_send_after_the_worktree_is_removed_runs_in_the_project(self):
        r = self.pier("run", "reviewer", "hello", "--worktree")
        self.assertEqual(r.returncode, 0, r.stderr)
        sid = self.sid_of(r)
        side = json.loads((self.state() / "sessions" / f"{sid}.pier.json").read_text())
        self.assertEqual(Path(side["cwd"]).resolve(), self.proj.resolve())  # restored
        cwd_rec = self.tmp / "pi-cwd"
        r = self.pier("send", sid, "continue", env={"MOCK_PI_RECORD_CWD": str(cwd_rec)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(Path(cwd_rec.read_text(encoding="utf-8")).resolve(), self.proj.resolve())

    def test_send_after_a_kept_worktree_is_deleted_runs_in_the_project(self):
        wt, stderr = self.run_with_worktree_mutation(
            lambda w: (w / "f.txt").write_text("edited\n", encoding="utf-8"))
        self.git("worktree", "remove", "--force", str(wt))
        cwd_rec = self.tmp / "pi-cwd"
        r = self.pier("send", stderr.split()[1], "continue", env={"MOCK_PI_RECORD_CWD": str(cwd_rec)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(Path(cwd_rec.read_text(encoding="utf-8")).resolve(), self.proj.resolve())

    def test_sigterm_removes_a_clean_worktree(self):
        slow = subprocess.Popen([sys.executable, str(PIER), "run", "reviewer", "x", "--worktree"],
                                cwd=str(self.proj), env=self.full_env({"MOCK_PI_SLEEP": "30"}),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        wt = None
        try:
            wtdir = self.state() / "worktrees"
            for _ in range(200):
                if wtdir.is_dir() and any(wtdir.iterdir()):
                    wt = next(wtdir.iterdir())
                    break
                time.sleep(0.05)
            self.assertIsNotNone(wt, "worktree never appeared")
            self.wait_for_running_pid()  # Pi is up, so the run's finally will fire
            slow.send_signal(signal.SIGTERM)
            self.assertEqual(slow.wait(timeout=10), 128 + signal.SIGTERM)
        finally:
            if slow.poll() is None:
                slow.terminate()
                slow.wait(timeout=10)
        stderr = slow.stderr.read()
        slow.stdout.close()
        slow.stderr.close()
        self.assertFalse(wt.exists(), stderr)  # clean snapshot removed on the way out
        self.assertNotIn("kept worktree", stderr)

    def test_head_moved_worktree_is_kept(self):
        # a commit inside the worktree counts as a change even with clean status
        wt, stderr = self.run_with_worktree_mutation(
            lambda w: self.git("commit", "-qm", "agent work", "--allow-empty", cwd=w))
        self.assertIn("kept worktree", stderr)
        self.assertTrue(wt.is_dir())

    def test_worktree_in_non_repo_errors(self):
        bare = self.tmp / "not a repo"
        bare.mkdir()
        r = self.pier("run", "reviewer", "x", "--worktree", cwd=bare)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a git repository", r.stderr)
        self.assertFalse((self.state() / "sessions").exists())  # no session left behind

    def test_worktree_in_repo_without_commits_errors(self):
        empty = self.tmp / "empty repo"
        empty.mkdir()
        self.git("init", "-q", cwd=empty)
        r = self.pier("run", "reviewer", "x", "--worktree", cwd=empty)
        self.assertEqual(r.returncode, 2)
        self.assertIn("no commits to snapshot", r.stderr)

    def test_dry_run_with_worktree_shows_the_worktree_cwd(self):
        r = self.pier("run", "reviewer", "x", "--worktree", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(str(self.state() / "worktrees" / "<ref>"), r.stdout)
        self.assertIn("detached worktree of", r.stderr)


class StorageTests(Base):
    def test_state_only_under_pier_home(self):
        other = self.tmp / "other"
        other.mkdir()
        sid = self.sid_of(self.pier("run", "reviewer", "hello", cwd=other))
        self.assertFalse((self.proj / ".pier").exists())
        self.assertFalse((other / ".pier").exists())
        files = sorted(str(p.relative_to(self.state())) for p in self.state().rglob("*")
                       if p.is_file() and not str(p.relative_to(self.state())).startswith("locks/"))
        self.assertEqual(len(files), 2, files)
        self.assertIn(f"sessions/{sid}.pier.json", files)
        pi_file = [f for f in files if f.endswith(".jsonl")][0]
        self.assertTrue(pi_file.startswith("sessions/") and pi_file.endswith(f"_{sid}.jsonl"))
        # run slots are empty files under locks/, kept for the next run's flock
        for p in (self.state() / "locks").rglob("*.lock"):
            self.assertEqual(p.read_text(encoding="utf-8"), "")
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600, p)

    def test_pier_home_override(self):
        alt = self.tmp / "alt"
        sid = self.sid_of(self.pier("run", "reviewer", "x", env={"PIER_HOME": str(alt)}), home=alt)
        self.assertTrue((alt / "sessions" / f"{sid}.pier.json").exists())
        self.assertFalse(self.state().exists())


class FormatTests(unittest.TestCase):
    def test_fmt_tokens_rounds(self):
        self.assertEqual(pi_state.fmt_tokens(999), "999")
        self.assertEqual(pi_state.fmt_tokens(1_801), "1.8k")
        self.assertEqual(pi_state.fmt_tokens(12_345), "12k")
        self.assertEqual(pi_state.fmt_tokens(1_234_567), "1.2M")


class ToonEncodeTests(unittest.TestCase):
    """TOON 4.4 encoder conformance vs SPEC + optional official CLI round-trip."""

    OFFICIAL_CLI = Path("/workspace/toon-validator/node_modules/@toon-format/cli/bin/toon.mjs")

    def setUp(self):
        self.enc = toon.encode_toon

    def _official_available(self) -> bool:
        return self.OFFICIAL_CLI.is_file()

    def _official_encode(self, obj):
        p = subprocess.run(
            ["node", str(self.OFFICIAL_CLI), "-e"],
            input=json.dumps(obj, ensure_ascii=False),
            text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.rstrip("\n")

    def _official_decode(self, toon_text: str):
        p = subprocess.run(
            ["node", str(self.OFFICIAL_CLI), "-d", "--strict"],
            input=toon_text if toon_text.endswith("\n") else toon_text + "\n",
            text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(p.returncode, 0, f"decode failed:\n{toon_text}\n{p.stderr}")
        return json.loads(p.stdout)

    def test_primitives_and_quoting(self):
        enc = self.enc
        self.assertEqual(enc(None), "null")
        self.assertEqual(enc(True), "true")
        self.assertEqual(enc(False), "false")
        self.assertEqual(enc(42), "42")
        self.assertEqual(enc("hi there"), "hi there")
        self.assertEqual(enc(" leading"), '" leading"')
        self.assertEqual(enc("trailing "), '"trailing "')
        self.assertEqual(enc(""), '""')
        self.assertEqual(enc({}), "")
        self.assertEqual(enc([]), "[]")
        # Spec §7.1 / §7.2: newlines are quoted escapes, not YAML block scalars
        self.assertEqual(enc({"msg": "a\nb"}), 'msg: "a\\nb"')
        self.assertEqual(enc({"s": 'say "hi"'}), 's: "say \\"hi\\""')
        self.assertEqual(enc({"bs": "a\\b"}), 'bs: "a\\\\b"')
        self.assertEqual(enc({"p": "a,b"}), 'p: "a,b"')
        self.assertEqual(enc({"numlike": "42"}), 'numlike: "42"')
        self.assertEqual(enc({"uni": "Hello 世界"}), "uni: Hello 世界")

    def test_empty_array_is_bracket_literal_not_header(self):
        # Spec §9.1: encoders MUST emit key: [] ; MUST NOT emit legacy key[0]:
        self.assertEqual(self.enc({"items": []}), "items: []")
        self.assertNotIn("[0]", self.enc({"items": []}))

    def test_objects_tables_lists(self):
        enc = self.enc
        self.assertEqual(enc({"a": 1, "b": "x y"}), "a: 1\nb: x y")
        self.assertEqual(enc({"items": []}), "items: []")
        self.assertEqual(enc([{"name": "a", "description": "d"}, {"name": "b", "description": "e"}]),
                         "[2]{name,description}:\n  a,d\n  b,e")
        self.assertEqual(enc({"x": [1, "a b", True]}), "x[3]: 1,a b,true")
        self.assertFalse(enc({"a": 1}).endswith("\n"))

    def test_nested_uniform_and_keyed_tabular(self):
        orders = {"orders": [
            {"id": 1, "customer": {"name": "Ada", "country": "DK"}, "total": 99},
            {"id": 2, "customer": {"name": "Bob", "country": "UK"}, "total": 149},
        ]}
        self.assertEqual(
            self.enc(orders),
            "orders[2]{id,customer{name,country},total}:\n  1,Ada,DK,99\n  2,Bob,UK,149",
        )
        users = {"users": {"alice": {"age": 30, "city": "Berlin"}, "bob": {"age": 25, "city": "Oslo"}}}
        self.assertEqual(
            self.enc(users),
            "users[2:]{age,city}:\n  alice: 30,Berlin\n  bob: 25,Oslo",
        )

    def test_list_item_nested_indent(self):
        obj = {"items": [1, {"a": 1, "b": {"c": 2}}, "text"]}
        expected = "items[3]:\n  - 1\n  - a: 1\n    b:\n      c: 2\n  - text"
        self.assertEqual(self.enc(obj), expected)
        deep = {"items": [{"users": [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Bob"}], "status": "active"}]}
        self.assertEqual(
            self.enc(deep),
            "items[1]:\n  - users[2]{id,name}:\n      1,Ada\n      2,Bob\n    status: active",
        )

    def test_matches_official_encode_when_available(self):
        if not self._official_available():
            self.skipTest("official @toon-format/cli not installed")
        cases = [
            {"tags": []},
            {"msg": "a\nb"},
            {"s": 'say "hi"', "p": "a,b", "bs": "a\\b", "numlike": "42", "uni": "Hello 世界 👋"},
            {"items": [1, {"a": 1, "b": {"c": 2}}, "text"]},
            {"items": [{"id": 1, "name": "First"}, {"id": 2, "name": "Second", "extra": True}]},
            {"items": [{"sku": "A1", "qty": 2}, {"sku": "B2", "qty": 1}]},
            {"orders": [
                {"id": 1, "customer": {"name": "Ada", "country": "DK"}, "total": 99},
                {"id": 2, "customer": {"name": "Bob", "country": "UK"}, "total": 149},
            ]},
            {"users": {"alice": {"age": 30, "city": "Berlin"}, "bob": {"age": 25, "city": "Oslo"}}},
            {"pairs": [[1, 2], [3, 4]]},
            {"x": {}},
            {"i": 1, "f": 1.5, "b": True, "n": None, "s": "hi"},
        ]
        for obj in cases:
            with self.subTest(obj=obj):
                self.assertEqual(self.enc(obj), self._official_encode(obj))

    def test_official_decode_roundtrip_when_available(self):
        """encode(ours) -> official decode -> same JSON model (skip if CLI absent)."""
        if not self._official_available():
            self.skipTest("official @toon-format/cli not installed")
        cases = [
            None, True, False, 0, 42, 1.5, "hi", "",
            "a,b", 'say "hi"', "a\\b", "a\nb", "42", "true", "Hello 世界",
            [], {},
            {"tags": []},
            {"msg": "line1\nline2"},
            {"s": 'q "x"', "p": "a,b", "bs": "a\\b", "numlike": "42", "uni": "世界"},
            {"items": [1, {"a": 1, "b": {"c": 2}}, "text"]},
            {"items": [{"id": 1, "name": "First"}, {"id": 2, "name": "Second", "extra": True}]},
            {"items": [{"sku": "A1", "qty": 2}, {"sku": "B2", "qty": 1}]},
            {"orders": [
                {"id": 1, "customer": {"name": "Ada", "country": "DK"}, "total": 99},
                {"id": 2, "customer": {"name": "Bob", "country": "UK"}, "total": 149},
            ]},
            {"users": {"alice": {"age": 30, "city": "Berlin"}, "bob": {"age": 25, "city": "Oslo"}}},
            {"pairs": [[1, 2], [3, 4]]},
            {"items": [{"users": [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Bob"}], "status": "active"}]},
        ]
        for obj in cases:
            with self.subTest(obj=obj):
                toon = self.enc(obj)
                got = self._official_decode(toon)
                self.assertEqual(got, obj)


class SystemPromptAndResourcesTests(Base):
    def test_system_prompt_mode_append(self):
        self.write_agent(".pier/agents/a.md",
                         "---\nname: a\nsystemPromptMode: append\n---\nAPPEND ME\n")
        a = self.dry("run", "a", "t")
        self.assertNotIn("--system-prompt", a)
        self.assertEqual(flag(a, "--append-system-prompt"), "APPEND ME")

    def test_report_contract_appended_after_role(self):
        # every run carries the ~60-token report contract, after the agent's own prompt
        self.write_agent(".pier/agents/r.md", "---\nname: r\n---\nREPLACE ME\n")
        a = self.dry("run", "r", "t")
        appends = [v for f, v in zip(a, a[1:]) if f == "--append-system-prompt"]
        self.assertEqual(appends, [pi_runtime.REPORT_CONTRACT])
        self.assertLess(a.index("--system-prompt"), a.index("--append-system-prompt"))
        self.write_agent(".pier/agents/a.md",
                         "---\nname: a\nsystemPromptMode: append\n---\nAPPEND ME\n")
        a = self.dry("run", "a", "t")
        self.assertEqual(a.count("--append-system-prompt"), 2)  # role append first, contract after
        self.assertEqual(a[a.index("--append-system-prompt", a.index("--append-system-prompt") + 1) + 1],
                         pi_runtime.REPORT_CONTRACT)

    def test_empty_skills_and_inherit_false(self):
        self.write_agent(".pier/agents/s.md", "---\nname: s\nskills:\n---\nB\n")
        self.assertIn("--no-skills", self.dry("run", "s", "t"))
        self.write_agent(".pier/agents/s2.md", "---\nname: s2\ninheritSkills: false\n---\nB\n")
        self.assertIn("--no-skills", self.dry("run", "s2", "t"))

    def test_named_skill_and_skill_path(self):
        skill_dir = self.proj / ".pi" / "skills" / "demo-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: demo-skill\n---\nDo demos.\n")
        priv = self.proj / ".pier" / "agents" / "priv"
        priv.mkdir(parents=True)
        (priv / "local-skill.md").write_text("---\nname: local\n---\nLocal.\n")
        self.write_agent(".pier/agents/s.md",
                         "---\nname: s\nskills: demo-skill\nskillPath: priv/local-skill.md\n---\nB\n")
        a = self.dry("run", "s", "t")
        skills = [a[i + 1] for i, x in enumerate(a) if x == "--skill"]
        self.assertEqual(len(skills), 2)
        self.assertTrue(any(s.endswith("demo-skill/SKILL.md") for s in skills))
        self.assertTrue(any(s.endswith("priv/local-skill.md") for s in skills))
        # files in subdirs of an agents dir are not agents
        self.assertNotIn("local", self.pier("agents", "list").stdout)

    def test_package_declared_skills_are_not_searched(self):
        # only Pi's standard skill dirs; package/settings skills go through skillPath
        pkg = self.proj / "node_modules" / "p" / "skills" / "pkg-skill"
        pkg.mkdir(parents=True)
        (pkg / "SKILL.md").write_text("---\nname: pkg-skill\n---\nX\n")
        (self.proj / "node_modules" / "p" / "package.json").write_text('{"pi": {"skills": "skills"}}')
        self.write_agent(".pier/agents/s.md", "---\nname: s\nskills: pkg-skill\n---\nB\n")
        r = self.pier("run", "s", "t", "--dry-run")
        self.assertEqual(r.returncode, 2)
        self.assertIn("use skillPath", r.stderr)

    def test_unresolved_skill_errors(self):
        self.write_agent(".pier/agents/s.md", "---\nname: s\nskills: missing-skill\n---\nB\n")
        r = self.pier("run", "s", "t", "--dry-run")
        self.assertEqual(r.returncode, 2)
        self.assertIn("missing-skill", r.stderr)

    def test_skill_singular_is_ignored(self):
        self.write_agent(".pier/agents/s.md", "---\nname: s\nskill: demo-skill\n---\nB\n")
        r = self.pier("run", "s", "t", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ignored frontmatter", r.stderr)
        self.assertNotIn("--skill", shlex.split(r.stdout))

    def test_extensions_allowlist_and_empty(self):
        ext = self.proj / ".pier" / "agents" / "my-ext.ts"
        ext.parent.mkdir(parents=True, exist_ok=True)
        ext.write_text("// ext\n")
        self.write_agent(".pier/agents/e.md",
                         "---\nname: e\nextensions: my-ext.ts\nsubagentOnlyExtensions: builtin:foo\n---\nB\n")
        a = self.dry("run", "e", "t")
        self.assertIn("--no-extensions", a)
        exts = [a[i + 1] for i, x in enumerate(a) if x == "--extension"]
        self.assertEqual(len(exts), 2)
        self.assertTrue(any(e.endswith("my-ext.ts") for e in exts))
        self.assertIn("builtin:foo", exts)
        for empty in ("", " []"):
            self.write_agent(".pier/agents/e2.md", f"---\nname: e2\nextensions:{empty}\n---\nB\n")
            a = self.dry("run", "e2", "t")
            self.assertIn("--no-extensions", a)
            self.assertNotIn("--extension", a)
        self.write_agent(".pier/agents/e3.md", "---\nname: e3\ntools: [read, 'ls']\n---\nB\n")
        a = self.dry("run", "e3", "t")
        self.assertEqual(a[a.index("--tools") + 1], "read,ls")

    def test_inherit_project_context_false(self):
        self.write_agent(".pier/agents/c.md",
                         "---\nname: c\ninheritProjectContext: false\ninheritGlobalContext: false\n---\nB\n")
        r = self.pier("run", "c", "t", "--dry-run")
        self.assertIn("--no-context-files", shlex.split(r.stdout))
        self.assertIn("inheritGlobalContext", r.stderr)


@unittest.skipUnless(os.environ.get("PIER_TEST_REAL_PI"), "set PIER_TEST_REAL_PI=/path/to/pi")
class RealPiArgvTests(Base):
    """Real Pi parses pier's argv; an empty Pi config dir + no key env -> fails only on
    the missing API key, so no model is called. HOME stays real for node shims."""

    def real_run(self, *argv: str) -> subprocess.CompletedProcess:
        clean = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
                 "PI_BIN": os.environ["PIER_TEST_REAL_PI"],
                 "PI_CODING_AGENT_DIR": str(self.tmp / "pi-agent"), "PIER_HOME": str(self.tmp / "pier-home")}
        return subprocess.run([sys.executable, str(PIER), "run", *argv],
                              cwd=str(self.proj), env=clean, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=60)

    def test_real_pi_accepts_argv(self):
        self.write_agent(".pier/agents/r.md", "---\nname: r\nmodel: anthropic/claude-sonnet-4-5:low\n"
                         "tools: read, grep\nexcludeTools: bash\n---\nRole \"q\" 中文\n")
        r = self.real_run("r", "task it's 中文")
        self.assertNotIn("Unknown option", r.stderr)
        self.assertIn("No API key", r.stderr)
        self.assertEqual(r.returncode, 1)

    def test_real_pi_accepts_bundled_agents(self):
        for f in sorted((SKILL / "agents").glob("*.md")):
            with self.subTest(agent=f.stem):
                r = self.real_run(f.stem, "t", "--model", "anthropic/claude-sonnet-4-5")
                self.assertIn("No API key", r.stderr)


if __name__ == "__main__":
    unittest.main()
