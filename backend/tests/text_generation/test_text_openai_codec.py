from haoai_backend.generation_providers.text.openai_codec import (
    OpenAITextStreamCodec,
    OpenAITextStreamEvent,
)


def _events(
    codec: OpenAITextStreamCodec,
    chunks: tuple[bytes, ...],
) -> list[OpenAITextStreamEvent]:
    emitted: list[OpenAITextStreamEvent] = []
    for chunk in chunks:
        emitted.extend(codec.feed(chunk))
    return emitted


def test_parses_fragmented_utf8_sse_and_bare_json_through_done_marker() -> None:
    body = (
        b": keepalive\n"
        b"event: completion\n"
        b'data: {"choices":[{"delta":{"content":"\xe4\xbd\xa0"}}]}\r\n'
        b'{"choices":[{"message":{"content":"\xe5\xa5\xbd"}}]}\n'
        b"data: [DONE]\n"
        b'data: {"choices":[{"delta":{"content":"after done"}}]}\n'
    )
    split_at = body.index(b"\xe4\xbd\xa0") + 1
    codec = OpenAITextStreamCodec(url="https://provider.example/v1/chat/completions")

    chunks = (body[:split_at], body[split_at : split_at + 1], body[split_at + 1 :])
    emitted = _events(codec, chunks)
    emitted.extend(codec.finalize())

    assert [(event.kind, event.text) for event in emitted] == [
        ("content", "你"),
        ("content", "好"),
        ("content", "after done"),
    ]
    assert codec.finalize() == ()


def test_parses_anthropic_delta_text_and_non_stream_message_content() -> None:
    codec = OpenAITextStreamCodec()
    emitted = _events(
        codec,
        (
            b'data: {"type":"content_block_delta","delta":{"text":"Claude"}}\n',
            b'{"choices":[{"message":{"content":" fallback"}}]}\n',
        ),
    )
    emitted.extend(codec.finalize())

    assert [(event.kind, event.text) for event in emitted] == [
        ("content", "Claude"),
        ("content", " fallback"),
    ]


def test_reasoning_is_a_distinct_event_and_only_reasoning_falls_back_at_eof() -> None:
    codec = OpenAITextStreamCodec()
    emitted = _events(
        codec,
        (
            b'data: {"choices":[{"delta":{"reasoning_content":"\xe6\x8e\xa8"}}]}\n',
            b'data: {"choices":[{"delta":{"reasoning_content":"\xe7\x90\x86"}}]}',
        ),
    )

    assert [(event.kind, event.text) for event in emitted] == [
        ("reasoning", "推"),
    ]

    emitted.extend(codec.finalize())
    assert [(event.kind, event.text) for event in emitted] == [
        ("reasoning", "推"),
        ("reasoning", "理"),
        ("content", "推理"),
    ]
    assert codec.finalize() == ()


def test_reasoning_is_never_added_to_content_when_any_content_was_returned() -> None:
    codec = OpenAITextStreamCodec()
    emitted = _events(
        codec,
        (
            b'data: {"choices":[{"delta":{"reasoning_content":"private"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"public"}}]}\n',
            b'data: {"choices":[{"delta":{"reasoning_content":"also private"}}]}\n',
        ),
    )
    emitted.extend(codec.finalize())

    assert [(event.kind, event.text) for event in emitted] == [
        ("reasoning", "private"),
        ("content", "public"),
        ("reasoning", "also private"),
    ]


def test_http_200_error_without_content_becomes_terminal_error_at_eof() -> None:
    codec = OpenAITextStreamCodec(url="https://provider.example/chat/completions")
    observed = codec.feed(b'data: {"error":{"message":"quota exhausted"}}\n')

    assert [(event.kind, event.message) for event in observed] == [
        ("upstream_error", "quota exhausted"),
    ]

    terminal = codec.finalize()
    assert len(terminal) == 1
    assert terminal[0].kind == "error"
    assert "quota exhausted" in (terminal[0].message or "")
    assert terminal[0].detail is None


def test_http_200_error_after_content_remains_an_observation_not_a_terminal_failure() -> None:
    codec = OpenAITextStreamCodec()
    emitted = _events(
        codec,
        (
            b'data: {"choices":[{"delta":{"content":"already delivered"}}]}\n',
            b'data: {"error":{"message":"late provider warning"}}\n',
        ),
    )
    terminal = codec.finalize()

    assert [(event.kind, event.text, event.message) for event in emitted] == [
        ("content", "already delivered", None),
        ("upstream_error", "", "late provider warning"),
    ]
    assert terminal == ()


def test_non_200_preserves_full_error_detail_separately_from_bounded_message() -> None:
    body = ("provider failure " + "x" * 600).encode("utf-8")
    codec = OpenAITextStreamCodec(
        status_code=503,
        url="https://provider.example/chat/completions",
    )
    assert codec.feed(body[:19]) == ()
    assert codec.feed(body[19:]) == ()

    terminal = codec.finalize()
    assert len(terminal) == 1
    event = terminal[0]
    assert event.kind == "error"
    assert event.status_code == 503
    assert event.detail == body.decode("utf-8")
    assert event.message == (
        "上游返回 HTTP 503（https://provider.example/chat/completions）："
        + body.decode("utf-8")[:500]
    )


def test_malformed_json_and_raw_html_produce_a_bounded_raw_snippet_error() -> None:
    codec = OpenAITextStreamCodec(url="https://provider.example/chat/completions")
    codec.feed(b"data: {not-json}\n")
    codec.feed(b"<!doctype html>" + b"x" * 600)

    terminal = codec.finalize()
    assert len(terminal) == 1
    event = terminal[0]
    assert event.kind == "error"
    assert "data: {not-json}" in (event.message or "")
    assert "<!doctype html>" in (event.message or "")
    raw_snippet = (event.message or "").split("上游原始返回片段：", 1)[1]
    assert len(raw_snippet) == 500


def test_eof_flushes_unterminated_line_and_ignores_invalid_utf8_bytes() -> None:
    codec = OpenAITextStreamCodec()
    line = b'data: {"choices":[{"delta":{"content":"A\xffB"}}]}'
    assert codec.feed(line[:23]) == ()
    emitted = codec.feed(line[23:])
    assert emitted == ()

    terminal = codec.finalize()
    assert [(event.kind, event.text) for event in terminal] == [("content", "AB")]
    assert codec.finalize() == ()


def test_done_markers_are_ignored_until_eof_and_later_content_is_read() -> None:
    codec = OpenAITextStreamCodec()
    emitted = _events(
        codec,
        (
            b"data: [DONE]\n",
            b'data: {"choices":[{"delta":{"content":"before"}}]}\n',
            b"data: [DONE]\n",
            b"data: [DONE]\n",
            b'data: {"choices":[{"delta":{"content":"between"}}]}\n',
            b"data: DO",
            b"NE\n",
            b"data: DONE\n",
            b'data: {"choices":[{"delta":{"content":"after"}}]}\n',
            b"data: [DONE]\n",
        ),
    )
    emitted.extend(codec.finalize())

    assert [(event.kind, event.text) for event in emitted] == [
        ("content", "before"),
        ("content", "between"),
        ("content", "after"),
    ]
    assert codec.feed(b'data: {"choices":[{"delta":{"content":"late"}}]}\n') == ()
    assert codec.finalize() == ()
