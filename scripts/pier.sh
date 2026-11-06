#!/usr/bin/env bash
# Thin wrapper so PATH installs can call `pier` without remembering scripts/.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/pier.py" "$@"
