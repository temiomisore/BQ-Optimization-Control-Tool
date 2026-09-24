#!/usr/bin/env bash
# Thin wrapper around the canonical CLI. Prefers a local virtualenv if present.
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
exec "$PYTHON_BIN" -m optimizer.cli "$@"
