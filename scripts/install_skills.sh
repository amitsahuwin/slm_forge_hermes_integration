#!/usr/bin/env bash
# Copy versioned skills from .hermes-skills/ into ~/.hermes/skills/
set -euo pipefail

SRC=".hermes-skills"
DEST="$HOME/.hermes/skills"

if [ ! -d "$SRC" ]; then
    echo "✗ $SRC not found. Run this from the project root."
    exit 1
fi

mkdir -p "$DEST"

count=0
shopt -s nullglob
for f in "$SRC"/*.md; do
    base=$(basename "$f")
    if [ "$base" = "README.md" ]; then continue; fi
    cp "$f" "$DEST/"
    echo "  ✓ Installed $base"
    count=$((count + 1))
done

if [ "$count" -eq 0 ]; then
    echo ""
    echo "ℹ No skills yet — Phase 2 will populate .hermes-skills/."
    echo "  ($DEST exists and is ready.)"
else
    echo ""
    echo "✓ Installed $count skill(s) to $DEST"
fi
