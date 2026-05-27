#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Install Ollama + qwen2.5-coder:14b + Hermes Agent.
# Configure Hermes to use the local Ollama instance.
# ─────────────────────────────────────────────────────────────
set -euo pipefail

echo "═══════════════════════════════════════════════════════"
echo "  SLM-Forge: Hermes Agent + Ollama Setup"
echo "  Target: macOS Apple Silicon (M3 Max 36 GB)"
echo "═══════════════════════════════════════════════════════"
echo ""

# ── 1. Ollama ─────────────────────────────────────────────────
if ! command -v ollama &>/dev/null; then
    if command -v brew &>/dev/null; then
        echo "→ Installing Ollama via Homebrew..."
        brew install ollama
    else
        echo "→ Installing Ollama via the official installer..."
        curl -fsSL https://ollama.com/install.sh | sh
    fi
else
    echo "✓ Ollama already installed: $(ollama --version 2>/dev/null | head -n1)"
fi

# ── 2. Configure keep-alive BEFORE starting Ollama ────────────
echo "→ Setting OLLAMA_KEEP_ALIVE=2m (frees RAM during training)"
launchctl setenv OLLAMA_KEEP_ALIVE 2m || true

# ── 3. Start / restart Ollama so it picks up the env var ──────
if command -v brew &>/dev/null; then
    if brew services list 2>/dev/null | grep -q "ollama.*started"; then
        echo "→ Restarting Ollama to pick up new env vars..."
        brew services restart ollama
    else
        echo "→ Starting Ollama service..."
        brew services start ollama || true
    fi
else
    echo "  ⚠ Homebrew not found — please run 'ollama serve' manually in another terminal."
fi

# Wait for Ollama to come up
echo "→ Waiting for Ollama API on :11434..."
for i in {1..15}; do
    if curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then
        echo "✓ Ollama responding"
        break
    fi
    if [ "$i" -eq 15 ]; then
        echo "✗ Ollama didn't respond in 15s. Try: brew services restart ollama"
        exit 1
    fi
    sleep 1
done

# ── 4. Pull qwen2.5-coder:14b ─────────────────────────────────
if ollama list 2>/dev/null | grep -q "qwen2.5-coder:14b"; then
    echo "✓ qwen2.5-coder:14b already pulled"
else
    echo "→ Pulling qwen2.5-coder:14b (~9 GB, takes a few minutes)..."
    ollama pull qwen2.5-coder:14b
fi

# ── 5. Install Hermes Agent ───────────────────────────────────
# Search common install locations for hermes binary
locate_hermes() {
    for candidate in "$HOME/.local/bin" "/opt/homebrew/bin" "/usr/local/bin"; do
        if [ -x "$candidate/hermes" ]; then
            export PATH="$candidate:$PATH"
            return 0
        fi
    done
    command -v hermes &>/dev/null
}

if ! locate_hermes; then
    echo "→ Installing Hermes Agent..."
    curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
    if ! locate_hermes; then
        echo "✗ Hermes install completed but binary not found. Check ~/.local/bin or restart your shell."
        exit 1
    fi
else
    echo "✓ Hermes already installed"
fi

# ── 6. Configure Hermes for local Ollama ──────────────────────
echo "→ Configuring Hermes to use local Ollama..."
hermes config set provider ollama         || true
hermes config set model qwen2.5-coder:14b || true
hermes config set base_url http://localhost:11434 || true

echo ""
echo "✓ Hermes configured. Current config:"
hermes config show 2>/dev/null || echo "  (run 'hermes config show' yourself to verify)"

cat <<MSG

────────────────────────────────────────────────────────────────
Next steps:
  • make hermes-install-skills   # load SLM-Forge skills (Phase 2+)
  • make dev                     # start UI + API

Switch to Groq later (one command, free tier):
  export GROQ_API_KEY=gsk_...
  hermes config set provider groq
  hermes config set model qwen-2.5-coder-32b
  hermes config set api_key \$GROQ_API_KEY
────────────────────────────────────────────────────────────────
MSG
