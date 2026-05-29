"""Base model catalogue."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class BaseModelInfo(BaseModel):
    hf_id: str
    label: str
    family: str
    size_params: str
    recommended_method: str
    notes: str


CATALOG: list[BaseModelInfo] = [
    BaseModelInfo(
        hf_id="mlx-community/Qwen2.5-3B-Instruct-4bit",
        label="Qwen 2.5 3B Instruct (4-bit)",
        family="qwen",
        size_params="3B",
        recommended_method="lora",
        notes="Default. Pre-quantized → QLoRA. Works cleanly on mlx-lm 0.31+.",
    ),
    BaseModelInfo(
        hf_id="mlx-community/Llama-3.2-3B-Instruct-4bit",
        label="Llama 3.2 3B Instruct (4-bit)",
        family="llama",
        size_params="3B",
        recommended_method="lora",
        notes="Strong general-purpose baseline.",
    ),
    BaseModelInfo(
        hf_id="mlx-community/Qwen2.5-7B-Instruct-4bit",
        label="Qwen 2.5 7B Instruct (4-bit)",
        family="qwen",
        size_params="7B",
        recommended_method="lora",
        notes="Larger, slower. Comfortable on 36GB M3 Max.",
    ),
    BaseModelInfo(
        hf_id="mlx-community/gemma-3n-E2B-it-bf16",
        label="Gemma 3n E2B (BROKEN on mlx-lm 0.31.3)",
        family="gemma",
        size_params="~2.3B effective",
        recommended_method="lora",
        notes="⚠ KeyError in sanitize() — wait for mlx-lm fix or use a different model.",
    ),
]


@router.get("", response_model=list[BaseModelInfo])
def list_models() -> list[BaseModelInfo]:
    return CATALOG
