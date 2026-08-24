#!/usr/bin/env bash
# Fetch the latest version and refresh the packages. Safe to run any time —
# it never touches your API key or anything in assets/.
set -e
cd "$(dirname "$0")"

if [ ! -d ".git" ]; then
  echo "This copy was not installed from the repository, so it cannot update"
  echo "itself. Download the latest version and replace this folder."
  exit 1
fi

if ! git remote | grep -q .; then
  echo "No update source is configured for this copy yet."
  exit 1
fi

echo "Checking for a newer version..."
git stash push --quiet --include-untracked -- ':!assets' 2>/dev/null || true
git pull --ff-only
[ -d ".venv" ] && ./.venv/bin/pip install -q -r requirements.txt

echo
echo "Up to date. Launch the app with run.command as usual."
