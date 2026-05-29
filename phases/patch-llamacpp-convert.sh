#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Homebrew's llama.cpp package installs only compiled binaries.
# convert_hf_to_gguf.py is a Python script that lives in the source repo only.
#
# This script:
#   1. Downloads convert_hf_to_gguf.py (+ its helpers) from the exact commit
#      that matches your installed llama.cpp version
#   2. Installs its Python deps into the project venv
#   3. Updates the Makefile check and pipeline.py to find it at the new path
#   4. Adds a 'scripts/convert_hf_to_gguf.py' so it's self-contained in the repo
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

if [ ! -f "pyproject.toml" ] || [ ! -d "packages/exporter" ]; then
    echo "✗ Run from project root (after Phase 4 is applied)."
    exit 1
fi

mkdir -p scripts/llama_cpp

echo "→ Detecting installed llama.cpp version..."
LLAMA_VER=$(brew info --json llama.cpp 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['versions']['stable'])" 2>/dev/null \
    || echo "")

if [ -z "$LLAMA_VER" ]; then
    echo "  Could not detect version via brew — fetching latest from main"
    LLAMA_VER="master"
    BRANCH="master"
else
    echo "  Detected: llama.cpp $LLAMA_VER"
    # Homebrew versions are like "b5123"; the Git tag is "b5123"
    BRANCH="b${LLAMA_VER#b}"   # ensure 'b' prefix
fi

BASE_URL="https://raw.githubusercontent.com/ggml-org/llama.cpp/${BRANCH}"

echo "→ Downloading convert_hf_to_gguf.py (branch/tag: $BRANCH)..."
curl -fsSL "${BASE_URL}/convert_hf_to_gguf.py" -o scripts/llama_cpp/convert_hf_to_gguf.py
echo "  ✓ convert_hf_to_gguf.py"

# The script imports from a gguf subpackage that lives alongside it
echo "→ Downloading gguf package helpers..."
mkdir -p scripts/llama_cpp/gguf

# Core gguf files needed by convert_hf_to_gguf.py
GGUF_FILES=(
    "__init__.py"
    "constants.py"
    "gguf_writer.py"
    "gguf_reader.py"
    "tensor_mapping.py"
    "vocab.py"
    "quants.py"
    "metadata.py"
    "lazy.py"
    "utility.py"
)

for f in "${GGUF_FILES[@]}"; do
    url="${BASE_URL}/gguf-py/gguf/${f}"
    dest="scripts/llama_cpp/gguf/${f}"
    if curl -fsSL "$url" -o "$dest" 2>/dev/null; then
        echo "  ✓ gguf/${f}"
    else
        echo "  ⚠ gguf/${f} not found at $url — may not be needed for this version"
        rm -f "$dest"
    fi
done

# ─────────────────────────────────────────────────────────────────────────────
# Install Python deps that convert_hf_to_gguf.py needs
# ─────────────────────────────────────────────────────────────────────────────
echo "→ Installing Python deps for convert_hf_to_gguf.py..."
uv pip install --quiet \
    sentencepiece \
    transformers \
    gguf \
    tiktoken \
    "numpy>=1.26" \
    || true  # non-fatal: some may already be in the venv

# ─────────────────────────────────────────────────────────────────────────────
# Patch packages/exporter/pipeline.py to find the script at its new location
# ─────────────────────────────────────────────────────────────────────────────
echo "→ Patching packages/exporter/pipeline.py to resolve convert_hf_to_gguf.py..."
python3 - <<'PYEOF'
from pathlib import Path

p = Path("packages/exporter/pipeline.py")
text = p.read_text()

# Replace _find_convert_script() with a new version that knows the local path
old_fn = '''def _find_convert_script() -> str | None:'''

if old_fn not in text:
    print("  ⚠ _find_convert_script not found — skipping patch")
    raise SystemExit(0)

# Find the full function and replace it
import re
# Match the function from 'def _find_convert_script' to the next 'def '
m = re.search(
    r'def _find_convert_script\(\).*?(?=\ndef |\Z)',
    text, re.DOTALL
)
if not m:
    print("  ⚠ Could not locate _find_convert_script body — skipping patch")
    raise SystemExit(0)

new_fn = '''def _find_convert_script() -> str | None:
    """Locate convert_hf_to_gguf.py.

    Priority:
      1. scripts/llama_cpp/convert_hf_to_gguf.py  (downloaded by patch_llamacpp_convert.sh)
      2. Homebrew share/libexec paths
      3. brew --prefix probe
    """
    import glob

    # 1. Project-local copy (most reliable)
    local = PROJECT_ROOT / "scripts" / "llama_cpp" / "convert_hf_to_gguf.py"
    if local.exists():
        return str(local)

    # 2. Homebrew standard paths
    candidates = [
        "/opt/homebrew/share/llama.cpp/convert_hf_to_gguf.py",
        "/opt/homebrew/libexec/llama.cpp/convert_hf_to_gguf.py",
        "/usr/local/share/llama.cpp/convert_hf_to_gguf.py",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c

    # 3. glob for versioned cellar paths
    for pattern in [
        "/opt/homebrew/Cellar/llama.cpp/*/share/llama.cpp/convert_hf_to_gguf.py",
        "/opt/homebrew/Cellar/llama.cpp/*/libexec/convert_hf_to_gguf.py",
    ]:
        for path in glob.glob(pattern):
            if os.path.exists(path):
                return path

    # 4. brew --prefix probe
    try:
        r = subprocess.run(
            ["brew", "--prefix", "llama.cpp"], capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            prefix = r.stdout.strip()
            for sub in ("share/llama.cpp", "libexec"):
                p = Path(prefix) / sub / "convert_hf_to_gguf.py"
                if p.exists():
                    return str(p)
    except Exception:  # noqa: BLE001
        pass

    return None

'''

text = text[:m.start()] + new_fn + text[m.end():]
p.write_text(text)
print("  ✓ _find_convert_script updated to check scripts/llama_cpp/ first")
PYEOF

# ─────────────────────────────────────────────────────────────────────────────
# Also fix the convert command in run_export_job to run the script with the
# correct Python and pass the gguf package path via PYTHONPATH
# ─────────────────────────────────────────────────────────────────────────────
python3 - <<'PYEOF'
from pathlib import Path
import re

p = Path("packages/exporter/pipeline.py")
text = p.read_text()

# Add PYTHONPATH so the local gguf package is found
old = '''    convert_cmd = [
        py, convert_script,
        str(fused_dir),
        "--outtype", "f16",
        "--outfile", str(f16_path),
    ]
    rc = _run_subprocess(convert_cmd, log_path, env=env)'''

new = '''    # Ensure the local gguf helpers are importable alongside convert_hf_to_gguf.py
    convert_env = dict(env)
    convert_script_dir = str(Path(convert_script).parent)
    pythonpath = convert_env.get("PYTHONPATH", "")
    convert_env["PYTHONPATH"] = (
        f"{convert_script_dir}:{pythonpath}" if pythonpath else convert_script_dir
    )
    convert_cmd = [
        py, convert_script,
        str(fused_dir),
        "--outtype", "f16",
        "--outfile", str(f16_path),
    ]
    rc = _run_subprocess(convert_cmd, log_path, env=convert_env)'''

if old in text:
    text = text.replace(old, new, 1)
    p.write_text(text)
    print("  ✓ PYTHONPATH set for convert_hf_to_gguf.py subprocess")
else:
    print("  ⚠ Could not find convert_cmd block — PYTHONPATH patch skipped")
PYEOF

# ─────────────────────────────────────────────────────────────────────────────
# Fix the Makefile check-llamacpp target to use the local script path
# ─────────────────────────────────────────────────────────────────────────────
python3 - <<'PYEOF'
from pathlib import Path
import re

mk = Path("Makefile")
text = mk.read_text()

old_target = r"""check-llamacpp: ## Verify llama.cpp (llama-quantize + convert_hf_to_gguf.py) is installed
	@if ! command -v llama-quantize >/dev/null 2>&1 && ! [ -x /opt/homebrew/bin/llama-quantize ]; then \
		echo "✗ llama-quantize not found. Install: brew install llama.cpp"; exit 1; \
	fi
	@PREFIX=$$(brew --prefix llama.cpp 2>/dev/null); \
	if [ -z "$$PREFIX" ]; then \
		echo "✗ llama.cpp not installed via Homebrew. Install: brew install llama.cpp"; exit 1; \
	fi; \
	if ! find "$$PREFIX" -name convert_hf_to_gguf.py 2>/dev/null | grep -q .; then \
		echo "✗ convert_hf_to_gguf.py not found under $$PREFIX"; exit 1; \
	fi
	@echo "✓ llama.cpp tools detected" """

new_target = r"""check-llamacpp: ## Verify llama.cpp + convert_hf_to_gguf.py are available
	@if ! command -v llama-quantize >/dev/null 2>&1 && ! [ -x /opt/homebrew/bin/llama-quantize ]; then \
		echo "✗ llama-quantize not found. Install: brew install llama.cpp"; exit 1; \
	fi
	@echo "✓ llama-quantize found"
	@if [ -f scripts/llama_cpp/convert_hf_to_gguf.py ]; then \
		echo "✓ convert_hf_to_gguf.py found (scripts/llama_cpp/)"; \
	elif find /opt/homebrew -name convert_hf_to_gguf.py 2>/dev/null | grep -q .; then \
		echo "✓ convert_hf_to_gguf.py found (homebrew)"; \
	else \
		echo "✗ convert_hf_to_gguf.py not found."; \
		echo "  Run: chmod +x patch_llamacpp_convert.sh && ./patch_llamacpp_convert.sh"; \
		exit 1; \
	fi"""

if old_target in text:
    text = text.replace(old_target, new_target, 1)
    mk.write_text(text)
    print("  ✓ Makefile check-llamacpp updated")
else:
    # Try a softer match
    import re
    text = re.sub(
        r'check-llamacpp:.*?(?=\n[a-zA-Z])',
        new_target + '\n',
        text,
        count=1,
        flags=re.DOTALL,
    )
    mk.write_text(text)
    print("  ✓ Makefile check-llamacpp updated (via regex)")
PYEOF

# ─────────────────────────────────────────────────────────────────────────────
# Verify the result
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "→ Verifying..."
if [ -f "scripts/llama_cpp/convert_hf_to_gguf.py" ]; then
    echo "  ✓ scripts/llama_cpp/convert_hf_to_gguf.py exists ($(wc -l < scripts/llama_cpp/convert_hf_to_gguf.py) lines)"
else
    echo "  ✗ scripts/llama_cpp/convert_hf_to_gguf.py not found — the download may have failed"
    echo "    Try manually:"
    echo "    curl -fsSL https://raw.githubusercontent.com/ggml-org/llama.cpp/master/convert_hf_to_gguf.py -o scripts/llama_cpp/convert_hf_to_gguf.py"
fi

if command -v llama-quantize &>/dev/null || [ -x /opt/homebrew/bin/llama-quantize ]; then
    echo "  ✓ llama-quantize on PATH"
else
    echo "  ✗ llama-quantize not found — brew install llama.cpp"
fi

cat <<MSG

╔══════════════════════════════════════════════════════════════════════╗
║  ✓ llamacpp convert patch applied                                    ║
╚══════════════════════════════════════════════════════════════════════╝

What changed:
  • scripts/llama_cpp/convert_hf_to_gguf.py  ← downloaded from GitHub
  • scripts/llama_cpp/gguf/                  ← helper package
  • packages/exporter/pipeline.py            ← _find_convert_script checks local first
  • packages/exporter/pipeline.py            ← PYTHONPATH set for the subprocess
  • Makefile check-llamacpp                  ← checks local script path

Now:

  make check-llamacpp      # should pass
  make exporter            # should start the export worker

Then queue an export:
  - Open /runs in the UI
  - Click any completed run
  - Click "Export to GGUF →"
  - Watch /exports for progress
MSG
