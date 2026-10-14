"""Pure parser for OpenAI-compatible text completion streams.

The codec consumes response bytes only. It does not make HTTP requests, read
credentials, or decide how downstream callers should present provider events.
"""

from __future__ import annotations

import codecs
import json
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class OpenAITextStreamEvent:
    """A parsed provider event, including fields hidden by the public stream."""

    kind: Literal["content", "reasoning", "upstream_error", "error"]
    text: str = ""
    message: str | None = None
    detail: str | None = None
    status_code: int | None = None


class OpenAITextStreamCodec:
    """Incrementally parse OpenAI-compatible SSE or raw JSON response bytes.

    feed emits content, reasoning, and HTTP-200 error observations as soon as
    complete lines arrive. finalize flushes an unterminated final line and emits
    either the legacy reasoning-only content fallback or a terminal no-content
    error. An HTTP error is emitted at finalization with its full body in detail
    and a bounded body excerpt in message.

    [DONE] and DONE are ignored markers. EOF finalization is idempotent, and
    further bytes are ignored.
    """

    def __init__(self, *, status_code: int = 200, url: str = "") -> None:
        self._status_code = status_code
        self._url = url
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
        self._line_buffer = ""
        self._http_error_body: list[str] = []
        self._raw_snippets: list[str] = []
        self._reasoning_parts: list[str] = []
        self._upstream_error = ""
        self._has_content = False
        self._finished = False

    def feed(self, chunk: bytes) -> tuple[OpenAITextStreamEvent, ...]:
        """Consume one raw response chunk and return newly parsed events."""

        if self._finished:
            return ()
        if not isinstance(chunk, bytes):
            raise TypeError("stream chunks must be bytes")

        decoded = self._decoder.decode(chunk, final=False)
        if self._status_code != 200:
            if decoded:
                self._http_error_body.append(decoded)
            return ()

        self._line_buffer += decoded
        return tuple(self._consume_complete_lines())

    def finalize(self) -> tuple[OpenAITextStreamEvent, ...]:
        """Flush EOF once and emit the appropriate terminal result, if any."""

        if self._finished:
            return ()

        decoded_tail = self._decoder.decode(b"", final=True)
        if self._status_code != 200:
            if decoded_tail:
                self._http_error_body.append(decoded_tail)
            return tuple(self._finish())

        self._line_buffer += decoded_tail
        events = self._consume_complete_lines()
        if self._line_buffer:
            final_line = self._line_buffer
            self._line_buffer = ""
            events.extend(self._consume_line(final_line))

        events.extend(self._finish())
        return tuple(events)

    def _consume_complete_lines(self) -> list[OpenAITextStreamEvent]:
        events: list[OpenAITextStreamEvent] = []
        while "\n" in self._line_buffer:
            line, self._line_buffer = self._line_buffer.split("\n", 1)
            events.extend(self._consume_line(line))
        return events

    def _consume_line(self, line: str) -> list[OpenAITextStreamEvent]:
        raw = line.strip()
        if not raw or raw.startswith(":") or raw.startswith("event:"):
            return []

        self._remember_raw(raw)
        data = raw[5:].strip() if raw.startswith("data:") else raw
        if data in ("[DONE]", "DONE"):
            return []

        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, dict):
            return []

        error = payload.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else str(error)
            self._upstream_error = str(message) if message else "上游返回错误"
            return [
                OpenAITextStreamEvent(
                    kind="upstream_error",
                    message=self._upstream_error,
                )
            ]

        if not payload.get("choices") and isinstance(payload.get("message"), str):
            self._upstream_error = payload["message"][:300]
            return [
                OpenAITextStreamEvent(
                    kind="upstream_error",
                    message=self._upstream_error,
                )
            ]

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        for choice in payload.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                delta = {}
            message = choice.get("message")
            if not isinstance(message, dict):
                message = {}
            text_parts.extend(
                self._truthy_text(value)
                for value in (delta.get("content"), message.get("content"))
            )
            reasoning_parts.extend(
                self._truthy_text(value)
                for value in (
                    delta.get("reasoning_content"),
                    message.get("reasoning_content"),
                )
            )

        text = "".join(text_parts)
        reasoning = "".join(reasoning_parts)
        if not text and payload.get("type") == "content_block_delta":
            delta = payload.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("text"), str):
                text = delta["text"]

        if text:
            self._has_content = True
            return [OpenAITextStreamEvent(kind="content", text=text)]
        if reasoning:
            self._reasoning_parts.append(reasoning)
            return [OpenAITextStreamEvent(kind="reasoning", text=reasoning)]
        return []

    def _finish(self) -> list[OpenAITextStreamEvent]:
        self._finished = True
        if self._status_code != 200:
            detail = "".join(self._http_error_body)
            excerpt = detail[:500] or "(空响应体)"
            return [
                OpenAITextStreamEvent(
                    kind="error",
                    message=f"上游返回 HTTP {self._status_code}（{self._url}）：{excerpt}",
                    detail=detail,
                    status_code=self._status_code,
                )
            ]

        if self._has_content:
            return []
        if self._reasoning_parts:
            return [
                OpenAITextStreamEvent(
                    kind="content",
                    text="".join(self._reasoning_parts),
                )
            ]

        snippet = "\n".join(self._raw_snippets)[:500]
        reason = self._upstream_error or "上游未返回任何正文内容"
        message = f"{reason}（接口：{self._url}）"
        if snippet:
            message += f"\n上游原始返回片段：{snippet}"
        else:
            message += "（上游响应体为空）"
        return [OpenAITextStreamEvent(kind="error", message=message)]

    def _remember_raw(self, text: str) -> None:
        if text and len(self._raw_snippets) < 20:
            self._raw_snippets.append(text[:500])

    @staticmethod
    def _truthy_text(value: object) -> str:
        return value if isinstance(value, str) and value else ""
