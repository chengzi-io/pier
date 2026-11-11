#!/usr/bin/env python3
"""Tests for the orchestrator plugin hooks. Runs hooks/orchestrator.py as a
subprocess with a temp CLAUDE_PLUGIN_DATA; never calls a model."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "orchestrator.py"
HOOKS_JSON = ROOT / "hooks" / "hooks.json"
PROMPT = ROOT / "hooks" / "orchestrator-prompt.md"

ALLOW_BASH = [
    "scripts/pier run librarian \"where is the CLI\"",
    "/Users/me/repo/scripts/pier send abc123 hello",
    "git status",
    "git log --oneline -5",
    "git show HEAD",
    "git diff",
    "git branch -a",
    "ls -la",
    "pwd",
    "cat scripts/pier",
    "head -20 AGENTS.md",
    "tail -n 5 README.md",
    "wc -l tests/test_pier.py",
    "sed -n 1,20p scripts/pier",
    "echo hello",
    "command -v python3",
    "which git",
    "cd x && ls -la",
    "FOO=1 ls",
    "cd /tmp",
]

DENY_BASH = [
    ("rg foo", "search"),
    ("grep -rn foo .", "search"),
    ("find . -name '*.py'", "search"),
    ("fd foo", "search"),
    ("ag foo", "search"),
    ("ack foo", "search"),
    ("pytest -q", "checks"),
    ("python3 -m unittest discover -s tests", "checks"),
    ("python -m unittest", "checks"),
    ("go test ./...", "checks"),
    ("npm test", "checks"),
    ("pnpm run build", "checks"),
    ("npx tsc", "checks"),
    ("make check", "checks"),
    ("cargo build", "checks"),
    ("ruff check .", "checks"),
    ("xcodebuild -scheme App", "checks"),
    ("git commit -m x", "vcs"),
    ("git push", "vcs"),
    ("gh pr list", "vcs"),
    ("curl https://example.com", "web"),
    ("wget https://example.com", "web"),
    ("cd x && rg foo", "search"),
    ("git status; go test ./...", "checks"),
    ("FOO=1 pytest", "checks"),
    ("git status && curl https://example.com", "web"),
]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pier-hook-"))
        self.data = self.tmp / "data"
        self.data.mkdir()
        self.env = {k: v for k, v in os.environ.items() if k != "PIER_ORCHESTRATOR"}
        self.env["CLAUDE_PLUGIN_DATA"] = str(self.data)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ----------------------------------------------------------

    def payload(self, tool, tool_input, agent_id=None, session_id="s-1"):
        data = {"session_id": session_id, "cwd": "/tmp", "hook_event_name": "PreToolUse",
                "tool_name": tool, "tool_input": tool_input}
        if agent_id is not None:
            data["agent_id"] = agent_id
        return data

    def run_hook(self, argv, stdin, mode="enforce", hook=None):
        env = dict(self.env)
        if mode is not None:
            env["PIER_ORCHESTRATOR"] = mode
        if not isinstance(stdin, str):
            stdin = json.dumps(stdin)
        return subprocess.run([sys.executable, str(hook or HOOK)] + argv,
                              input=stdin, text=True, capture_output=True,
                              env=env, timeout=60)

    def events(self):
        path = self.data / "orchestrator-events.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def assert_allow(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")

    def assert_deny(self, proc, category):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertEqual(out["permissionDecision"], "deny")
        reason = out["permissionDecisionReason"]
        self.assertTrue(reason.startswith(f"orchestrator: delegate {category}: "), reason)
        self.assertNotIn("\n", reason)
        return reason


class BashTest(Base):
    def test_allow(self):
        for command in ALLOW_BASH:
            with self.subTest(command=command):
                self.data.joinpath("orchestrator-events.jsonl").unlink(missing_ok=True)
                proc = self.run_hook(["pre-tool-use"],
                                     self.payload("Bash", {"command": command}))
                self.assert_allow(proc)
                self.assertEqual([e["verdict"] for e in self.events()], ["allow"])
                self.assertEqual(self.events()[0]["category"], "allow")

    def test_deny(self):
        for command, category in DENY_BASH:
            with self.subTest(command=command, category=category):
                self.data.joinpath("orchestrator-events.jsonl").unlink(missing_ok=True)
                proc = self.run_hook(["pre-tool-use"],
                                     self.payload("Bash", {"command": command}))
                self.assert_deny(proc, category)
                event = self.events()[0]
                self.assertEqual(event["category"], category)
                self.assertEqual(event["verdict"], "deny")
                self.assertEqual(event["tool"], "Bash")
                self.assertEqual(event["mode"], "enforce")
                self.assertEqual(event["session_id"], "s-1")
                self.assertTrue(event["ts"].endswith("Z"))

    def test_head_is_two_words_of_offending_segment(self):
        proc = self.run_hook(["pre-tool-use"],
                             self.payload("Bash", {"command": "git status; go test ./..."}))
        self.assert_deny(proc, "checks")
        self.assertEqual(self.events()[0]["head"], "go test")


class ToolTest(Base):
    def test_grep_and_glob_suggest_librarian(self):
        for tool, tool_input in (("Grep", {"pattern": "class Run"}),
                                 ("Glob", {"pattern": "**/*.py"})):
            with self.subTest(tool=tool):
                self.data.joinpath("orchestrator-events.jsonl").unlink(missing_ok=True)
                proc = self.run_hook(["pre-tool-use"], self.payload(tool, tool_input))
                reason = self.assert_deny(proc, "search")
                self.assertIn('pier run librarian "<question>"', reason)
                self.assertEqual(self.events()[0]["tool"], tool)

    def test_read_is_always_allowed_and_logs_path(self):
        proc = self.run_hook(["pre-tool-use"],
                             self.payload("Read", {"file_path": "/tmp/proj/scripts/pier"}))
        self.assert_allow(proc)
        event = self.events()[0]
        self.assertEqual(event["category"], "read")
        self.assertEqual(event["verdict"], "allow")
        self.assertEqual(event["head"], "/tmp/proj/scripts/pier")

    def test_agent_id_is_allowed_silently(self):
        for mode in ("shadow", "enforce"):
            with self.subTest(mode=mode):
                self.data.joinpath("orchestrator-events.jsonl").unlink(missing_ok=True)
                proc = self.run_hook(["pre-tool-use"],
                                     self.payload("Bash", {"command": "rg foo"},
                                                  agent_id="sub-1"),
                                     mode=mode)
                self.assert_allow(proc)
                event = self.events()[0]
                self.assertEqual(event["category"], "subagent")
                self.assertEqual(event["verdict"], "allow")


class ModeTest(Base):
    def test_inactive_modes_touch_nothing(self):
        for mode in (None, "", "off", "SHADOW", "dry-run"):
            with self.subTest(mode=mode):
                proc = self.run_hook(["pre-tool-use"],
                                     self.payload("Bash", {"command": "rg foo"}), mode=mode)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, "")
                self.assertEqual(self.events(), [])
                start = self.run_hook(["session-start"], "", mode=mode)
                self.assertEqual(start.returncode, 0, start.stderr)
                self.assertEqual(start.stdout, "")

    def test_shadow_logs_would_deny_without_decision(self):
        proc = self.run_hook(["pre-tool-use"],
                             self.payload("Bash", {"command": "rg foo"}), mode="shadow")
        self.assert_allow(proc)
        event = self.events()[0]
        self.assertEqual(event["mode"], "shadow")
        self.assertEqual(event["verdict"], "would-deny")


class LogTest(Base):
    def test_log_has_no_full_command(self):
        command = "curl -H 'Authorization: Bearer TOPSECRET' https://example.com"
        proc = self.run_hook(["pre-tool-use"], self.payload("Bash", {"command": command}))
        self.assert_deny(proc, "web")
        raw = (self.data / "orchestrator-events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("TOPSECRET", raw)
        self.assertNotIn(command, raw)
        self.assertEqual(self.events()[0]["head"], "curl -H")

    def test_log_falls_back_to_home_without_plugin_data(self):
        env = dict(self.env)
        env.pop("CLAUDE_PLUGIN_DATA")
        env["HOME"] = str(self.tmp / "home")
        env["PIER_ORCHESTRATOR"] = "shadow"
        proc = subprocess.run([sys.executable, str(HOOK), "pre-tool-use"],
                              input=json.dumps(self.payload("Bash", {"command": "pwd"})),
                              text=True, capture_output=True, env=env, timeout=60)
        self.assert_allow(proc)
        path = self.tmp / "home" / ".claude" / "pier-orchestrator" / "orchestrator-events.jsonl"
        self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)


class FailLoudTest(Base):
    def test_bad_json_fails_loud(self):
        proc = self.run_hook(["pre-tool-use"], "{not json")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertIn("invalid hook input", proc.stderr)

    def test_non_object_json_fails_loud(self):
        proc = self.run_hook(["pre-tool-use"], "[]")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertIn("expected a JSON object", proc.stderr)


class SessionStartTest(Base):
    def test_prints_prompt_as_additional_context(self):
        proc = self.run_hook(["session-start"], "", mode="shadow")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart")
        self.assertEqual(out["additionalContext"], PROMPT.read_text(encoding="utf-8"))

    def test_missing_prompt_fails_loud(self):
        bare = self.tmp / "bare"
        bare.mkdir()
        script = bare / "orchestrator.py"
        shutil.copy(HOOK, script)
        proc = self.run_hook(["session-start"], "", mode="enforce", hook=script)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertIn("cannot read prompt", proc.stderr)


class HooksJsonTest(Base):
    def test_wiring(self):
        hooks = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]
        start = hooks["SessionStart"][0]["hooks"][0]["command"]
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/hooks/orchestrator.py", start)
        self.assertTrue(start.endswith(" session-start"))
        tool = hooks["PreToolUse"][0]
        self.assertEqual(tool["matcher"], "Bash|Grep|Glob|Read")
        self.assertTrue(tool["hooks"][0]["command"].endswith(" pre-tool-use"))


if __name__ == "__main__":
    unittest.main()
