"""Dataset helpers shared by training backends (Phase Q).

Moved verbatim from ``backends/mlx.py`` so the CUDA backend can reuse the
same chat-vs-text detection (PEFT/TRL's completion masking needs the same
signal as MLX-LM's ``mask_prompt``).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("trainer.dataset_utils")


def count_jsonl(path: Path) -> int:
    """Count non-empty lines in a JSONL file."""
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def detect_dataset_format(dataset_dir: Path) -> str:
    """Inspect train.jsonl's first non-empty row and return ``"chat"`` or ``"text"``.

    Supported row shapes:
      • chat:  ``{"messages": [{"role": ..., "content": ...}, ...]}``
      • prompt+completion: ``{"prompt": "...", "completion": "..."}``  (also chat-like)
      • text:  ``{"text": "..."}``

    Prompt masking (MLX ``mask_prompt`` / TRL assistant-only loss) only
    applies to chat/completion formats.
    """
    train_path = dataset_dir / "train.jsonl"
    if not train_path.exists():
        return "text"  # caller will fail later with a clearer error
    try:
        with train_path.open("r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                obj = json.loads(s)
                if not isinstance(obj, dict):
                    return "text"
                if "messages" in obj or ("prompt" in obj and "completion" in obj):
                    return "chat"
                return "text"
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Could not detect dataset format (%s) — defaulting to text", e)
    return "text"
