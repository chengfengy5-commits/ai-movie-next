"""Request models kept local to the media upload routes."""

from __future__ import annotations

from pydantic import BaseModel


class RetryOssRequest(BaseModel):
    url: str
