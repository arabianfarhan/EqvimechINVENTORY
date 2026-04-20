#!/bin/bash
set -e

# Double-click this file in Finder to run an interactive push helper.
# It will stage all changes, prompt for a commit message, commit, and push to origin/main.

cd "$(dirname "$0")"

echo "Repository: $(pwd)"

git status --porcelain

read -p "Stage all changes and push? [y/N]: " ok
if [[ "$ok" != "y" && "$ok" != "Y" ]]; then
  echo "Aborted."
  read -n1 -r -p "Press any key to exit..."
  exit 1
fi

git add -A
read -p "Commit message (leave blank for 'Auto commit'): " msg
if [ -z "$msg" ]; then msg="Auto commit"; fi

# Try to commit; if nothing to commit, continue
if git commit -m "$msg"; then
  echo "Committed."
else
  echo "Nothing to commit or commit failed (maybe no changes). Continuing to push staged/remote refs."
fi

echo "Pushing to origin/main..."
git push origin main

echo "Push complete."
read -n1 -r -p "Press any key to exit..."
