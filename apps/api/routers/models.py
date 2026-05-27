"""Base model catalogue (curated list — Hermes will expand this in Phase 2)."""
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
        hf_id="mlx-community/gemma-3n-E2B-it-bf16",
        label="Gemma 3n E2B (instruct, bf16)",
        family="gemma",
        size_params="~2.3B effective",
        recommended_method="lora",
        notes="Default for Phase 1. Fast on M3 Max. Gemma 4 E2B path will replace this when MLX-LM adds support.",
    ),
    BaseModelInfo(
        hf_id="mlx-community/gemma-3n-E4B-it-bf16",
        label="Gemma 3n E4B (instruct, bf16)",
        family="gemma",
        size_params="~4.5B effective",
        recommended_method="lora",
        notes="Better quality; ~2× memory of E2B. Comfortable on 36GB M3 Max.",
    ),
    BaseModelInfo(
        hf_id="mlx-community/Qwen2.5-3B-Instruct-4bit",
        label="Qwen 2.5 3B Instruct (4-bit)",
        family="qwen",
        size_params="3B",
        recommended_method="lora",
        notes="Pre-quantized → QLoRA automatically. Rock-solid on MLX. Fastest iteration.",
    ),
    BaseModelInfo(
        hf_id="mlx-community/Llama-3.2-3B-Instruct-4bit",
        label="Llama 3.2 3B Instruct (4-bit)",
        family="llama",
        size_params="3B",
        recommended_method="lora",
        notes="Strong general-purpose baseline.",
    ),
]


@router.get("", response_model=list[BaseModelInfo])
def list_models() -> list[BaseModelInfo]:
    return CATALOG
