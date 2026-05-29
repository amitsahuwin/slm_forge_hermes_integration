"""Convert raw row dicts into mlx_lm.lora's chat-templated JSONL format.

Output schema (what mlx_lm.lora expects): each line is {"text": "..."}
containing the fully-templated prompt + response.
"""
from __future__ import annotations

from typing import Literal

ChatTemplate = Literal["gemma", "llama3", "qwen", "raw"]


def _gemma_template(user: str, model: str) -> str:
    return (
        f"<start_of_turn>user\n{user}<end_of_turn>\n"
        f"<start_of_turn>model\n{model}<end_of_turn>"
    )


def _llama3_template(user: str, model: str) -> str:
    return (
        f"<|begin_of_text|><|start_header_id|>user<|end_header_id|>\n\n"
        f"{user}<|eot_id|>"
        f"<|start_header_id|>assistant<|end_header_id|>\n\n"
        f"{model}<|eot_id|>"
    )


def _qwen_template(user: str, model: str) -> str:
    return (
        f"<|im_start|>user\n{user}<|im_end|>\n"
        f"<|im_start|>assistant\n{model}<|im_end|>"
    )


def format_row(
    row: dict,
    *,
    prompt_field: str,
    response_field: str,
    template: ChatTemplate = "qwen",
    system_prompt: str | None = None,
) -> dict[str, str] | None:
    """Convert one source row into mlx_lm.lora's text format.

    Returns None if required fields are missing or empty.
    """
    if prompt_field not in row or response_field not in row:
        return None
    user = str(row[prompt_field]).strip()
    model = str(row[response_field]).strip()
    if not user or not model:
        return None

    if system_prompt:
        user = f"{system_prompt}\n\n{user}"

    if template == "gemma":
        text = _gemma_template(user, model)
    elif template == "llama3":
        text = _llama3_template(user, model)
    elif template == "qwen":
        text = _qwen_template(user, model)
    else:  # raw
        text = f"{user}\n\n{model}"

    return {"text": text}


def auto_detect_template(base_model: str) -> ChatTemplate:
    """Guess the right chat template from the base model HF id."""
    m = base_model.lower()
    if "qwen" in m:
        return "qwen"
    if "llama" in m:
        return "llama3"
    if "gemma" in m:
        return "gemma"
    return "raw"
