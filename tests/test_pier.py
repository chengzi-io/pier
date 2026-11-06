#!/usr/bin/env python3
"""Tests for pier. Uses mock/mock_pi only; never calls a model.

Optional: PIER_TEST_REAL_PI=/path/to/pi runs an argv-parse check against a real
Pi binary with an empty HOME and --offline (no credentials -> no model call).
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIER = ROOT / "scripts" / "pier.py"
WRAPPER = ROOT / "scripts" / "pier"
MOCK = ROOT / "mock" / "mock_pi"


def load_mod():
    spec = importlib.util.spec_from_file_location("pier_mod", PIER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pier_mod"] = mod
    spec.loader.exec_module(mod)
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
            "MOCK_PI_RECORD_DIR": str(self.rec_dir), "PIER_QUIET": "",
        }
        for k in ("PIER_HOME", "PIER_AGENT_DIRS", "PIER_USER_AGENTS_DIR",
                  "MOCK_PI_FAIL", "MOCK_PI_FAIL_ON", "MOCK_PI_SLEEP", "MOCK_PI_SLEEP_ON", "MOCK_PI_REPLY"):
            self.env[k] = ""

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pier(self, *args, env=None, input_text=None, cwd=None, json_out=True):
        e = {k: v for k, v in os.environ.items()}
        e.update(self.env)
        e = {k: v for k, v in e.items() if v != ""}
        if env:
            e.update(env)
        argv = list(args)
        # Existing JSON parsers: inject --json unless caller opted out or already set.
        # Insert before --extra (REMAINDER) so it is not swallowed into Pi passthrough.
        if json_out and "--json" not in argv and "--help" not in argv and "--version" not in argv:
            if "--extra" in argv:
                i = argv.index("--extra")
                argv.insert(i, "--json")
            else:
                argv.append("--json")
        return subprocess.run([sys.executable, str(PIER), *argv], cwd=str(cwd or self.proj), env=e,
                              input=input_text, stdin=None if input_text is not None else subprocess.DEVNULL,
                              text=True, capture_output=True, timeout=60)

    def j(self, r):
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            self.fail(f"not JSON (rc={r.returncode}): {r.stdout!r} / {r.stderr!r}")

    def argv(self):
        return json.loads(self.rec.read_text())

    def calls(self):
        return [json.loads(p.read_text()) for p in sorted(self.rec_dir.glob("*.json"))] if self.rec_dir.exists() else []

    def write_agent(self, rel, text):
        p = self.proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p


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
        self.assertIn("async", c["meta"])
        self.assertEqual(c["ignored"], ["launcher", "weird"])

    def test_no_strict_or_allow_flags(self):
        src = PIER.read_text()
        self.assertNotIn("--strict-frontmatter", src)
        self.assertNotIn("allow-unsupported", src)


class DiscoveryTests(Base):
    def test_lookup_order_project_pier_agents_wins(self):
        self.write_agent(".pier/agents/reviewer.md", "---\nname: reviewer\ndescription: p\n---\nPROJECT BODY\n")
        (self.home / ".pier" / "agents").mkdir(parents=True)
        (self.home / ".pier" / "agents" / "reviewer.md").write_text("---\nname: reviewer\n---\nUSER BODY\n")
        d = self.j(self.pier("run", "reviewer", "t", "--dry-run"))
        a = d["argv"]
        self.assertEqual(a[a.index("--system-prompt") + 1], "PROJECT BODY")
        self.assertEqual(d["prompt"], "## Task\n\nt\n")
        shutil.rmtree(self.proj / ".pier" / "agents")
        d = self.j(self.pier("run", "reviewer", "t", "--dry-run"))
        a = d["argv"]
        self.assertEqual(a[a.index("--system-prompt") + 1], "USER BODY")

    def test_pi_dirs_never_read(self):
        self.write_agent(".pi/agents/scout.md", "---\nname: scout\n---\nSCOUT\n")
        self.write_agent(".agents/scout.md", "---\nname: scout\n---\nSCOUT2\n")
        r = self.pier("run", "scout", "t", "--dry-run")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown agent", r.stderr)
        # flag gone
        r = self.pier("run", "--help")
        self.assertNotIn("include-pi-subagents", r.stdout + r.stderr)

    def test_paths_command(self):
        d = self.j(self.pier("agents", "paths"))
        srcs = [l["source"] for l in d["layers"]]
        self.assertEqual(srcs[0], "skill")
        self.assertEqual(srcs[-2:], ["user", "project"])
        self.assertNotIn("pi-subagents", srcs)
        self.assertTrue(d["new_default"].endswith(os.path.join(".pier", "agents")))

    def test_list_name_description_only(self):
        rows = self.j(self.pier("agents", "list"))
        names = [a["name"] for a in rows]
        for n in ("explorer", "planner", "reviewer", "worker"):
            self.assertIn(n, names)
        for a in rows:
            self.assertEqual(set(a.keys()), {"name", "description"})
        # aliases no longer resolve
        r = self.pier("run", "pr-reviewer", "t", "--dry-run")
        self.assertEqual(r.returncode, 2)


class MappingTests(Base):
    def test_full_mapping(self):
        self.write_agent(".pier/agents/m.md",
                         "---\nname: m\nmodel: anthropic/claude-x\nthinking: high\ntools: read, bash\n"
                         "excludeTools: write\ndefaultProvider: openai\n---\nBODY\n")
        d = self.j(self.pier("run", "m", "go", "--dry-run"))
        a = d["argv"]
        self.assertEqual(a[0], str(MOCK))
        self.assertEqual(a[1:3], ["--mode", "json"])
        self.assertNotIn("--provider", a)  # model already has provider/
        self.assertEqual(a[a.index("--model") + 1], "anthropic/claude-x")
        self.assertEqual(a[a.index("--thinking") + 1], "high")
        self.assertEqual(a[a.index("--tools") + 1], "read,bash")
        self.assertEqual(a[a.index("--exclude-tools") + 1], "write")
        self.assertEqual(a[a.index("--name") + 1], "m")
        self.assertIn("--session-dir", a)
        self.assertIn("--session-id", a)
        self.assertEqual(a[-2], "--")
        self.assertFalse((self.proj / ".pier" / "jobs").exists() and any((self.proj / ".pier" / "jobs").iterdir()))
        self.assertFalse(self.rec.exists())  # dry-run never runs pi

    def test_default_provider_and_suffix(self):
        self.write_agent(".pier/agents/p.md", "---\nname: p\nmodel: gpt-x:low\nthinking: high\ndefaultProvider: openai\n---\nB\n")
        a = self.j(self.pier("run", "p", "go", "--dry-run"))["argv"]
        self.assertEqual(a[a.index("--provider") + 1], "openai")
        self.assertEqual(a[a.index("--model") + 1], "gpt-x:low")
        self.assertNotIn("--thinking", a)  # suffix wins over frontmatter thinking
        a = self.j(self.pier("run", "p", "go", "--dry-run", "--thinking", "max"))["argv"]
        self.assertEqual(a[a.index("--thinking") + 1], "max")  # explicit CLI always emitted

    def test_cli_overrides(self):
        a = self.j(self.pier("run", "reviewer", "x", "--dry-run", "--model", "zai/glm", "--tools", "read",
                             "--name", "N", "--mode", "print"))["argv"]
        self.assertEqual(a[a.index("--tools") + 1], "read")
        self.assertEqual(a[a.index("--name") + 1], "N")
        self.assertIn("--print", a)

    def test_empty_tools_no_tools(self):
        self.write_agent(".pier/agents/e.md", "---\nname: e\ntools: contact_supervisor, mcp:x/y\n---\nB\n")
        r = self.pier("run", "e", "go", "--dry-run")
        d = self.j(r)
        self.assertIn("--no-tools", d["argv"])
        self.assertIn("contact_supervisor", r.stderr)  # dropped, but not silently
        self.assertIn("no effect", r.stderr)
        self.assertTrue(any("contact_supervisor" in n for n in d["notes"]))

    def test_ignored_fields_simple_notice(self):
        self.write_agent(".pier/agents/x.md", "---\nname: x\nsystemPromptMode: replace\nasync: true\nlauncher: tmux\n---\nB\n")
        r = self.pier("run", "x", "go", "--dry-run")
        d = self.j(r)
        self.assertEqual(d["ignored_fields"], ["launcher"])
        self.assertIn("not mapped, no effect", r.stderr)
        self.assertIn("--system-prompt", d["argv"])
        self.assertEqual(d["argv"][d["argv"].index("--system-prompt") + 1], "B")
        self.assertNotIn("async", r.stderr)  # async is metadata, not mapped, no warning
        r = self.pier("run", "x", "go", "--dry-run", env={"PIER_QUIET": "1"})
        self.assertEqual(r.stderr, "")

    def test_exact_prompt_composition(self):
        self.write_agent(".pier/agents/c.md", "---\nname: c\n---\n\nROLE LINE\n\nSecond para.\n\n")
        (self.proj / "h.md").write_text("HANDOFF\n")
        d = self.j(self.pier("run", "c", "DO THE THING", "--dry-run"))
        self.assertEqual(d["prompt"], "## Task\n\nDO THE THING\n")
        self.assertEqual(d["argv"][d["argv"].index("--system-prompt") + 1], "ROLE LINE\n\nSecond para.")
        d = self.j(self.pier("run", "c", "T", "--handoff-file", "h.md", "--dry-run"))
        self.assertEqual(d["prompt"], "## Context handoff\n\nHANDOFF\n\n## Task\n\nT\n")
        d = self.j(self.pier("run", "c", "T", "--no-agent-body", "--dry-run"))
        self.assertEqual(d["prompt"], "## Task\n\nT\n")
        self.assertNotIn("--system-prompt", d["argv"])
        self.assertNotIn("--append-system-prompt", d["argv"])

    def test_resolved_show(self):
        d = self.j(self.pier("agents", "show", "reviewer", "--resolved"))
        fields = [m["field"] for m in d["mapping"]]
        for f in ("name", "tools", "excludeTools", "thinking", "(body)", "task", "effective"):
            self.assertIn(f, fields)
        self.assertIn("argv_shell", d)

    def test_argv_shell_quoting_unicode(self):
        task = "it's \"quoted\" $HOME `x` 中文 ✓"
        d = self.j(self.pier("run", "reviewer", task, "--dry-run", "--extra", "--api-key", "SECRET"))
        self.assertNotIn("SECRET", d["argv_shell"])
        self.assertIn("<redacted>", d["argv_shell"])
        import shlex
        parsed = shlex.split(d["argv_shell"])
        self.assertEqual(parsed[-1], d["prompt"])
        self.assertIn(task, d["prompt"])

    def test_large_prompt_uses_file_transport(self):
        big = "x" * 120_000
        (self.proj / "big.txt").write_text(big)
        d = self.j(self.pier("run", "reviewer", "--file", "big.txt", "--dry-run"))
        self.assertEqual(d["prompt_transport"], "file")
        self.assertTrue(d["argv"][-1].startswith("@"))
        r = self.pier("run", "reviewer", "--file", "big.txt")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.argv()[-1].startswith("@"))
        self.assertIn("xxxx", self.j(r)["result_preview"])


class InputModesTests(Base):
    def test_task_file_and_stdin(self):
        (self.proj / "task file.md").write_text("LONG TASK 中文\n")
        r = self.pier("run", "reviewer", "--file", "task file.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("LONG TASK 中文", self.argv()[-1])
        r = self.pier("run", "reviewer", "--file", "-", input_text="FROM STDIN\n")
        self.assertIn("FROM STDIN", self.argv()[-1])
        r = self.pier("run", "reviewer", input_text="IMPLICIT STDIN\n")
        self.assertIn("IMPLICIT STDIN", self.argv()[-1])
        r = self.pier("run", "reviewer", "--task-file", "task file.md", "--dry-run")
        self.assertIn("LONG TASK", self.j(r)["prompt"])

    def test_agent_file_direct(self):
        p = self.tmp / "tmp agent.md"
        p.write_text("---\nname: adhoc\ntools: read\n---\nADHOC ROLE\n")
        d = self.j(self.pier("run", "--agent-file", str(p), "do", "it", "--dry-run"))
        self.assertEqual(d["agent"], "adhoc")
        self.assertEqual(d["argv"][d["argv"].index("--system-prompt") + 1], "ADHOC ROLE")
        self.assertEqual(d["prompt"], "## Task\n\ndo it\n")
        r = self.pier("run", "--agent-file", str(p), "--agent", "reviewer", "x", "--dry-run")
        self.assertEqual(r.returncode, 2)

    def test_custom_mode_without_agent(self):
        d = self.j(self.pier("run", "--instructions", "You are terse.", "summarise", "--model", "m1",
                             "--tools", "read,grep", "--dry-run"))
        self.assertIsNone(d["agent"])
        self.assertEqual(d["prompt"], "## Task\n\nsummarise\n")
        self.assertEqual(d["argv"][d["argv"].index("--system-prompt") + 1], "You are terse.")
        self.assertNotIn("--name", d["argv"])
        d = self.j(self.pier("run", "reviewer", "x", "--role", "OVERRIDE", "--dry-run"))
        self.assertEqual(d["argv"][d["argv"].index("--system-prompt") + 1], "OVERRIDE")
        self.assertNotIn("rigorous code reviewer", d["prompt"])
        self.assertNotIn("rigorous code reviewer", " ".join(d["argv"]))
        r = self.pier("run", "--no-agent", "x", "--dry-run")
        self.assertEqual(r.returncode, 2)

    def test_wrapper_runs_without_bin_on_path(self):
        r = subprocess.run([str(WRAPPER), "--version"], capture_output=True, text=True)
        self.assertIn("pier", r.stdout)


class AgentAuthoringTests(Base):
    GOOD = "---\nname: sql-reviewer\ndescription: Reviews SQL\ntools: read, grep\nthinking: low\n---\n\nYou review SQL.\n"

    def test_validate(self):
        p = self.tmp / "sql-reviewer.md"
        p.write_text(self.GOOD)
        d = self.j(self.pier("agents", "validate", str(p)))
        self.assertTrue(d["ok"])
        p.write_text("---\nname: bad name\nthinking: ultra\nlauncher: x\n---\n")
        r = self.pier("agents", "validate", str(p))
        self.assertEqual(r.returncode, 1)
        d = self.j(r)
        self.assertEqual(len(d["errors"]), 3)
        self.assertTrue(any("no effect" in w for w in d["warnings"]))

    def test_new_default_project_path(self):
        p = self.tmp / "def.md"
        p.write_text(self.GOOD)
        d = self.j(self.pier("agents", "new", "sql-reviewer", "--file", str(p)))
        target = self.proj / ".pier" / "agents" / "sql-reviewer.md"
        self.assertEqual(Path(d["written"]), target.resolve())
        self.assertTrue(target.exists())
        r = self.pier("agents", "new", "sql-reviewer", "--file", str(p))
        self.assertEqual(r.returncode, 2)  # no silent overwrite
        self.assertIn("--force", r.stderr)
        d = self.j(self.pier("run", "sql-reviewer", "t", "--dry-run"))
        self.assertEqual(d["agent_path"], str(target.resolve()))
        d = self.j(self.pier("agents", "new", "sql-reviewer", "--file", "-", "--user", input_text=self.GOOD))
        self.assertEqual(Path(d["written"]), (self.home / ".pier" / "agents" / "sql-reviewer.md").resolve())

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

    def test_new_rejects_invalid_and_name_mismatch(self):
        p = self.tmp / "def.md"
        p.write_text(self.GOOD)
        r = self.pier("agents", "new", "other", "--file", str(p))
        self.assertEqual(r.returncode, 1)
        self.assertFalse((self.proj / ".pier" / "agents" / "other.md").exists())

    def test_skill_documents_boundary(self):
        skill = (ROOT / "SKILL.md").read_text()
        self.assertIn("references/agent-authoring.md", skill)
        self.assertIn(".pier/agents", skill)
        self.assertIn("PIER_HOME", skill)
        self.assertIn("--system-prompt", skill)
        self.assertTrue((ROOT / "references" / "agent-authoring.md").exists())
        readme = (ROOT / "README.md").read_text()
        self.assertIn("不会污染 Pi", readme)
        self.assertIn("`~/.pier/`", readme)
        self.assertIn("0.3.0", readme)


class JobLifecycleTests(Base):
    def test_sync_failure_exit_code_and_stderr_tail(self):
        r = self.pier("run", "reviewer", "x", env={"MOCK_PI_FAIL": "1"})
        self.assertEqual(r.returncode, 3)
        d = self.j(r)
        self.assertEqual(d["state"], "failed")
        self.assertIn("simulated", d["stderr_tail"])

    def test_missing_pi(self):
        r = self.pier("run", "reviewer", "x", env={"PI_BIN": "/nonexistent/pi"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("does not install Pi", r.stderr)
        self.assertEqual(self.pier("run", "reviewer", "x", "--dry-run", env={"PI_BIN": "/nonexistent/pi"}).returncode, 0)

    def test_stdin_not_leaked_to_pi(self):
        r = self.pier("run", "reviewer", "--task", "T")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("leak", self.j(r)["result_preview"])

    def test_background_and_await(self):
        d = self.j(self.pier("start", "reviewer", "bg job", env={"MOCK_PI_SLEEP": "0.5"}))
        jid = d["job_id"]
        r = self.pier("await", jid, "--timeout", "20")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.j(r)["state"], "completed")
        r = self.pier("result", jid)
        self.assertIn("mock-ok: bg job", r.stdout)

    def test_cancel(self):
        d = self.j(self.pier("start", "reviewer", "slow", env={"MOCK_PI_SLEEP": "30"}))
        jid = d["job_id"]
        for _ in range(50):
            if self.j(self.pier("status", jid)).get("state") == "running":
                break
            time.sleep(0.1)
        self.j(self.pier("cancel", jid))
        r = self.pier("await", jid, "--timeout", "10")
        self.assertEqual(self.j(r)["state"], "cancelled")
        self.assertEqual(r.returncode, 1)

    def test_resume_uses_session_not_continue(self):
        d = self.j(self.pier("run", "reviewer", "first"))
        r = self.pier("resume", d["job_id"], "follow up")
        self.assertEqual(r.returncode, 0, r.stderr)
        a = self.argv()
        self.assertNotIn("--continue", a)
        ref = a[a.index("--session") + 1]
        self.assertTrue(ref.endswith(d["session_id"] + ".jsonl"))
        self.assertIn("[resumed", self.j(r)["result_preview"])
        self.assertEqual(a[-1], "## Task\n\nfollow up\n")
        self.assertNotIn("--system-prompt", a)
        self.assertNotIn("--append-system-prompt", a)

    def test_resume_falls_back_to_session_id(self):
        d = self.j(self.pier("run", "reviewer", "first"))
        pier_home = self.home / ".pier"
        st_path = pier_home / "jobs" / d["job_id"] / "status.json"
        st = json.loads(st_path.read_text())
        st["session_file"] = None
        st_path.write_text(json.dumps(st))
        for f in (pier_home / "sessions").glob("*.jsonl"):
            f.rename(f.with_name("sub_" + f.name))
        r = self.pier("resume", d["job_id"], "again", "--dry-run")
        a = self.j(r)["argv"]
        self.assertNotIn("--continue", a)
        self.assertIn("--session", a)

    def test_resume_no_session_errors(self):
        d = self.j(self.pier("run", "reviewer", "x", "--no-session"))
        r = self.pier("resume", d["job_id"], "y")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--no-session", r.stderr)


class BatchTests(Base):
    def write_batch(self, data, name="jobs.json"):
        p = self.proj / name
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p

    def test_mixed_results_exit_nonzero(self):
        (self.proj / "t3.md").write_text("task from file 中文")
        p = self.write_batch({"defaults": {"agent": "reviewer", "model": "m-default"}, "concurrency": 2, "jobs": [
            {"id": "a", "task": "alpha"},
            {"id": "b", "task": "BOOM please", "model": "m-override"},
            {"id": "c", "task_file": "t3.md", "agent": None, "instructions": "Custom role"},
        ]})
        r = self.pier("batch", "run", "--file", str(p), env={"MOCK_PI_FAIL_ON": "BOOM"})
        self.assertEqual(r.returncode, 1)
        d = self.j(r)
        st = {j["id"]: j for j in d["jobs"]}
        self.assertEqual(st["a"]["state"], "completed")
        self.assertEqual(st["b"]["state"], "failed")
        self.assertEqual(st["b"]["exit_code"], 3)
        self.assertIn("simulated", st["b"]["stderr_tail"])
        self.assertEqual(st["c"]["state"], "completed")
        self.assertFalse(d["ok"])
        calls = self.calls()
        self.assertEqual(len(calls), 3)
        models = sorted(c[c.index("--model") + 1] for c in calls)
        self.assertEqual(models, ["m-default", "m-default", "m-override"])
        custom = [c for c in calls if "--system-prompt" in c and c[c.index("--system-prompt") + 1] == "Custom role"][0]
        self.assertNotIn("rigorous code reviewer", " ".join(custom))
        self.assertNotIn("--name", custom)

    def test_all_ok_exit_zero_and_preview(self):
        p = self.write_batch([{"agent": "explorer", "task": "x" * 250}, {"agent": "planner", "task": "p"}])
        r = self.pier("batch", "run", "--file", str(p))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        d = self.j(r)
        self.assertTrue(d["ok"])
        self.assertTrue(d["jobs"][0]["result_truncated"])
        self.assertEqual(len(d["jobs"][0]["result_preview"]), 200)
        self.assertNotIn("result", d["jobs"][0])
        r = self.pier("batch", "result", d["batch_id"])
        self.assertIn("x" * 250, r.stdout)

    def test_atomic_validation_starts_nothing(self):
        p = self.write_batch({"jobs": [
            {"agent": "reviewer", "task": "ok"},
            {"agent": "nope", "task": "x"},
            {"task": "no agent"},
            {"agent": "reviewer", "task": "t", "bogus": 1},
            {"agent": "reviewer"},
        ]})
        r = self.pier("batch", "run", "--file", str(p))
        self.assertEqual(r.returncode, 2)
        d = self.j(r)
        self.assertEqual(sorted(e["index"] for e in d["errors"]), [1, 2, 3, 4])
        self.assertEqual(d["started"], 0)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.proj / ".pier" / "jobs").exists() and any((self.proj / ".pier" / "jobs").iterdir()))

    def test_stdin_and_dry_run(self):
        data = json.dumps({"jobs": [{"agent": "reviewer", "task": "q \"x\" 中文"}]})
        r = self.pier("batch", "run", "--file", "-", "--dry-run", input_text=data)
        self.assertEqual(r.returncode, 0, r.stderr)
        d = self.j(r)
        self.assertEqual(len(d["jobs"]), 1)
        self.assertIn("中文", d["jobs"][0]["prompt"])
        self.assertEqual(self.calls(), [])

    def test_concurrency_limit(self):
        p = self.write_batch({"concurrency": 2, "jobs": [{"agent": "reviewer", "task": f"t{i}"} for i in range(4)]})
        t0 = time.time()
        r = self.pier("batch", "run", "--file", str(p), env={"MOCK_PI_SLEEP": "0.6"})
        elapsed = time.time() - t0
        self.assertEqual(r.returncode, 0)
        self.assertGreater(elapsed, 1.1)  # 4 jobs / 2 workers * 0.6s
        self.assertEqual(self.pier("batch", "run", "--file", str(p), "--concurrency", "0").returncode, 2)

    def test_start_await_status_cancel(self):
        p = self.write_batch({"jobs": [{"id": "fast", "agent": "reviewer", "task": "fast"},
                                       {"id": "slow", "agent": "reviewer", "task": "SLOWME"}]})
        d = self.j(self.pier("batch", "start", "--file", str(p), env={"MOCK_PI_SLEEP_ON": "SLOWME"}))
        bid = d["batch_id"]
        self.assertEqual(len(d["job_ids"]), 2)
        for _ in range(100):
            s = self.j(self.pier("status", "--batch", bid))
            states = {j["id"]: j["state"] for j in s["jobs"]}
            if states["fast"] == "completed" and states["slow"] == "running":
                break
            time.sleep(0.1)
        self.pier("batch", "cancel", bid)
        r = self.pier("batch", "await", bid, "--timeout", "15")
        self.assertEqual(r.returncode, 1)
        states = {j["id"]: j["state"] for j in self.j(r)["jobs"]}
        self.assertEqual(states, {"fast": "completed", "slow": "cancelled"})
        jids = d["job_ids"]
        r = self.pier("cancel", "--jobs", ",".join(jids))
        self.assertEqual(r.returncode, 0)



class CwdDecouplingTests(Base):
    """--cwd is Pi workdir only; pier state lives under ~/.pier (or PIER_HOME)."""

    def pier_state(self):
        return Path(self.home) / ".pier"

    def test_run_stores_under_home_not_project(self):
        other = self.tmp / "other-proj"
        other.mkdir()
        d = self.j(self.pier("run", "reviewer", "hello", "--cwd", str(self.proj)))
        jid = d["job_id"]
        self.assertTrue((self.pier_state() / "jobs" / jid / "status.json").is_file())
        self.assertFalse((self.proj / ".pier" / "jobs").exists())
        self.assertFalse((other / ".pier").exists())
        # sessions isolated under pier home, not project / Pi default
        a = self.argv()
        sdir = a[a.index("--session-dir") + 1]
        self.assertEqual(Path(sdir).resolve(), (self.pier_state() / "sessions").resolve())
        self.assertTrue(sdir.startswith(str(self.pier_state())))
        # project agents dir may exist from lookup, but no runtime dirs
        for name in ("jobs", "batches", "sessions"):
            self.assertFalse((self.proj / ".pier" / name).exists())

    def test_status_await_result_resume_from_other_cwd(self):
        d = self.j(self.pier("run", "reviewer", "first", "--cwd", str(self.proj)))
        jid = d["job_id"]
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        # no --cwd needed
        st = self.j(self.pier("status", jid, cwd=elsewhere))
        self.assertEqual(st["job_id"], jid)
        self.assertEqual(st["state"], "completed")
        r = self.pier("result", jid, cwd=elsewhere)
        self.assertIn("mock-ok: first", r.stdout)
        r = self.pier("resume", jid, "follow", cwd=elsewhere)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[resumed", self.j(r)["result_preview"])
        # resume also wrote under home, not under elsewhere or project
        self.assertFalse((elsewhere / ".pier").exists())
        self.assertFalse((self.proj / ".pier" / "jobs").exists())

    def test_pier_home_override(self):
        custom = self.tmp / "custom-pier-home"
        d = self.j(self.pier("run", "reviewer", "x", env={"PIER_HOME": str(custom)}))
        jid = d["job_id"]
        self.assertTrue((custom / "jobs" / jid / "status.json").is_file())
        self.assertFalse((self.home / ".pier" / "jobs").exists())
        self.assertFalse((self.proj / ".pier" / "jobs").exists())
        other = self.tmp / "somewhere-else"
        other.mkdir()
        st = self.j(self.pier("status", jid, env={"PIER_HOME": str(custom)}, cwd=other))
        self.assertEqual(st["state"], "completed")
        # dry-run reports pier_home
        dr = self.j(self.pier("run", "reviewer", "y", "--dry-run", env={"PIER_HOME": str(custom)}))
        self.assertEqual(Path(dr["pier_home"]).resolve(), custom.resolve())

    def test_session_dir_isolated_from_project(self):
        d = self.j(self.pier("run", "reviewer", "s"))
        a = self.argv()
        sdir = Path(a[a.index("--session-dir") + 1]).resolve()
        self.assertEqual(sdir, (self.pier_state() / "sessions").resolve())
        self.assertNotEqual(sdir, (self.proj / ".pier" / "sessions").resolve())
        # session files landed only under pier home
        sessions = list((self.pier_state() / "sessions").glob("*.jsonl"))
        self.assertTrue(sessions)
        self.assertFalse((self.proj / ".pier" / "sessions").exists())
        self.assertTrue(d.get("session_id"))

    def test_project_pier_runtime_state_is_never_read(self):
        """A <cwd>/.pier/jobs|batches tree is ignored by every query command."""
        jid, bid = "jplanted0001", "bplanted0001"
        jdir = self.proj / ".pier" / "jobs" / jid
        jdir.mkdir(parents=True)
        (jdir / "status.json").write_text(json.dumps({
            "job_id": jid, "state": "completed", "exit_code": 0, "cwd": str(self.proj),
            "mode": "json", "session_id": "s1", "result_path": str(jdir / "result.md"),
        }), encoding="utf-8")
        (jdir / "result.md").write_text("PLANTED\n", encoding="utf-8")
        bdir = self.proj / ".pier" / "batches" / bid
        bdir.mkdir(parents=True)
        (bdir / "batch.json").write_text(json.dumps({"batch_id": bid, "jobs": [jid]}), encoding="utf-8")
        home_jobs = str(self.pier_state())
        for args in (("status", jid), ("await", jid, "--timeout", "1"), ("result", jid),
                     ("cancel", jid), ("resume", jid, "x", "--dry-run"),
                     ("status", "--batch", bid), ("batch", "status", bid), ("batch", "result", bid)):
            r = self.pier(*args, cwd=self.proj)
            self.assertNotEqual(r.returncode, 0, args)
            self.assertNotIn("PLANTED", r.stdout + r.stderr, args)
            self.assertIn("unknown", r.stderr, args)
            self.assertIn(home_jobs, r.stderr, args)
            self.assertNotIn(str(self.proj / ".pier"), r.stderr, args)
        # list only shows ~/.pier jobs
        self.j(self.pier("run", "reviewer", "fresh", "--cwd", str(self.proj)))
        rows = self.j(self.pier("list", cwd=self.proj))
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0]["job_id"], jid)
        # planted tree untouched; nothing new written beside it
        self.assertEqual([p.name for p in (self.proj / ".pier" / "jobs").iterdir()], [jid])
        self.assertEqual(json.loads((jdir / "status.json").read_text())["state"], "completed")

    def test_query_commands_have_no_cwd_flag(self):
        d = self.j(self.pier("run", "reviewer", "q"))
        jid = d["job_id"]
        for args in (("status", jid), ("await", jid), ("result", jid), ("cancel", jid),
                     ("list",), ("resume", jid, "x", "--dry-run"), ("batch", "status", "b1")):
            r = self.pier(*args, "--cwd", str(self.proj))
            self.assertEqual(r.returncode, 2, args)
            self.assertIn("unrecognized arguments: --cwd", r.stderr, args)



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

    def test_cli_default_toon_and_json_flag(self):
        tmp = Path(tempfile.mkdtemp(prefix="pier-toon-"))
        try:
            home = tmp / "home"; home.mkdir()
            proj = tmp / "proj"; proj.mkdir()
            env = {k: v for k, v in os.environ.items()}
            env.update({"PI_BIN": str(MOCK), "HOME": str(home), "PIER_QUIET": "1"})
            r = subprocess.run([sys.executable, str(PIER), "agents", "list"], cwd=str(proj), env=env,
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(r.stdout.lstrip().startswith("["), r.stdout)
            self.assertIn("{name,description}", r.stdout)
            r2 = subprocess.run([sys.executable, str(PIER), "agents", "list", "--json"], cwd=str(proj), env=env,
                                capture_output=True, text=True, timeout=30)
            rows = json.loads(r2.stdout)
            self.assertTrue(isinstance(rows, list) and rows)
            self.assertEqual(set(rows[0].keys()), {"name", "description"})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

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
        d = self.j(self.pier("run", "a", "t", "--dry-run"))
        self.assertIn("--append-system-prompt", d["argv"])
        self.assertNotIn("--system-prompt", d["argv"])
        self.assertEqual(d["argv"][d["argv"].index("--append-system-prompt") + 1], "APPEND ME")

    def test_empty_skills_and_inherit_false(self):
        self.write_agent(".pier/agents/s.md", "---\nname: s\nskills:\n---\nB\n")
        a = self.j(self.pier("run", "s", "t", "--dry-run"))["argv"]
        self.assertIn("--no-skills", a)
        self.write_agent(".pier/agents/s2.md", "---\nname: s2\ninheritSkills: false\n---\nB\n")
        a = self.j(self.pier("run", "s2", "t", "--dry-run"))["argv"]
        self.assertIn("--no-skills", a)

    def test_named_skill_and_skill_path(self):
        skill_dir = self.proj / ".pi" / "skills" / "demo-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: demo-skill\n---\nDo demos.\n")
        priv = self.proj / ".pier" / "agents" / "priv"
        priv.mkdir(parents=True)
        (priv / "local-skill.md").write_text("---\nname: local\n---\nLocal.\n")
        self.write_agent(".pier/agents/s.md",
                         "---\nname: s\nskills: demo-skill\nskillPath: priv/local-skill.md\n---\nB\n")
        a = self.j(self.pier("run", "s", "t", "--dry-run"))["argv"]
        skills = [a[i + 1] for i, x in enumerate(a) if x == "--skill"]
        self.assertEqual(len(skills), 2)
        self.assertTrue(any(str(skill_dir / "SKILL.md") == s or s.endswith("demo-skill/SKILL.md") for s in skills))
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
        a = self.j(self.pier("run", "e", "t", "--dry-run"))["argv"]
        self.assertIn("--no-extensions", a)
        exts = [a[i + 1] for i, x in enumerate(a) if x == "--extension"]
        self.assertEqual(len(exts), 2)
        self.assertTrue(any(e.endswith("my-ext.ts") for e in exts))
        self.assertIn("builtin:foo", exts)
        self.write_agent(".pier/agents/e2.md", "---\nname: e2\nextensions:\n---\nB\n")
        a = self.j(self.pier("run", "e2", "t", "--dry-run"))["argv"]
        self.assertIn("--no-extensions", a)
        self.assertNotIn("--extension", a)

    def test_inherit_project_context_false(self):
        self.write_agent(".pier/agents/c.md",
                         "---\nname: c\ninheritProjectContext: false\ninheritGlobalContext: false\n---\nB\n")
        d = self.j(self.pier("run", "c", "t", "--dry-run"))
        self.assertIn("--no-context-files", d["argv"])
        self.assertIn("inheritGlobalContext", d["ignored_fields"])

    def test_live_run_passes_system_prompt(self):
        self.write_agent(".pier/agents/r.md", "---\nname: r\n---\nLIVE ROLE\n")
        r = self.pier("run", "r", "do it")
        self.assertEqual(r.returncode, 0, r.stderr)
        a = self.argv()
        self.assertEqual(a[a.index("--system-prompt") + 1], "LIVE ROLE")
        self.assertEqual(a[-1], "## Task\n\ndo it\n")


@unittest.skipUnless(os.environ.get("PIER_TEST_REAL_PI"), "set PIER_TEST_REAL_PI=/path/to/pi")
class RealPiArgvTests(Base):
    """Real Pi parses pier's argv; empty HOME + --offline -> fails only on missing API key."""

    def test_real_pi_accepts_argv(self):
        real = os.environ["PIER_TEST_REAL_PI"]
        self.env["PI_BIN"] = real
        self.write_agent(".pier/agents/r.md", "---\nname: r\nmodel: anthropic/claude-sonnet-4-5:low\n"
                         "tools: read, grep\nexcludeTools: bash\n---\nRole \"q\" 中文\n")
        clean = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.home), "PI_BIN": real,
                 "PIER_QUIET": "1"}
        r = subprocess.run([sys.executable, str(PIER), "run", "r", "task it's 中文", "--extra", "--offline"],
                           cwd=str(self.proj), env=clean, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=60)
        d = json.loads(r.stdout)
        self.assertNotIn("Unknown option", d.get("stderr_tail", ""))
        self.assertIn("No API key", d.get("stderr_tail", ""))
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
