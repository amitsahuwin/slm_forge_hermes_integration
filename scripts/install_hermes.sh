#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Install Ollama + qwen3:30b-a3b + Hermes Agent and point Hermes at the
# local Ollama instance.
#
# Cross-platform (Phase T):
#   • macOS (Apple Silicon)  → Homebrew + launchctl + brew services
#   • Linux  (incl. NVIDIA)  → official installer + systemd (or nohup fallback)
# ─────────────────────────────────────────────────────────────
set -euo pipefail

OS="$(uname -s)"
MODEL="${HERMES_MODEL:-qwen3:30b-a3b}"
KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-2m}"

echo "═══════════════════════════════════════════════════════"
echo "  SLM-Forge: Hermes Agent + Ollama Setup"
echo "  Host: $OS $(uname -m)"
echo "═══════════════════════════════════════════════════════"
echo ""

is_macos() { [ "$OS" = "Darwin" ]; }
have()     { command -v "$1" >/dev/null 2>&1; }

# ── 1. Install Ollama ─────────────────────────────────────────
if ! have ollama; then
    if is_macos && have brew; then
        echo "→ Installing Ollama via Homebrew..."
        brew install ollama
    else
        echo "→ Installing Ollama via the official installer..."
        curl -fsSL https://ollama.com/install.sh | sh
    fi
else
    echo "✓ Ollama already installed: $(ollama --version 2>/dev/null | head -n1)"
fi

# ── 2. Start Ollama with keep-alive ───────────────────────────
echo "→ Configuring OLLAMA_KEEP_ALIVE=$KEEP_ALIVE (frees RAM/VRAM between jobs)"
if is_macos; then
    launchctl setenv OLLAMA_KEEP_ALIVE "$KEEP_ALIVE" || true
    if have brew; then
        if brew services list 2>/dev/null | grep -q "ollama.*started"; then
            echo "→ Restarting Ollama (brew services) to pick up env vars..."
            brew services restart ollama
        else
            echo "→ Starting Ollama (brew services)..."
            brew services start ollama || true
        fi
    else
        echo "  ⚠ Homebrew not found — run 'ollama serve' manually in another terminal."
    fi
else
    # Linux: prefer systemd (the official installer registers an ollama unit).
    if have systemctl && systemctl list-unit-files 2>/dev/null | grep -q '^ollama\.service'; then
        echo "→ Configuring systemd drop-in for OLLAMA_KEEP_ALIVE..."
        if sudo -n true 2>/dev/null || [ "$(id -u)" = "0" ]; then
            sudo mkdir -p /etc/systemd/system/ollama.service.d
            printf '[Service]\nEnvironment="OLLAMA_KEEP_ALIVE=%s"\n' "$KEEP_ALIVE" \
                | sudo tee /etc/systemd/system/ollama.service.d/keepalive.conf >/dev/null
            sudo systemctl daemon-reload
            sudo systemctl enable --now ollama
            sudo systemctl restart ollama
        else
            echo "  ⚠ No passwordless sudo — starting/enabling ollama without the drop-in."
            sudo systemctl enable --now ollama 2>/dev/null || systemctl --user enable --now ollama 2>/dev/null || true
        fi
    else
        # No systemd (containers, minimal images): background the server.
        if ! curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then
            echo "→ Starting 'ollama serve' in the background (no systemd detected)..."
            OLLAMA_KEEP_ALIVE="$KEEP_ALIVE" nohup ollama serve >/tmp/ollama.log 2>&1 &
        fi
    fi
fi

# ── 3. Wait for the Ollama API ────────────────────────────────
echo "→ Waiting for Ollama API on :11434..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then
        echo "✓ Ollama responding"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "✗ Ollama didn't respond in 30s."
        if is_macos; then echo "  Try: brew services restart ollama"; else echo "  Try: sudo systemctl restart ollama   (or: ollama serve)"; fi
        exit 1
    fi
    sleep 1
done

# ── 4. Pull the model ─────────────────────────────────────────
if ollama list 2>/dev/null | grep -q "$MODEL"; then
    echo "✓ $MODEL already pulled"
else
    echo "→ Pulling $MODEL (large; takes a few minutes)..."
    ollama pull "$MODEL"
fi

# ── 5. Install Hermes Agent ───────────────────────────────────
locate_hermes() {
    for candidate in "$HOME/.local/bin" "/opt/homebrew/bin" "/usr/local/bin" "/usr/bin"; do
        if [ -x "$candidate/hermes" ]; then
            export PATH="$candidate:$PATH"
            return 0
        fi
    done
    have hermes
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
hermes config set provider ollama                 || true
hermes config set model "$MODEL"                  || true
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
