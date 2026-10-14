"""Pure text-generation provider protocol codecs."""

from .openai_codec import OpenAITextStreamCodec, OpenAITextStreamEvent

__all__ = ["OpenAITextStreamCodec", "OpenAITextStreamEvent"]
