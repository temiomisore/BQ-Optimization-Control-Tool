#!/usr/bin/env bash
set -e
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

if [ -f ".venv/bin/python3" ]; then
    PYTHON_BIN=".venv/bin/python3"
elif [ -f "venv/bin/python3" ]; then
    PYTHON_BIN="venv/bin/python3"
elif [ -f "$HOME/.venv/bin/python3" ]; then
    PYTHON_BIN="$HOME/.venv/bin/python3"
elif [ -f "$HOME/.venv-bq/bin/python3" ]; then
    PYTHON_BIN="$HOME/.venv-bq/bin/python3"
else
    PYTHON_BIN="python3"
fi

$PYTHON_BIN scripts/demo_setup_2.py
