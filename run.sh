#!/usr/bin/env bash
# macOS / Linux launcher: sets up a virtual environment on first run,
# then starts the app in your browser. Double-clickable as run.command.
set -e
cd "$(dirname "$0")"

PY=python3
command -v $PY >/dev/null 2>&1 || { echo "Python 3 is not installed. Get it from https://www.python.org/downloads/"; exit 1; }

if [ ! -d ".venv" ]; then
  echo "First run: creating the virtual environment (a few minutes)…"
  $PY -m venv .venv
  ./.venv/bin/python -m pip install --upgrade pip
  ./.venv/bin/pip install -r requirements-local.txt
fi

echo "Starting the Bible Study Video Editor…"
exec ./.venv/bin/python -m streamlit run app.py
