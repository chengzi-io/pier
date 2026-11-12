#!/usr/bin/env python3
"""SessionStart: one fixed orchestrator reminder."""
from __future__ import annotations

import json
import sys

REMINDER = (
    "This is an orchestrator session: legwork is decomposed into bounded tasks "
    "and delegated through the pier skill; decisions, specs and verdicts stay here. "
    "Legwork means codebase sweeps, web lookups, multi-file edits, long runs, or any "
    "step needing over three exploratory commands. Bash here is for checks: "
    "git status/diff, one test run, `sed -n` on a cited range."
)


def main():
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": REMINDER,
    }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
