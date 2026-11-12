#!/usr/bin/env python3
"""Tests for scripts/session-start/orchestrator_reminder.py. Runs the hook as a subprocess; no model calls."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "scripts" / "session-start" / "orchestrator_reminder.py"
HOOKS_JSON = ROOT / "hooks" / "hooks.json"
PLUGIN_JSON = ROOT / ".claude-plugin" / "plugin.json"


class SessionStartTest(unittest.TestCase):
    def run_hook(self, stdin="ignored stdin"):
        return subprocess.run([sys.executable, str(HOOK)], input=stdin, text=True,
                              capture_output=True, timeout=30)

    def reminder(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        out = json.loads(proc.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart")
        return out["additionalContext"]

    def test_emits_one_short_factual_reminder(self):
        text = self.reminder(self.run_hook())
        self.assertLessEqual(len(text), 400)
        for phrase in ("delegated through the pier skill",
                       "decisions, specs and verdicts stay here",
                       "over three exploratory commands",
                       "Bash here is for checks"):
            self.assertIn(phrase, text)
        self.assertNotIn("You must", text)
        self.assertNotIn("agents list", text)

    def test_readme_quotes_the_reminder(self):
        text = self.reminder(self.run_hook())
        self.assertIn(f"> {text}\n", (ROOT / "README.md").read_text(encoding="utf-8"))

    def test_does_not_read_stdin(self):
        # The write end stays open: a hook that reads stdin blocks until the timeout.
        read_end, write_end = os.pipe()
        try:
            proc = subprocess.run([sys.executable, str(HOOK)], stdin=read_end, text=True,
                                  capture_output=True, timeout=10)
        finally:
            os.close(read_end)
            os.close(write_end)
        self.assertEqual(self.reminder(proc), self.reminder(self.run_hook()))

    def test_hooks_json_wires_only_session_start(self):
        hooks = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]
        self.assertEqual(set(hooks), {"SessionStart"})
        group = hooks["SessionStart"][0]
        self.assertEqual(group["matcher"], "startup|clear|compact")
        handler = group["hooks"][0]
        self.assertEqual(handler["command"], "python3")
        self.assertEqual(handler["args"], ["${CLAUDE_PLUGIN_ROOT}/scripts/session-start/orchestrator_reminder.py"])
        self.assertNotIn("onFailure", handler)

    def test_plugin_has_no_mode_option(self):
        plugin = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
        self.assertNotIn("userConfig", plugin)


if __name__ == "__main__":
    unittest.main()
