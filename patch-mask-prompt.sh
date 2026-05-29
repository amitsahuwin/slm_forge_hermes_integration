#!/usr/bin/env bash
# Tells mlx_lm.lora to only compute loss on the assistant's tokens, not the
# whole prompt+response. This is the standard SFT setup; without it, val_loss
# is artificially inflated by ~2-3x.
set -euo pipefail

if [ ! -f "packages/trainer/runner.py" ]; then
    echo "✗ Run from project root."
    exit 1
fi

python3 - <<'PYEOF'
from pathlib import Path
p = Path("packages/trainer/runner.py")
text = p.read_text()

# Add mask_prompt: True to the YAML config dict in _write_yaml_config.
# We insert it right after "seed": run["seed"], inside the cfg dict.
needle = '"seed": run["seed"],\n    }'
replacement = '"seed": run["seed"],\n        "mask_prompt": True,  # loss only on assistant tokens (proper SFT)\n    }'

if "mask_prompt" in text:
    print("  ✓ mask_prompt already present — no change")
elif needle in text:
    text = text.replace(needle, replacement, 1)
    p.write_text(text)
    print("  ✓ Added mask_prompt=True to trainer YAML config")
else:
    # Fall back: try to find any plausible insertion spot
    import re
    m = re.search(r'"seed":\s*run\["seed"\],\s*\n(\s*)\}', text)
    if m:
        indent = m.group(1)
        text = text[:m.start()] + (
            f'"seed": run["seed"],\n{indent}"mask_prompt": True,  # loss only on assistant tokens\n{indent}}}'
        ) + text[m.end():]
        p.write_text(text)
        print("  ✓ Added mask_prompt=True (via fallback regex)")
    else:
        print("  ✗ Could not find insertion point. Add this line manually to the cfg dict in")
        print("    packages/trainer/runner.py → _write_yaml_config():")
        print('      "mask_prompt": True,')
        raise SystemExit(1)
PYEOF

cat <<MSG

╔══════════════════════════════════════════════════════════════════════╗
║  ✓ mask_prompt fix applied                                           ║
╚══════════════════════════════════════════════════════════════════════╝

Effect: future runs will compute loss ONLY on the assistant's response tokens
(not the user's prompt). Val_loss should drop from ~5 to ~1.5-3.0 range,
where you can actually see meaningful overfitting curves.

Restart the trainer (Ctrl-C then make trainer) so changes take effect.
Then proceed with Phase 4 bootstrap.
MSG
