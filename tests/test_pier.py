#!/usr/bin/env python3
"""Tests for pier. Uses mock/mock_pi only; never calls a model.

Optional: PIER_TEST_REAL_PI=/path/to/pi runs an argv-parse check against a real
Pi binary with an empty HOME and --offline (no credentials -> no model call).
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIER = ROOT / "scripts" / "pier"
MOCK = ROOT / "mock" / "mock_pi"


def load_mod():
    loader = importlib.machinery.SourceFileLoader("pier_mod", str(PIER))
    spec = importlib.util.spec_from_loader("pier_mod", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pier_mod"] = mod
    loader.exec_module(mod)
    return mod


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
        for k in ("PIER_HOME", "PIER_AGENT_DIRS", "MOCK_PI_FAIL", "MOCK_PI_FAIL_ON",
                  "MOCK_PI_SLEEP", "MOCK_PI_SLEEP_ON", "MOCK_PI_REPLY"):
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
        args = list(args)
        if "--extra" in args:
            args.insert(args.index("--extra"), "--dry-run")
        else:
            args.append("--dry-run")
        r = self.pier(*args, **kw)
        self.assertEqual(r.returncode, 0, r.stderr)
        tokens = shlex.split(r.stdout)
        self.assertEqual(tokens[0], "cd")
        self.assertEqual(tokens[2], "&&")
        return tokens[3:]

    def argv(self):
        return json.loads(self.rec.read_text())

    def calls(self):
        return [json.loads(p.read_text()) for p in sorted(self.rec_dir.glob("*.json"))] if self.rec_dir.exists() else []

    def state(self):
        return self.home / ".pier"

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
        self.mod = load_mod()

    def test_simple(self):
        fm, body = self.mod.parse_frontmatter("---\nname: explorer\ntools: read, grep\n---\n\nHello body\n")
        self.assertEqual(fm, {"name": "explorer", "tools": "read, grep"})
        self.assertEqual(body, "Hello body")

    def test_blocks_and_lists(self):
        fm, _ = self.mod.parse_frontmatter("---\nname: x\ndescription: |\n  one\n  two\ntools:\n  - read\n  - grep\n---\nB\n")
        self.assertEqual(fm["description"], "one\ntwo")
        self.assertEqual(self.mod.parse_list(fm["tools"]), ["read", "grep"])
        self.assertEqual(self.mod.parse_list("- read  # c\n- 'grep'"), ["read", "grep"])

    def test_classify(self):
        c = self.mod.classify_fields({"name": "a", "model": "m", "description": "d", "async": "true",
                                      "systemPromptMode": "replace", "launcher": "x", "weird": "1"})
        self.assertEqual(c["mapped"], ["name", "model", "systemPromptMode"])
        self.assertEqual(c["meta"], ["description"])
        self.assertEqual(c["ignored"], ["async", "launcher", "weird"])


class LastTurnTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_mod()
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
        t = self.mod.last_turn(p)
        self.assertEqual((t.reply, t.stop), ("a2 final", None))

    def test_unanswered_turn_has_no_reply(self):
        p = self.session({"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"},
                         {"role": "user", "content": "q2"})
        self.assertEqual(self.mod.last_turn(p).reply, "")

    def test_failed_stop_reason_beats_exit_zero_and_earlier_text(self):
        # JSON-mode Pi exits 0 on provider errors; the session entry is authoritative.
        t = self.mod.Turn("let me look", "error", "529 overloaded")
        out = self.mod.turn_outcome(t, 0, "")
        self.assertEqual(out.reply, "")
        self.assertIn("529 overloaded", out.error)
        self.assertEqual(out.code, 1)


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
        for n in ("explorer", "planner", "reviewer", "worker"):
            self.assertIn(n, r.stderr)
        self.assertEqual(self.calls(), [])

    def test_paths_command(self):
        out = self.pier("agents", "paths").stdout
        rows = [l.split(",")[0].strip() for l in out.splitlines() if l.startswith("  ")]
        self.assertEqual(rows, ["skill", "user", "project"])
        self.assertIn(str((self.state() / "agents").resolve()), out)

    def test_list_is_toon_table(self):
        r = self.pier("agents", "list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("[4]{name,description}:"), r.stdout)
        self.assertIn("\n  reviewer,", r.stdout)

    def test_show_prints_path_and_file(self):
        r = self.pier("agents", "show", "reviewer")
        self.assertEqual(r.stdout.splitlines()[0], str(ROOT / "agents" / "reviewer.md"))
        self.assertIn("You are a rigorous code reviewer.", r.stdout)


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
        self.assertEqual(flag(self.dry("run", "p", "go", "--thinking", "max"), "--thinking"), "max")

    def test_cli_overrides(self):
        a = self.dry("run", "reviewer", "x", "--model", "zai/glm", "--tools", "read")
        self.assertEqual(flag(a, "--tools"), "read")
        self.assertEqual(flag(a, "--model"), "zai/glm")
        self.assertIn("--no-tools", self.dry("run", "reviewer", "x", "--no-tools"))
        r = self.pier("run", "reviewer", "x", "--no-tools", "--tools", "read")
        self.assertEqual(r.returncode, 2)

    def test_non_native_tools_dropped_with_note(self):
        self.write_agent(".pier/agents/e.md", "---\nname: e\ntools: contact_supervisor, mcp:x/y\nlauncher: tmux\n---\nB\n")
        r = self.pier("run", "e", "go", "--dry-run")
        self.assertIn("--no-tools", shlex.split(r.stdout))
        self.assertIn("contact_supervisor", r.stderr)
        self.assertIn("launcher", r.stderr)
        r = self.pier("run", "e", "go")
        lines = r.stderr.splitlines()
        self.assertTrue(lines[1].startswith("pier: ") and " ok (e)" in lines[1], r.stderr)

    def test_prompt_is_task_and_body_is_system_prompt(self):
        self.write_agent(".pier/agents/c.md", "---\nname: c\n---\n\nROLE LINE\n\nSecond para.\n\n")
        a = self.dry("run", "c", "DO THE THING")
        self.assertEqual(a[-1], "DO THE THING")
        self.assertEqual(flag(a, "--system-prompt"), "ROLE LINE\n\nSecond para.")

    def test_quoting_unicode_roundtrip(self):
        task = "it's \"quoted\" $HOME `x` 中文 ✓"
        self.assertEqual(self.dry("run", "reviewer", task)[-1], task)

    def test_api_key_in_extra_rejected(self):
        r = self.pier("run", "reviewer", "x", "--extra", "--api-key", "SECRET")
        self.assertEqual(r.returncode, 2)
        self.assertIn("environment", r.stderr)
        self.assertNotIn("SECRET", r.stderr)
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

    def test_new_default_project_path(self):
        p = self.tmp / "def.md"
        p.write_text(self.GOOD)
        r = self.pier("agents", "new", "sql-reviewer", "--file", str(p))
        target = (self.proj / ".pier" / "agents" / "sql-reviewer.md").resolve()
        self.assertIn(f"written: {target}", r.stdout)
        self.assertTrue(target.exists())
        r = self.pier("agents", "new", "sql-reviewer", "--file", str(p))
        self.assertEqual(r.returncode, 2)  # no silent overwrite
        self.assertIn("--force", r.stderr)
        r = self.pier("agents", "new", "sql-reviewer", "--file", "-", "--user", input_text=self.GOOD)
        self.assertTrue((self.state() / "agents" / "sql-reviewer.md").exists(), r.stderr)

    def test_new_never_touches_pi_or_skill_dirs(self):
        p = self.tmp / "def.md"
        p.write_text(self.GOOD)
        before = sorted(x.name for x in (ROOT / "agents").iterdir())
        self.pier("agents", "new", "sql-reviewer", "--file", str(p))
        self.pier("agents", "new", "sql-reviewer", "--file", str(p), "--user")
        self.assertFalse((self.home / ".pi").exists())
        self.assertFalse((self.proj / ".pi").exists())
        self.assertFalse((self.proj / ".agents").exists())
        self.assertEqual(before, sorted(x.name for x in (ROOT / "agents").iterdir()))

    def test_new_rejects_name_mismatch(self):
        p = self.tmp / "def.md"
        p.write_text(self.GOOD)
        r = self.pier("agents", "new", "other", "--file", str(p))
        self.assertEqual(r.returncode, 1)
        self.assertFalse((self.proj / ".pier" / "agents" / "other.md").exists())


class SessionTests(Base):
    def test_run_prints_reply_and_session(self):
        r = self.pier("run", "reviewer", "hello")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "mock-ok: hello\n")
        sid = self.sid_of(r)
        side = json.loads((self.state() / "sessions" / f"{sid}.pier.json").read_text())
        self.assertEqual(side["cwd"], str(self.proj.resolve()))
        self.assertIn("--system-prompt", side["flags"])
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
            r = self.pier("result", self.sid_of(r))
            self.assertEqual(r.returncode, 1)

    def test_length_prints_partial_reply_and_fails(self):
        r = self.pier("run", "reviewer", "x", env={"MOCK_PI_STOP": "length", "MOCK_PI_REPLY": "PARTIAL"})
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "PARTIAL\n")
        self.assertIn("output token limit", r.stderr)

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
        r = self.pier("result", sid, "--max-chars", "0")
        self.assertEqual(r.stdout, f"full reply: {Path(path).resolve()}\n")  # 0 = path only (resolve: /var symlink)
        r = self.pier("result", sid, "--max-chars", "100000")
        self.assertEqual(r.stdout, reply + "\n")  # a large budget inlines it all

    def test_max_chars_env_default_and_failure_keeps_path_last(self):
        reply = "B" * 30_000
        r = self.pier("run", "reviewer", "x", env={"MOCK_PI_REPLY": reply, "PIER_MAX_CHARS": "5000"})
        self.assertEqual(r.returncode, 0, r.stderr)
        sid = self.sid_of(r)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "B" * 2_000)
        self.assertIn(str(Path(self.state() / "out" / f"{sid}.md").resolve()), lines[-1])
        # a nonzero exit keeps the path as stdout's last line
        r = self.pier("send", sid, "again", "--max-chars", "10",
                      env={"MOCK_PI_REPLY": reply, "MOCK_PI_STOP": "length"})
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
                          f"pier: {ref} ok (reviewer) · 1k tok · $0.01"])
        r = self.pier("run", "reviewer", "x", env={"MOCK_PI_FAIL": "1"})
        ref = self.ref_of(r)
        line = r.stderr.splitlines()[1]
        self.assertTrue(line.startswith(f"pier: {ref} failed (reviewer)"), r.stderr)
        self.assertIn(" — ", line)  # the failure reason is appended

    def test_send_resolves_prefix_name_and_errors_on_ambiguity(self):
        sid = self.sid_of(self.pier("run", "reviewer", "first"))
        r = self.pier("send", sid[:12], "via prefix", cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[resumed", r.stdout)
        r = self.pier("send", "reviewer", "via name", cwd=self.tmp)  # --name handle
        self.assertEqual(r.returncode, 0, r.stderr)
        # same --name twice: newest wins (the later run's session)
        self.pier("run", "reviewer", "second", "--name", "dup")
        newest = self.sid_of(self.pier("run", "reviewer", "third", "--name", "dup"))
        r = self.pier("send", "dup", "newest", cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"[resumed {newest}", r.stdout)
        # an ambiguous prefix names its alternatives
        other = self.state() / "sessions" / f"{sid[:6]}aa{sid[6:].replace('-', '')}.pier.json"
        other.write_text(json.dumps({"cwd": str(self.tmp), "flags": [], "name": "other"}), encoding="utf-8")
        try:
            r = self.pier("send", sid[:6], "x")
            self.assertEqual(r.returncode, 2)
            self.assertIn("ambiguous", r.stderr)
            self.assertIn(sid[:6], r.stderr)  # the alternatives are named by their refs
        finally:
            other.unlink()

    def test_result_reads_last_turn_only(self):
        sid = self.sid_of(self.pier("run", "reviewer", "first"))
        self.assertEqual(self.pier("result", sid).stdout, "mock-ok: first\n")
        r = self.pier("send", sid, "second", env={"MOCK_PI_FAIL": "1"})
        self.assertEqual(r.returncode, 3)
        r = self.pier("result", sid)
        self.assertEqual(r.returncode, 1)  # never hands back the stale first reply
        self.assertIn("no reply after the last prompt", r.stderr)

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
        # pier does not touch the caller's checkout
        self.assertEqual((self.proj / "f.txt").read_text(), "base\n")

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


class StorageTests(Base):
    def test_state_only_under_pier_home(self):
        other = self.tmp / "other"
        other.mkdir()
        sid = self.sid_of(self.pier("run", "reviewer", "hello", "--cwd", str(self.proj), cwd=other))
        self.assertFalse((self.proj / ".pier").exists())
        self.assertFalse((other / ".pier").exists())
        files = sorted(str(p.relative_to(self.state())) for p in self.state().rglob("*") if p.is_file())
        self.assertEqual(len(files), 2, files)
        self.assertIn(f"sessions/{sid}.pier.json", files)
        pi_file = [f for f in files if f.endswith(".jsonl")][0]
        self.assertTrue(pi_file.startswith("sessions/--") and pi_file.endswith(f"_{sid}.jsonl"))

    def test_pier_home_override(self):
        alt = self.tmp / "alt"
        sid = self.sid_of(self.pier("run", "reviewer", "x", env={"PIER_HOME": str(alt)}), home=alt)
        self.assertTrue((alt / "sessions" / f"{sid}.pier.json").exists())
        self.assertFalse(self.state().exists())


class ToonEncodeTests(unittest.TestCase):
    """TOON 4.4 encoder conformance vs SPEC + optional official CLI round-trip."""

    OFFICIAL_CLI = Path("/workspace/toon-validator/node_modules/@toon-format/cli/bin/toon.mjs")

    def setUp(self):
        self.mod = load_mod()
        self.enc = self.mod.encode_toon

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
        a = self.dry("run", "reviewer", "t")
        appends = [v for f, v in zip(a, a[1:]) if f == "--append-system-prompt"]
        self.assertEqual(appends, [load_mod().REPORT_CONTRACT])
        self.assertLess(a.index("--system-prompt"), a.index("--append-system-prompt"))
        self.write_agent(".pier/agents/a.md",
                         "---\nname: a\nsystemPromptMode: append\n---\nAPPEND ME\n")
        a = self.dry("run", "a", "t")
        self.assertEqual(a.count("--append-system-prompt"), 2)  # role append first, contract after
        self.assertEqual(a[a.index("--append-system-prompt", a.index("--append-system-prompt") + 1) + 1],
                         load_mod().REPORT_CONTRACT)

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

    def test_unresolved_skill_errors(self):
        self.write_agent(".pier/agents/s.md", "---\nname: s\nskills: missing-skill\n---\nB\n")
        r = self.pier("run", "s", "t", "--dry-run")
        self.assertEqual(r.returncode, 2)
        self.assertIn("missing-skill", r.stderr)

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
        self.write_agent(".pier/agents/e2.md", "---\nname: e2\nextensions:\n---\nB\n")
        a = self.dry("run", "e2", "t")
        self.assertIn("--no-extensions", a)
        self.assertNotIn("--extension", a)

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

    def test_real_pi_accepts_argv(self):
        real = os.environ["PIER_TEST_REAL_PI"]
        self.write_agent(".pier/agents/r.md", "---\nname: r\nmodel: anthropic/claude-sonnet-4-5:low\n"
                         "tools: read, grep\nexcludeTools: bash\n---\nRole \"q\" 中文\n")
        clean = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), "PI_BIN": real,
                 "PI_CODING_AGENT_DIR": str(self.tmp / "pi-agent"), "PIER_HOME": str(self.tmp / "pier-home")}
        r = subprocess.run([sys.executable, str(PIER), "run", "r", "task it's 中文", "--extra", "--offline"],
                           cwd=str(self.proj), env=clean, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=60)
        self.assertNotIn("Unknown option", r.stderr)
        self.assertIn("No API key", r.stderr)
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
