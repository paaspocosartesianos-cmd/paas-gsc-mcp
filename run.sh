#!/bin/bash
# Wrapper for the PAAS GSC MCP server. Sets up a venv on first run, then runs the server.
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/venv"

if [ ! -d "$VENV" ]; then
  echo "[paas-gsc-mcp] First run — creating venv at $VENV" >&2
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -r "$DIR/requirements.txt"
fi

exec "$VENV/bin/python" "$DIR/server.py"
