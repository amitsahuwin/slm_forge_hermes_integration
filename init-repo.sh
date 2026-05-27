#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Initialize the local git repo and push to GitHub.
# Idempotent: safe to re-run.
# ─────────────────────────────────────────────────────────────
set -euo pipefail

REMOTE="git@github.com:amitsahuwin/slm_forge_hermes_integration.git"

echo "→ Verifying SSH access to GitHub..."
ssh_output=$(ssh -T -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 git@github.com 2>&1 || true)
if ! echo "$ssh_output" | grep -q "successfully authenticated"; then
    cat <<MSG
✗ SSH access to GitHub is not configured.

To set it up:
  1. Generate an SSH key (if you don't have one):
       ssh-keygen -t ed25519 -C "your_email@example.com"
  2. Copy the public key:
       pbcopy < ~/.ssh/id_ed25519.pub
  3. Add it on GitHub:
       https://github.com/settings/ssh/new
  4. Re-run this script:
       ./init-repo.sh
MSG
    exit 1
fi
echo "✓ SSH access OK"

if [ -d .git ]; then
    echo "→ Git repo already initialized"
else
    echo "→ Initializing git repo (branch: main)"
    git init -b main >/dev/null
fi

if ! git remote get-url origin >/dev/null 2>&1; then
    echo "→ Adding remote origin: $REMOTE"
    git remote add origin "$REMOTE"
else
    current=$(git remote get-url origin)
    if [ "$current" != "$REMOTE" ]; then
        echo "→ Updating remote origin"
        git remote set-url origin "$REMOTE"
    else
        echo "✓ Remote origin already set"
    fi
fi

echo "→ Staging files..."
git add -A

if git diff --cached --quiet; then
    echo "→ Nothing to commit"
else
    git commit -m "Phase 0: project scaffold (uv + FastAPI + React + Vite + Tailwind + Hermes)" >/dev/null
    echo "✓ Committed"
fi

echo "→ Pushing to GitHub..."
git push -u origin main

echo ""
echo "✓ Pushed to: $REMOTE"
echo "  https://github.com/amitsahuwin/slm_forge_hermes_integration"
