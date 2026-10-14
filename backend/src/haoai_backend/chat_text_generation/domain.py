"""Pure legacy payload construction and stream redaction."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
import json
from typing import Any, Mapping, Sequence

from .ports import ChatTextModel


@dataclass(frozen=True, slots=True)
class ResolvedPrompt:
    system_prompt: str
    user_prompt: str


@dataclass(frozen=True, slots=True)
class PreparedStream:
    actor_id: str
    attempt_id: str
    model: ChatTextModel
    messages: tuple[Mapping[str, str], ...]
    enable_thinking: bool
    task_id: str | None
    message_id: str | None
    original_key: str
    message_chapter_id: str | None = None
    quoted_amount: int = 0
    disposition: str = "zero"
    actual_debited_amount: int = 0
    original_debit_log_id: str | None = None
    original_business_key: str | None = None
    task_type: str | None = None
    task_execution_generation: int | None = None
    task_claim_token: str | None = None


def stream_provenance(model: ChatTextModel, actor_id: str) -> str:
    payload = {
        "owner_id": "system" if model.is_system else actor_id,
        "provider": model.provider,
        "model_id": model.id,
    }
    return "model-ref:v1:" + json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )


def with_task(
    prepared: PreparedStream,
    task_id: str,
    *,
    task_type: str,
    execution_generation: int,
    claim_token: str | None,
) -> PreparedStream:
    return replace(
        prepared,
        task_id=task_id,
        task_type=task_type,
        task_execution_generation=execution_generation,
        task_claim_token=claim_token,
    )


def review_request_data(request: Any, model: ChatTextModel) -> str:
    return json.dumps(
        {
            "chapter_id": request.chapter_id,
            "frame_index": request.frame_index,
            "prompt_id": request.prompt_id or "",
            "model_id": model.id,
            "is_system": model.is_system,
            "cost_per_call": model.cost_per_call,
            "enable_thinking": request.enable_thinking,
            "error_reason": (request.error_reason or "")[:2000],
            "asset_context": (request.asset_context or "")[:8000],
            "prev_context": (request.prev_context or "")[:8000],
            "source_message_id": request.message_id or "",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def batch_request_data(request: Any, model: ChatTextModel) -> str:
    return json.dumps(
        {
            "mode": "batch_sequential_concurrent",
            "chapter_id": request.chapter_id,
            "frame_count": len(request.frames),
            "frames": [
                {
                    "index": frame.index,
                    "text": frame.text,
                    "asset_context": frame.asset_context,
                }
                for frame in request.frames
            ],
            "model_id": request.model_id,
            "prompt_id": request.prompt_id or "",
            "enable_thinking": request.enable_thinking,
            "is_system": model.is_system,
            "cost_per_call": model.cost_per_call,
            "concurrency": request.concurrency,
            "chapter_texts": request.chapter_texts or [],
            "chapter_background": "",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def redact_chunk(value: str, secret: str, *, final: bool = False) -> tuple[str, str]:
    """Replace complete keys and retain only a possible cross-chunk prefix."""
    if not secret:
        return value, ""
    masked = value.replace(secret, "[已隐藏]")
    if final:
        return masked, ""
    max_suffix = min(len(secret) - 1, len(masked))
    overlap = 0
    for size in range(max_suffix, 0, -1):
        if masked.endswith(secret[:size]):
            overlap = size
            break
    stable = masked[:-overlap] if overlap else masked
    return stable, masked[-overlap:] if overlap else ""


def redact_text(value: str, secret: str) -> str:
    result, pending = redact_chunk(value, secret)
    tail, _ = redact_chunk(pending, secret, final=True)
    return result + tail
