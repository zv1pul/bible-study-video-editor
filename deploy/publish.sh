#!/usr/bin/env bash
# Publish to GitHub and/or the Hugging Face Space.
#
#   First time:  ./deploy/publish.sh <github-url> <space-url>
#   After that:  ./deploy/publish.sh            (pushes to whatever is set up)
set -e
cd "$(dirname "$0")/.."

add_remote () {
  local name="$1" url="$2"
  [ -z "$url" ] && return 0
  if git remote | grep -qx "$name"; then
    git remote set-url "$name" "$url"
  else
    git remote add "$name" "$url"
  fi
  echo "  $name -> $url"
}

add_remote origin "$1"
add_remote space  "$2"

if ! git remote | grep -q .; then
  echo "No remotes configured yet. Run:"
  echo "  ./deploy/publish.sh https://github.com/<you>/<repo>.git https://huggingface.co/spaces/<you>/<space>"
  exit 1
fi

branch="$(git rev-parse --abbrev-ref HEAD)"
for remote in $(git remote); do
  echo
  echo "Pushing $branch to $remote..."
  git push -u "$remote" "$branch"
done

echo
echo "Done."
git remote | grep -qx space && cat <<'NOTE'

The Space rebuilds itself now; it usually takes a couple of minutes.

Before anyone uses it, open the Space's Settings -> Variables and secrets and
add:
    GEMINI_API_KEY
    GROQ_API_KEY

Without those, visitors have to paste their own keys - which is the better
choice if several people will be using it, since the free allowances are then
per person rather than shared.
NOTE
